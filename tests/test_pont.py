"""Pont FastAPI et contrôle du run depuis l'interface (étape 9 : D1, D2, D3)."""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.factices import population_factice
from agents.roles import Role
from bridge.app import Diffusion, Services, SuiviDirect, creer_app
from bridge.client import ligne
from bridge.vue import VueMonde
from bus import events as ev
from replay.bilan import bilan_depuis
from replay.rejeu import charger
from sim.domaine import Zone, debut_du_jour
from sim.orchestrateur import Orchestrateur
from sim.run import RunInfo, executer_run, new_run, write_manifest
from sim.settings import load_settings
from tests.test_infra import _services_up
from tests.test_orchestrateur import AgentScripte, Recorder, decision
from tests.test_rejeu import (
    DEFAULT,
    _avec_run_id,
    _ecrire_historique,
    _petite_config,
    _run_en_memoire,
)


def _injection(run_id: str, zone: Zone | None = None, minute: int = 600) -> ev.Injection:
    return ev.Injection(
        run_id=run_id,
        sim_time_occurred=minute,
        sim_time_processed=minute,
        source_agent_id="pont",
        zone=zone,
        payload=ev.InjectionPayload(nature="rupture_stock", description="Plus de stock ici."),
    )


def _commande(run_id: str, commande: str) -> ev.Command:
    return ev.Command(
        run_id=run_id,
        sim_time_occurred=0,
        sim_time_processed=0,
        source_agent_id="pont",
        payload=ev.CommandPayload(commande=commande),
    )


class ControleScripte:
    """Source de contrôle de test : la liste n°i est rendue au i-ème relevé."""

    def __init__(self, *releves: list) -> None:
        self.releves = list(releves)
        self.appels = 0

    async def relever(self) -> list:
        self.appels += 1
        return self.releves.pop(0) if self.releves else []


def _orchestrateur(controle: ControleScripte, agents=None) -> tuple[Orchestrateur, Recorder]:
    config = _petite_config()
    recorder = Recorder()
    agents = agents or [
        AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a), Zone.PLAGE),
        AgentScripte("sceptique_02", Role.SCEPTIQUE, lambda a, p: decision(a), Zone.PISCINE),
    ]
    run = new_run(config, DEFAULT)
    return Orchestrateur(config, run, agents, [], recorder, controle=controle), recorder


# --- contrôle du run par l'orchestrateur -----------------------------------------------------


def test_injection_becomes_a_disturbance_the_next_day_in_its_zone() -> None:
    controle = ControleScripte([], [])  # jour 1 : rien ; injection reçue avant le jour 2
    orch, recorder = _orchestrateur(controle)
    injection = _injection(orch.run.run_id, zone=Zone.PLAGE)
    controle.releves[1] = [injection]
    bilan = asyncio.run(orch.executer(3))

    (perturbation,) = recorder.of(ev.Disturbance)
    assert perturbation.caused_by == (injection.event_id,)  # traçable jusqu'à l'injection (F6)
    assert perturbation.zone == Zone.PLAGE
    assert perturbation.sim_time_occurred == debut_du_jour(2)  # demandée J1, effective J2 (C3)
    plage, piscine = orch.agents
    # Un agent ne perçoit (et n'est déclenché) que les jours où il décide : J1, puis J2 pour
    # l'agent de la plage, touché par la perturbation ; celui de la piscine n'est pas concerné.
    assert [len(p.evenements) for p in plage.perceptions] == [0, 1]
    assert [len(p.evenements) for p in piscine.perceptions] == [0]
    assert bilan.injections == 1
    assert bilan_depuis(recorder.events, 2)["injections"] == 1  # le rejeu la recompte


def test_pause_waits_for_resume_between_days() -> None:
    controle = ControleScripte([], [_commande("x", "pause")], [], [], [_commande("x", "reprise")])
    orch, recorder = _orchestrateur(controle)
    bilan = asyncio.run(orch.executer(3))
    assert bilan.jours == 3
    assert controle.appels == 6  # J1, J2 (pause), deux attentes, reprise, J3
    assert len(recorder.of(ev.ClockStep)) == 3


def test_stop_command_ends_the_run_cleanly() -> None:
    controle = ControleScripte([], [], [_commande("x", "arret")])
    orch, recorder = _orchestrateur(controle)
    bilan = asyncio.run(orch.executer(5))
    assert bilan.jours == 2
    assert [c.payload.jour for c in recorder.of(ev.ClockStep)] == [1, 2]


def test_stop_also_works_during_a_pause() -> None:
    controle = ControleScripte([_commande("x", "pause")], [], [_commande("x", "arret")])
    orch, _ = _orchestrateur(controle)
    assert asyncio.run(orch.executer(5)).jours == 0


# --- vue du monde et suivi du flux -----------------------------------------------------------


