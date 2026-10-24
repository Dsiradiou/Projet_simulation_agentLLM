"""Historique Parquet, rejeu sans LLM et requêtes DuckDB (étape 8, E3 et F5)."""

import asyncio
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiokafka import AIOKafkaConsumer, ConsumerRecord

from agents.agent_llm import AgentLLM
from agents.base import Agent, Decision, Perception
from agents.factices import population_factice
from agents.roles import Role
from bus import events as ev
from bus.events import topic_for
from bus.producer import ordre_publication
from bus.topics import Topic
from llm.routeur import Routeur
from replay.bilan import COMPARABLES, bilan_depuis, population_agents
from replay.ecrivain import EcrivainHistorique
from replay.historique import connexion, requete
from replay.rejeu import HistoriqueAbsent, charger, rejouer, verifier
from sim.config import Config, load_config
from sim.domaine import Zone
from sim.orchestrateur import Orchestrateur
from sim.run import RunInfo, executer_run, new_run, write_manifest
from sim.settings import load_settings
from tests.test_infra import _services_up
from tests.test_llm import ClientScripte, _json
from tests.test_orchestrateur import AgentScripte, Recorder, decision

DEFAULT = Path(__file__).parent.parent / "configs" / "default.yaml"


def _petite_config() -> Config:
    data = load_config(DEFAULT).model_dump(mode="json")
    data["population"] = {
        "pharmacien": 1,
        "habitue_soin": 2,
        "sceptique": 3,
        "vendeur": 2,
        "meteo": 1,
    }
    return Config.model_validate(data)


class AgentEnPanne(Agent):
    async def decider(self, perception: Perception) -> Decision:
        raise RuntimeError("panne simulée")


def _run_en_memoire(jours: int = 3) -> tuple[Config, list[ev.Envelope], dict]:
    """Run sans Kafka, avec de tout : factices, LLM scriptés (relance, repli), agent en panne,
    décision mal formée. Renvoie les événements dans l'ordre de publication et le bilan."""
    config = _petite_config()
    agents, acteurs = population_factice(config)
    client = ClientScripte(*([_json(sens="genial"), _json(), "x", "y"] * jours))
    routeur = Routeur(client, config.llm)
    agents += [
        AgentLLM(
            f"{r.value}_09", r, Zone.PLAGE, random.Random(0), routeur, config.monde, 42, [], 0.3
        )
        for r in (Role.HABITUE_SOIN, Role.SCEPTIQUE)
    ]
    agents.append(AgentEnPanne("vendeur_09", Role.VENDEUR, Zone.PLAGE, random.Random(0)))
    agents.append(
        AgentScripte(
            "vendeur_08",
            Role.VENDEUR,
            lambda a, p: decision(a, justification="x" * 600),  # trop long : sortie invalide
            toujours=True,
        )
    )
    recorder = Recorder()
    orch = Orchestrateur(config, new_run(config, DEFAULT), agents, acteurs, recorder)
    bilan = asyncio.run(orch.executer(jours))
    resume = {k: getattr(bilan, k) for k in COMPARABLES}
    return config, recorder.events, resume


def test_bilan_is_rebuilt_from_events_alone() -> None:
    config, events, live = _run_en_memoire()
    population = config.nb_agents - config.population[Role.METEO] + 4
    rebuilt = bilan_depuis(events, population)
    assert rebuilt == live
    assert live["replis"] and live["relances"] and live["incidents"]  # le run couvre ces cas
    assert live["decisions_conservees"] >= 0


def _record(event: ev.Envelope, seq: int, ts: int, offset: int) -> ConsumerRecord:
    return ConsumerRecord(
        topic=topic_for(event).value,
        partition=0,
        offset=offset,
        timestamp=ts,
        timestamp_type=0,
        key=event.run_id.encode(),
        value=ev.encode(event),
        checksum=None,
        serialized_key_size=0,
        serialized_value_size=0,
        headers=[("producteur", b"orchestrateur"), ("seq", str(seq).encode())],
    )


def _ecrire_historique(events: list[ev.Envelope], runs_dir: Path, run_id: str) -> None:
    """Écrit un historique comme le ferait l'écrivain, sans Kafka. Les messages arrivent topic
    par topic (comme d'un consommateur), et tous dans la même milliseconde : seul le numéro de
    publication permet de retrouver l'ordre."""
    ecrivain = EcrivainHistorique("inutilise:9092", run_id, runs_dir)
    ecrivain.dossier.mkdir(parents=True)
    records = [_record(e, i, ts=1_000, offset=i) for i, e in enumerate(events)]
    for record in sorted(records, key=lambda r: r.topic):
        ecrivain._recevoir(record)
        if len(ecrivain._tampon) == 10:
            ecrivain._ecrire()  # plusieurs fichiers, comme un long run
    ecrivain._ecrire()


def _avec_run_id(events: list[ev.Envelope], run_id: str) -> list[ev.Envelope]:
    return [e.model_copy(update={"run_id": run_id}) for e in events]


def test_parquet_history_replays_events_byte_for_byte(tmp_path: Path) -> None:
    _, events, _ = _run_en_memoire(2)
    events = _avec_run_id(events, "run_a")
    _ecrire_historique(events, tmp_path, "run_a")
    assert len(list((tmp_path / "run_a" / "evenements").glob("part-*.parquet"))) > 1
    rejoues = charger("run_a", tmp_path)
    assert [ev.encode(e) for e in rejoues] == [ev.encode(e) for e in events]


