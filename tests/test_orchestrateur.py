import asyncio
import json
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.base import Agent, Decision, MessageIntent, Perception
from agents.factices import population_factice
from agents.roles import Palier, Role
from bus import events as ev
from sim import orchestrateur as orch_module
from sim.config import Config, load_config
from sim.domaine import HEURE_DECISION, Action, Canal, Sens, Zone, debut_du_jour
from sim.horloge import SimClock, ordre_paliers
from sim.orchestrateur import Orchestrateur
from sim.run import RunInfo, agent_rng, new_run, write_manifest

DEFAULT = Path(__file__).parent.parent / "configs" / "default.yaml"


class Recorder:
    """Publisher en mémoire : remplace Kafka dans les tests unitaires."""

    def __init__(self) -> None:
        self.events: list[ev.Envelope] = []

    async def publish(self, event: ev.Envelope) -> None:
        ev.decode(ev.encode(event))  # tout ce qui est publié respecte le contrat
        self.events.append(event)

    def of(self, cls: type[ev.Envelope]) -> list:
        return [e for e in self.events if isinstance(e, cls)]


class AgentScripte(Agent):
    """Agent de test dont la décision est donnée par une fonction."""

    def __init__(
        self,
        agent_id: str,
        role: Role,
        script: Callable[["AgentScripte", Perception], Decision],
        zone: Zone = Zone.PLAGE,
        toujours: bool = False,
        latence_s: float = 0.0,
    ) -> None:
        super().__init__(agent_id, role, zone, random.Random(0))
        self.script = script
        self.toujours = toujours
        self.latence_s = latence_s
        self.perceptions: list[Perception] = []

    def doit_decider(self, perception: Perception) -> bool:
        return self.toujours or super().doit_decider(perception)

    async def decider(self, perception: Perception) -> Decision:
        self.perceptions.append(perception)
        if self.latence_s:
            await asyncio.sleep(self.latence_s)
        return self.script(self, perception)


def decision(agent: Agent, sens: Sens = Sens.BANAL, **kwargs) -> Decision:
    llm = agent.palier is not None
    return Decision(
        action=Action.IGNORER,
        sens=sens,
        justification="test",
        porteur=False,
        moteur=ev.Moteur.LLM if llm else ev.Moteur.REGLE,
        palier=agent.palier,
        appel_llm=ev.LlmCall(modele="test", tokens_entree=1, tokens_sortie=1, duree_s=0)
        if llm
        else None,
        **kwargs,
    )


def simple(agent: AgentScripte, perception: Perception) -> Decision:
    return decision(agent)


def config(**changes) -> Config:
    base = load_config(DEFAULT)
    data = base.model_dump(mode="json")
    for section, values in changes.items():
        data[section].update(values)
    return Config.model_validate(data)


RUN = RunInfo("run_test", 1, "test", datetime(2026, 10, 17, tzinfo=UTC))


def orchestrateur(agents: list[Agent], cfg: Config | None = None) -> tuple[Orchestrateur, Recorder]:
    recorder = Recorder()
    return Orchestrateur(cfg or config(), RUN, agents, [], recorder), recorder


# --- horloge et identité du run -------------------------------------------------------------


def test_clock_moves_forward_only() -> None:
    clock = SimClock(duree_jours=3)
    assert clock.commencer_jour(1) == 0
    assert clock.heure_decision() == HEURE_DECISION
    assert clock.commencer_jour(2) == debut_du_jour(2)
    with pytest.raises(ValueError, match="recule"):
        clock.commencer_jour(2)
    with pytest.raises(ValueError, match="hors"):
        clock.commencer_jour(4)


def test_palier_order_alternates() -> None:
    assert ordre_paliers(1) == [Palier.PETIT, Palier.MOYEN, Palier.GRAND]
    assert ordre_paliers(2) == list(reversed(ordre_paliers(1)))


def test_run_identity_and_manifest(tmp_path: Path) -> None:
    cfg = load_config(DEFAULT)
    run = new_run(cfg, "configs/Mon Scénario.yaml", datetime(2026, 10, 17, 9, 5, tzinfo=UTC))
    assert run.run_id == "20261017-090500-mon-sc-nario-s42"
    manifest = json.loads(write_manifest(run, cfg, tmp_path).read_text(encoding="utf-8"))
    assert manifest["seed"] == 42 and manifest["config"]["population"]["pharmacien"] == 6
    assert manifest["git"]["commit"]


def test_agent_rng_depends_only_on_seed_and_id() -> None:
    a = [agent_rng(42, "pharmacien_01").random() for _ in range(3)]
    assert a == [agent_rng(42, "pharmacien_01").random() for _ in range(3)]
    assert a != [agent_rng(42, "pharmacien_02").random() for _ in range(3)]
    assert a != [agent_rng(43, "pharmacien_01").random() for _ in range(3)]


# --- ordonnancement par palier ---------------------------------------------------------------