def test_world_view_is_built_from_events_alone() -> None:
    _, events, live = _run_en_memoire(2)
    vue = VueMonde()
    vue.reinitialiser(events[0].run_id)
    for event in events:
        vue.appliquer(event)
    assert vue.jour == 2
    nb_decisions = sum(len(f.decisions) for f in vue.agents.values())
    assert nb_decisions == live["decisions"]
    repli = vue.agents["sceptique_09"].detail()["derniere_decision"]
    assert repli["moteur"] == "repli" and repli["appel_llm"]["tentatives"] == 2
    directs = [m for f in vue.agents.values() for m in f.messages_directs_recus]
    assert all(m["canal"] == "direct" for m in directs)


def test_live_follow_switches_to_a_new_run_and_ignores_others() -> None:
    services = Services(etat=None, publier=None, vue=VueMonde(), diffusion=Diffusion())
    suivi = SuiviDirect.__new__(SuiviDirect)
    suivi.services = services
    file = services.diffusion.abonner()
    _, events, _ = _run_en_memoire(1)
    for e in _avec_run_id(events, "run_1")[:3]:
        suivi.recevoir(e, ev.encode(e).decode())
    autre = _avec_run_id(events, "run_x")[5]  # événement d'un autre run, hors début de run
    suivi.recevoir(autre, ev.encode(autre).decode())
    nouveau = _avec_run_id(events, "run_2")[0]  # horloge, jour 1 d'un nouveau run
    suivi.recevoir(nouveau, ev.encode(nouveau).decode())
    messages = [json.loads(file.get_nowait()) for _ in range(file.qsize())]
    assert [m["type"] for m in messages][0] == "pont.nouveau_run"
    assert {m.get("run_id") for m in messages} == {"run_1", "run_2"}
    assert services.vue.run_id == "run_2"


def test_slow_client_is_dropped_instead_of_blocking() -> None:
    diffusion = Diffusion()
    file = diffusion.abonner()
    for i in range(file.maxsize + 1):
        diffusion.diffuser(str(i))
    assert diffusion.clients == 0


# --- API REST et WebSocket, avec un état en mémoire ------------------------------------------


class EtatEnMemoire:
    def __init__(self, statut: str = "en_cours") -> None:
        self.courant = {"run_id": "run_t", "statut": statut, "jour": "2",
                        "minute": str(debut_du_jour(2) + 600)}  # fmt: skip
        self.fiches = {
            "sceptique_01": {"role": "sceptique", "palier": "petit", "sens": "banal",
                             "messages_recus": json.dumps([{"de": "x", "contenu": "salut"}])},
            "vendeur_01": {"role": "vendeur", "palier": "regles", "seuil": "0.420",
                           "justification": "Règle de seuil : 10% de porteurs."},
        }  # fmt: skip

    async def run_courant(self):
        return self.courant

    async def monde(self, run_id):
        return {"jour": "2", "minute": str(debut_du_jour(2) + 600), "meteo": ""}

    async def zones(self, run_id):
        return {"plage": {"presents": 1, "porteurs": 0}}

    async def agents(self, run_id):
        return self.fiches

    async def agent(self, run_id, agent_id):
        return self.fiches.get(agent_id, {})


def _client(statut: str = "en_cours", runs_dir: Path | None = None) -> tuple[TestClient, list]:
    publies: list[ev.Envelope] = []

    async def publier(event: ev.Envelope) -> None:
        publies.append(event)

    services = Services(EtatEnMemoire(statut), publier, VueMonde(), Diffusion())
    if runs_dir:
        services.runs_dir = runs_dir
    return TestClient(creer_app(services)), publies


def test_injection_request_is_published_for_the_current_run() -> None:
    client, publies = _client()
    with client:
        r = client.post("/injections", json={"nature": "polemique", "description": "Un avis viral.",
                                             "zone": "reseaux_sociaux"})  # fmt: skip
    assert r.status_code == 202
    (injection,) = publies
    assert isinstance(injection, ev.Injection)
    assert (injection.run_id, injection.zone) == ("run_t", Zone.RESEAUX_SOCIAUX)
    assert injection.sim_time_occurred == debut_du_jour(2) + 600  # heure simulée du monde (C3)
    assert r.json()["event_id"] == str(injection.event_id)


@pytest.mark.parametrize(
    ("corps", "code"),
    [
        ({"nature": "inondation", "description": "x"}, 422),  # hors des perturbations prévues
        ({"nature": "polemique", "description": ""}, 422),
        ({"nature": "polemique", "description": "x", "zone": "lune"}, 422),
    ],
)
def test_invalid_injection_is_refused(corps: dict, code: int) -> None:
    client, publies = _client()
    with client:
        assert client.post("/injections", json=corps).status_code == code
    assert publies == []


def test_nothing_is_injected_without_a_running_run() -> None:
    client, publies = _client(statut="termine")
    with client:
        corps = {"nature": "polemique", "description": "x"}
        assert client.post("/injections", json=corps).status_code == 409
        assert client.post("/commandes", json={"commande": "pause"}).status_code == 409
    assert publies == []