def test_replay_and_recorded_bilan_agree(tmp_path: Path) -> None:
    config, events, live = _run_en_memoire(2)
    run = RunInfo("run_b", 42, "test", datetime.now(UTC))
    write_manifest(run, config, tmp_path)
    # La population du manifeste ne connaît que la configuration : on n'y ajoute pas d'agents.
    events = [e for e in _avec_run_id(events, "run_b") if not e.source_agent_id.endswith("_09")]
    events = [e for e in events if not e.source_agent_id.endswith("_08")]
    manifeste = json.loads((tmp_path / "run_b" / "manifest.json").read_text(encoding="utf-8"))
    attendu = bilan_depuis(events, population_agents(manifeste))
    (tmp_path / "run_b" / "bilan.json").write_text(json.dumps(attendu), encoding="utf-8")
    _ecrire_historique(events, tmp_path, "run_b")
    assert verifier("run_b", charger("run_b", tmp_path), tmp_path) == {}
    # Un bilan altéré est détecté.
    (tmp_path / "run_b" / "bilan.json").write_text(
        json.dumps({**attendu, "decisions": attendu["decisions"] + 1}), encoding="utf-8"
    )
    assert "decisions" in verifier("run_b", charger("run_b", tmp_path), tmp_path)


def test_missing_history_is_reported(tmp_path: Path) -> None:
    with pytest.raises(HistoriqueAbsent):
        charger("inconnu", tmp_path)


def test_replay_speed_is_in_simulated_days_per_second() -> None:
    _, events, _ = _run_en_memoire(3)

    async def tout() -> float:
        debut = time.perf_counter()
        n = [e async for e in rejouer(events, vitesse=20)]
        assert len(n) == len(events)
        return time.perf_counter() - debut

    assert 0.09 <= asyncio.run(tout()) < 0.5  # 2 attentes de 1/20 s entre 3 jours


def test_publication_order_breaks_ties_within_a_millisecond() -> None:
    cles = [
        ordre_publication(1000, "orchestrateur", 10),
        ordre_publication(1000, "orchestrateur", 2),
    ]
    assert sorted(cles)[0][2] == 2
    assert ordre_publication(999, "orchestrateur", 50) < ordre_publication(1000, "orchestrateur", 0)


def test_duckdb_views_answer_reproducible_queries(tmp_path: Path) -> None:
    config, events, live = _run_en_memoire(2)
    for run_id in ("run_c", "run_d"):
        write_manifest(RunInfo(run_id, 42, "test", datetime.now(UTC)), config, tmp_path)
        _ecrire_historique(_avec_run_id(events, run_id), tmp_path, run_id)
    sens = requete("sens_par_jour", tmp_path)
    assert {r[0] for r in sens} == {"run_c", "run_d"}
    assert sum(r[3] for r in sens if r[0] == "run_c") == live["decisions"]
    cout = {(r[0], r[1]): r[2] for r in requete("cout_par_modele", tmp_path)}
    assert sum(v for (run, _), v in cout.items() if run == "run_c") == live["appels_llm"]
    incidents = {r[1]: r[2] for r in requete("incidents_par_nature", tmp_path) if r[0] == "run_c"}
    assert incidents == live["incidents_par_nature"]
    # Deux lectures donnent le même résultat (F5).
    assert requete("messages_par_palier", tmp_path) == requete("messages_par_palier", tmp_path)
    # Traçabilité (F6) : chaque incident de décision remonte à une décision de l'historique.
    con = connexion(tmp_path)
    orphelins = con.execute(
        "SELECT count(*) FROM incidents i WHERE len(i.caused_by) > 0 AND NOT EXISTS "
        "(SELECT 1 FROM evenements e WHERE e.event_id = i.caused_by[1])"
    ).fetchone()
    assert orphelins == (0,)


def test_duckdb_works_without_any_history(tmp_path: Path) -> None:
    assert requete("runs", tmp_path) == []
    assert requete("sens_par_jour", tmp_path) == []


# --- de bout en bout sur Kafka ---------------------------------------------------------------


@pytest.mark.integration
def test_run_is_recorded_from_kafka_and_replays_identically(tmp_path: Path) -> None:
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    config = _petite_config()
    run = RunInfo(f"test-rejeu-{datetime.now(UTC):%H%M%S%f}", 42, "test", datetime.now(UTC))
    write_manifest(run, config, tmp_path)

    async def scenario() -> tuple[dict, list[ev.Envelope]]:
        # Un lecteur indépendant voit ce que voit la console en direct.
        lecteur = AIOKafkaConsumer(
            *(t.value for t in Topic),
            bootstrap_servers=settings.kafka_bootstrap_servers,
            auto_offset_reset="latest",
        )
        await lecteur.start()
        await lecteur.getmany(timeout_ms=1000)
        agents, acteurs = population_factice(config)
        resume = await executer_run(config, run, agents, acteurs, settings, 3, runs_dir=tmp_path)
        lus: list[ev.Envelope] = []
        async with asyncio.timeout(20):
            # Les événements de l'orchestrateur et les indicateurs du service (étape 14).
            while len(lus) < resume["evenements_publies"] + resume["indicateurs_publies"]:
                lot = await lecteur.getmany(timeout_ms=500)
                lus += [
                    e
                    for records in lot.values()
                    for r in records
                    if (e := ev.decode(r.value)).run_id == run.run_id
                ]
        await lecteur.stop()
        return resume, lus

    resume, en_direct = asyncio.run(scenario())
    assert resume["evenements_enregistres"] == resume["evenements_publies"]
    assert resume["indicateurs_enregistres"] == resume["indicateurs_publies"] > 0
    rejoues = charger(run.run_id, tmp_path)
    assert {e.event_id for e in rejoues} == {e.event_id for e in en_direct}
    assert verifier(run.run_id, rejoues, tmp_path) == {}  # mêmes indicateurs (E3)