def _un_agent_par_palier() -> list[Agent]:
    return [
        AgentScripte("pharmacien_01", Role.PHARMACIEN, simple, toujours=True),
        AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, simple, toujours=True),
        AgentScripte("sceptique_01", Role.SCEPTIQUE, simple, toujours=True),
        AgentScripte("vendeur_01", Role.VENDEUR, simple, toujours=True),
    ]


def test_alternating_order_halves_tier_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, _ = orchestrateur(_un_agent_par_palier())
    bilan = asyncio.run(orch.executer(jours=6))
    assert bilan.bascules_palier == 6  # une bascule par jour
    assert bilan.sequence_paliers[:6] == ["petit", "moyen", "grand", "grand", "moyen", "petit"]

    fixe = [Palier.PETIT, Palier.MOYEN, Palier.GRAND]
    monkeypatch.setattr(orch_module, "ordre_paliers", lambda jour: fixe)
    orch, _ = orchestrateur(_un_agent_par_palier())
    assert asyncio.run(orch.executer(jours=6)).bascules_palier == 11  # deux par jour sauf le 1er


def test_parallelism_limit_per_tier() -> None:
    actifs, pic = 0, 0

    class Compteur(AgentScripte):
        async def decider(self, perception: Perception) -> Decision:
            nonlocal actifs, pic
            actifs += 1
            pic = max(pic, actifs)
            await asyncio.sleep(0.01)
            actifs -= 1
            return decision(self)

    agents = [Compteur(f"sceptique_{i:02d}", Role.SCEPTIQUE, simple) for i in range(8)]
    orch, _ = orchestrateur(agents)
    asyncio.run(orch.executer(jours=1))
    assert pic == 2  # parallélisme du palier petit dans la configuration


def test_publication_order_does_not_depend_on_latency() -> None:
    agents = [
        AgentScripte("sceptique_01", Role.SCEPTIQUE, simple, latence_s=0.03),
        AgentScripte("sceptique_02", Role.SCEPTIQUE, simple, latence_s=0.0),
        AgentScripte("pharmacien_01", Role.PHARMACIEN, simple, latence_s=0.01),
    ]
    orch, rec = orchestrateur(agents)
    asyncio.run(orch.executer(jours=1))
    assert [e.source_agent_id for e in rec.of(ev.AgentDecision)] == sorted(
        a.agent_id for a in agents
    )


# --- temps, livraison et déclencheurs --------------------------------------------------------


def _emetteur(canal: Canal, destinataires: tuple[str, ...] = ()) -> Callable:
    def script(agent: AgentScripte, perception: Perception) -> Decision:
        if perception.jour == 1:
            return decision(agent, messages=(MessageIntent(canal, "banal", destinataires),))
        return decision(agent)

    return script


@pytest.mark.parametrize(("delai", "jour_lecture"), [(0, 2), (120, 2), (1439, 2), (1800, 3)])
def test_message_is_read_after_delivery_delay(delai: int, jour_lecture: int) -> None:
    script = _emetteur(Canal.DIRECT, ("sceptique_01",))
    emetteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, script, toujours=True)
    lecteur = AgentScripte("sceptique_01", Role.SCEPTIQUE, simple, toujours=True)
    cfg = config(simulation={"delais_livraison_min": {"direct": delai, "zone": 0, "reseaux": 0}})
    orch, rec = orchestrateur([emetteur, lecteur], cfg)
    asyncio.run(orch.executer(jours=4))

    message = rec.of(ev.AgentMessage)[0]
    assert message.payload.livraison == HEURE_DECISION + delai
    recu = [p.jour for p in lecteur.perceptions if p.messages]
    assert recu == [jour_lecture]


def test_same_day_messages_are_never_read_same_day() -> None:
    """Barrière synchrone : même un message de zone livré immédiatement attend le lendemain."""
    script = _emetteur(Canal.ZONE)
    emetteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, script, toujours=True)
    voisin = AgentScripte("sceptique_01", Role.SCEPTIQUE, simple, toujours=True)
    loin = AgentScripte("sceptique_02", Role.SCEPTIQUE, simple, zone=Zone.PHARMACIE, toujours=True)
    orch, _ = orchestrateur([emetteur, voisin, loin])
    asyncio.run(orch.executer(jours=2))
    assert [len(p.messages) for p in voisin.perceptions] == [0, 1]
    assert [len(p.messages) for p in loin.perceptions] == [0, 0]  # pas dans la zone


def test_unchanged_agent_keeps_its_decision_without_calling() -> None:
    calme = AgentScripte("sceptique_01", Role.SCEPTIQUE, simple)
    orch, rec = orchestrateur([calme])
    bilan = asyncio.run(orch.executer(jours=5))
    assert len(calme.perceptions) == 1  # seulement le premier jour
    assert bilan.decisions == 1 and bilan.decisions_conservees == 4
    assert len(rec.of(ev.AgentDecision)) == 1