def test_commands_are_published() -> None:
    client, publies = _client(statut="pause")
    with client:
        assert client.post("/commandes", json={"commande": "reprise"}).status_code == 202
        assert client.post("/commandes", json={"commande": "dormir"}).status_code == 422
    assert [c.payload.commande for c in publies] == ["reprise"]


def test_agent_inspection_shows_state_rule_and_history() -> None:
    client, _ = _client()
    with client:
        llm = client.get("/agents/sceptique_01").json()
        regle = client.get("/agents/vendeur_01").json()
        assert client.get("/agents/inconnu_99").status_code == 404
        tous = client.get("/agents").json()
        etat = client.get("/etat").json()
    assert llm["etat"]["messages_recus"] == [{"de": "x", "contenu": "salut"}]
    assert "seuil" in regle["etat"]["regle"] and regle["etat"]["seuil"] == "0.420"  # D2 règles
    assert [a["agent_id"] for a in tous] == ["sceptique_01", "vendeur_01"]
    assert etat["run"]["statut"] == "en_cours" and etat["jour"] == 2


def test_live_websocket_starts_with_a_world_snapshot() -> None:
    client, _ = _client()
    with client, client.websocket_connect("/ws") as ws:
        premier = ws.receive_json()
    assert premier["type"] == "pont.instantane"
    assert [a["agent_id"] for a in premier["agents"]] == ["sceptique_01", "vendeur_01"]
    assert premier["run"]["run_id"] == "run_t"


def test_replay_websocket_streams_the_recorded_run(tmp_path: Path) -> None:
    _, events, _ = _run_en_memoire(2)
    events = _avec_run_id(events, "run_r")
    _ecrire_historique(events, tmp_path, "run_r")
    client, _ = _client(runs_dir=tmp_path)
    with client, client.websocket_connect("/ws?rejeu=run_r") as ws:
        debut = ws.receive_json()
        recus = [ws.receive_text() for _ in range(debut["evenements"])]
        fin = ws.receive_json()
    assert debut["type"] == "pont.rejeu" and fin["type"] == "pont.fin_rejeu"
    assert recus == [ev.encode(e).decode() for e in charger("run_r", tmp_path)]
    assert all(ligne(m) for m in recus)  # le client de test sait tout afficher
    with client, client.websocket_connect("/ws?rejeu=absent") as ws:
        assert ws.receive_json()["type"] == "pont.erreur"


# --- de bout en bout : le vrai pont pilote un vrai run ---------------------------------------


@pytest.mark.integration
def test_bridge_injects_and_stops_a_real_run(tmp_path: Path) -> None:
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    config = _petite_config()
    run = RunInfo(f"test-pont-{datetime.now(UTC):%H%M%S%f}", 42, "test", datetime.now(UTC))
    write_manifest(run, config, tmp_path)
    agents, acteurs = population_factice(config, facteur_latence=0.4)  # un jour ≈ 1 à 2 s
    resultat: dict = {}

    def lancer() -> None:
        resultat.update(
            asyncio.run(executer_run(config, run, agents, acteurs, settings, 8, runs_dir=tmp_path))
        )

    def attendre_jour(client: TestClient, jour: int) -> None:
        limite = time.monotonic() + 30
        while time.monotonic() < limite:
            etat = client.get("/etat").json()
            if (etat.get("run") or {}).get("run_id") == run.run_id and etat["jour"] >= jour:
                return
            time.sleep(0.05)
        pytest.fail(f"le run n'a pas atteint le jour {jour}")

    with TestClient(creer_app()) as client:
        fil = threading.Thread(target=lancer)
        fil.start()
        attendre_jour(client, 1)
        corps = {"nature": "vague_chaleur", "description": "Canicule annoncée."}
        injection = client.post("/injections", json=corps)
        assert injection.status_code == 202
        jour_injection = client.get("/etat").json()["jour"]
        attendre_jour(client, jour_injection + 1)
        assert client.post("/commandes", json={"commande": "arret"}).status_code == 202
        fil.join(timeout=60)
        statut = client.get("/etat").json()["run"]["statut"]

    assert statut == "arrete"
    assert resultat["jours"] < 8  # arrêté avant la fin
    assert resultat["evenements_enregistres"] == resultat["evenements_publies"]
    assert resultat["evenements_externes"] == 2  # l'injection et la commande du pont
    events = charger(run.run_id, tmp_path)
    (perturbation,) = [e for e in events if isinstance(e, ev.Disturbance)]
    injections = [e for e in events if isinstance(e, ev.Injection)]
    assert [str(i.event_id) for i in injections] == [injection.json()["event_id"]]
    assert perturbation.caused_by == (injections[0].event_id,)
    assert perturbation.sim_time_occurred > injections[0].sim_time_occurred  # jamais en arrière
    assert resultat["injections"] == 1