def test_receiving_a_message_triggers_a_decision() -> None:
    script = _emetteur(Canal.DIRECT, ("sceptique_01",))
    emetteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, script)
    lecteur = AgentScripte("sceptique_01", Role.SCEPTIQUE, simple)
    orch, _ = orchestrateur([emetteur, lecteur])
    asyncio.run(orch.executer(jours=4))
    assert [p.jour for p in lecteur.perceptions] == [1, 2]


# --- cohérence temporelle et pannes ----------------------------------------------------------


def test_citing_unreceived_information_is_detected() -> None:
    fantome = uuid.uuid4()
    tricheur = AgentScripte(
        "sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a, caused_by=(fantome,))
    )
    orch, rec = orchestrateur([tricheur])
    bilan = asyncio.run(orch.executer(jours=1))
    assert bilan.violations_temporelles == 1
    incident = rec.of(ev.Incident)[0]
    assert incident.payload.nature is ev.NatureIncident.VIOLATION_TEMPORELLE
    assert incident.caused_by == (rec.of(ev.AgentDecision)[0].event_id,)


def test_citing_a_received_message_is_allowed() -> None:
    script = _emetteur(Canal.DIRECT, ("sceptique_01",))
    emetteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, script)

    def cite(agent: AgentScripte, perception: Perception) -> Decision:
        return decision(agent, caused_by=tuple(m.event_id for m in perception.messages))

    lecteur = AgentScripte("sceptique_01", Role.SCEPTIQUE, cite)
    orch, rec = orchestrateur([emetteur, lecteur])
    bilan = asyncio.run(orch.executer(jours=3))
    assert bilan.violations_temporelles == 0
    message_id = rec.of(ev.AgentMessage)[0].event_id
    assert any(message_id in d.caused_by for d in rec.of(ev.AgentDecision))


def test_failing_agent_does_not_stop_the_world() -> None:
    def panne(agent: AgentScripte, perception: Perception) -> Decision:
        if perception.jour == 2:
            raise TimeoutError("modèle muet")
        return decision(agent, sens=Sens.TENDANCE)

    fragile = AgentScripte("sceptique_01", Role.SCEPTIQUE, panne, toujours=True)
    orch, rec = orchestrateur([fragile])
    bilan = asyncio.run(orch.executer(jours=3))
    assert bilan.jours == 3 and bilan.incidents == 1
    assert rec.of(ev.Incident)[0].payload.nature is ev.NatureIncident.SANS_REPONSE
    assert fragile.sens is Sens.TENDANCE  # décision précédente conservée


def test_malformed_decision_publishes_nothing_but_an_incident() -> None:
    bavard = AgentScripte(
        "sceptique_01",
        Role.SCEPTIQUE,
        lambda a, p: decision(a, messages=(MessageIntent(Canal.ZONE, "x" * 600),)),
    )
    orch, rec = orchestrateur([bavard])
    asyncio.run(orch.executer(jours=1))
    assert not rec.of(ev.AgentDecision) and not rec.of(ev.AgentMessage)
    assert rec.of(ev.Incident)[0].payload.nature is ev.NatureIncident.SORTIE_INVALIDE


# --- reproductibilité et population factice --------------------------------------------------


def _trace(events: list[ev.Envelope]) -> list[tuple]:
    """Contenu d'un run, sans ce qui varie légitimement : identifiants et heure réelle."""
    return [
        (e.type, e.source_agent_id, e.sim_time_occurred, e.zone,
         e.payload.model_dump_json(exclude={"destinataires"}))
        for e in events
    ]  # fmt: skip


def _run_factice(seed: int) -> list[tuple]:
    cfg = config(run={"seed": seed})
    agents, acteurs = population_factice(cfg)
    recorder = Recorder()
    asyncio.run(Orchestrateur(cfg, RUN, agents, acteurs, recorder).executer(jours=4))
    return _trace(recorder.events)


def test_same_seed_same_run() -> None:
    assert _run_factice(42) == _run_factice(42)
    assert _run_factice(42) != _run_factice(7)


def test_same_seed_same_run_across_processes() -> None:
    """Deux processus distincts, avec un ordre des ensembles différent (PYTHONHASHSEED),
    doivent produire exactement le même run : rien ne doit dépendre de cet ordre."""
    import hashlib
    import os
    import subprocess
    import sys

    script = (
        "import hashlib; from tests.test_orchestrateur import _run_factice; "
        "print(hashlib.sha256(repr(_run_factice(42)).encode()).hexdigest())"
    )
    empreintes = set()
    for hash_seed in ("1", "2"):
        sortie = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            cwd=Path(__file__).parent.parent,
        )
        empreintes.add(sortie.stdout.strip())
    assert len(empreintes) == 1
    assert hashlib.sha256(b"").hexdigest() not in empreintes


def test_factice_population_follows_config() -> None:
    agents, acteurs = population_factice(load_config(DEFAULT))
    # Météo et calendrier du concurrent sont des acteurs du monde, pas des agents.
    assert len(agents) == 119 and [a.actor_id for a in acteurs] == [
        "meteo",
        "marque_concurrente_01",
    ]
    assert sum(a.palier is not None for a in agents) == 84
    assert len({a.agent_id for a in agents}) == 119
