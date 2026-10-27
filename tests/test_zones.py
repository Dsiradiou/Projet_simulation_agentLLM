"""Plusieurs zones d'un même type et lancement depuis l'interface de paramétrage (étape 15 bis)."""

import asyncio
import dataclasses
import json
import sys
import time
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agents.base import Perception
from agents.factices import population_factice
from agents.population import construire_commerce, identites, points_de_vente
from agents.roles import Role
from bridge.app import Diffusion, Services, SuiviDirect, creer_app
from bridge.lancement import DemandeLancement, LancementRefuse, Lanceur, ecrire_configuration
from bridge.vue import VueMonde
from bus import events as ev
from replay.bilan import bilan_depuis
from sim.commerce import Commerce
from sim.config import Config, load_config
from sim.domaine import Canal, Zone, concerne, lieux_du_monde, type_de_lieu
from sim.routines import Routines
from tests.test_orchestrateur import AgentScripte, decision, orchestrateur
from tests.test_pont import EtatEnMemoire
from tests.test_regles import _perturbation

CONFIGS = Path(__file__).parent.parent / "configs"
DEFAULT = load_config(CONFIGS / "default.yaml")


def _config(zones: dict[str, int], base: Config = DEFAULT) -> Config:
    data = base.model_dump(mode="json")
    data["zones"] = zones
    return Config.model_validate(data)


# --- vocabulaire --------------------------------------------------------------------------------


def test_a_single_zone_keeps_its_name_and_several_are_numbered() -> None:
    assert lieux_du_monde() == tuple(z.value for z in Zone)
    lieux = lieux_du_monde({Zone.PLAGE: 3, Zone.PHARMACIE: 1})
    assert lieux[:4] == ("pharmacie", "plage_1", "plage_2", "plage_3")
    assert len(lieux) == len(Zone) + 2
    assert type_de_lieu("plage_2") is Zone.PLAGE
    assert type_de_lieu("centre_commercial") is Zone.CENTRE_COMMERCIAL


def test_a_type_name_targets_every_copy_and_a_number_only_one() -> None:
    assert concerne(None, "plage_2")
    assert concerne("plage", "plage_2") and concerne("plage", "plage")
    assert concerne("plage_2", "plage_2") and not concerne("plage_1", "plage_2")
    assert not concerne("piscine", "plage_1")


def test_zone_counts_are_bounded(tmp_path: Path) -> None:
    assert _config({"plage": 4}).lieux.count("plage_4") == 1
    for nombre in (0, 5):
        with pytest.raises(ValidationError):
            _config({"plage": nombre})
    with pytest.raises(ValidationError):
        _config({"lune": 2})


# --- population et routines ---------------------------------------------------------------------


def test_one_zone_per_type_draws_exactly_as_before() -> None:
    avant = [(x.agent_id, x.zone, x.contacts, x.abonnements) for x in identites(DEFAULT)]
    explicite = _config({z.value: 1 for z in Zone})
    apres = [(x.agent_id, x.zone, x.contacts, x.abonnements) for x in identites(explicite)]
    assert avant == apres
    assert all(not x.lieux for x in identites(explicite))


def test_each_agent_has_a_habitual_copy_of_each_repeated_zone() -> None:
    config = _config({"plage": 3, "pharmacie": 2})
    population = list(identites(config))
    assert all(x.zone in config.lieux for x in population)
    assert all(set(x.lieux) == {Zone.PLAGE, Zone.PHARMACIE} for x in population)
    plages = Counter(x.lieux[Zone.PLAGE] for x in population)
    assert set(plages) == {"plage_1", "plage_2", "plage_3"}  # tirées uniformément
    assert [x.lieux for x in identites(config)] == [x.lieux for x in population]  # reproductible


def test_morning_routine_leads_to_the_habitual_copy() -> None:
    config = _config({"plage": 3, "domicile": 2})
    agents, _ = population_factice(config)
    routines = Routines(config.routines, config.run.seed)
    meteo = ev.WeatherPayload(indice_uv=8, temperature_c=30, conditions="ensoleille")
    vus = 0
    for _ in range(5):
        for agent_id, lieu in routines.deplacements(agents, meteo).items():
            agent = next(a for a in agents if a.agent_id == agent_id)
            assert lieu == agent.lieu_de(type_de_lieu(lieu))
            agent.zone = lieu
            vus += 1
    assert vus > 0


# --- orchestrateur ------------------------------------------------------------------------------


def _parle_en_zone(agent, perception):
    from agents.base import MessageIntent

    return decision(agent, messages=(MessageIntent(Canal.ZONE, "On en parle ici."),))


def test_plan_comes_first_and_zone_talk_stays_in_its_place() -> None:
    config = _config({"plage": 2})
    a = AgentScripte("baigneur_occasionnel_01", Role.BAIGNEUR_OCCASIONNEL, _parle_en_zone,
                     zone="plage_1", toujours=True)  # fmt: skip
    voisin = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda ag, p: decision(ag),
                          zone="plage_1", toujours=True)  # fmt: skip
    loin = AgentScripte("sceptique_02", Role.SCEPTIQUE, lambda ag, p: decision(ag),
                        zone="plage_2", toujours=True)  # fmt: skip
    orch, recorder = orchestrateur([a, voisin, loin], config)
    asyncio.run(orch.executer(2))
    plan = recorder.events[0]
    assert isinstance(plan, ev.WorldPlan) and plan.payload.lieux == config.lieux
    assert recorder.of(ev.AgentMessage)[0].zone == "plage_1"
    assert voisin.perceptions[1].messages and not loin.perceptions[1].messages


def test_part_of_users_is_counted_per_place() -> None:
    def adopte(agent, perception):
        return dataclasses.replace(decision(agent), porteur=True)

    porteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, adopte, zone="plage_1")
    a1 = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda ag, p: decision(ag),
                      zone="plage_1", toujours=True)  # fmt: skip
    a2 = AgentScripte("sceptique_02", Role.SCEPTIQUE, lambda ag, p: decision(ag),
                      zone="plage_2", toujours=True)  # fmt: skip
    orch, _ = orchestrateur([porteur, a1, a2], _config({"plage": 2}))
    asyncio.run(orch.executer(2))
    assert a1.perceptions[1].part_porteurs_zone == pytest.approx(0.5)
    assert a2.perceptions[1].part_porteurs_zone == 0.0


def _perceptions_du_jour_2(cible: str | None) -> dict[str, Perception]:
    from tests.test_pont import ControleScripte, _injection

    agents = [
        AgentScripte(f"sceptique_0{i}", Role.SCEPTIQUE, lambda ag, p: decision(ag), zone=lieu,
                     toujours=True)
        for i, lieu in enumerate(("plage_1", "plage_2", "piscine"), start=1)
    ]  # fmt: skip
    orch, _ = orchestrateur(agents, _config({"plage": 2}))
    orch.controle = ControleScripte([], [_injection(orch.run.run_id, zone=cible)])
    asyncio.run(orch.executer(2))
    return {a.zone: a.perceptions[1] for a in agents}


def test_a_disturbance_on_a_type_reaches_every_copy_and_on_a_place_only_it() -> None:
    sur_le_type = _perceptions_du_jour_2("plage")
    assert sur_le_type["plage_1"].evenements and sur_le_type["plage_2"].evenements
    assert not sur_le_type["piscine"].evenements
    sur_un_lieu = _perceptions_du_jour_2("plage_2")
    assert sur_un_lieu["plage_2"].evenements and not sur_un_lieu["plage_1"].evenements


# --- commerce -----------------------------------------------------------------------------------


def test_each_copy_of_a_shop_has_its_share_of_the_stock() -> None:
    config = _config({"pharmacie": 2})
    points = points_de_vente(config)
    assert points == ["pharmacie_1", "pharmacie_2", "centre_commercial"]
    c = Commerce(config.commerce, 100, points=points)
    assert c.stock["pharmacie_1"] + c.stock["pharmacie_2"] == pytest.approx(
        c.stock["centre_commercial"], abs=1
    )  # même offre totale par type : la géographie change, pas l'offre
    assert c.acheter("a", "pharmacie_2") == "pharmacie_2"
    c.reagir(_perturbation("rupture_stock", zone="pharmacie_1"), 3)
    assert c.stock["pharmacie_1"] == 0 and c.stock["pharmacie_2"] > 0
    c.reagir(_perturbation("rupture_stock", zone=Zone.PHARMACIE), 3)
    assert c.stock["pharmacie_2"] == 0 and c.stock["centre_commercial"] > 0
    ruptures = [e for e in c.fin_du_jour(18 * 60, "r") if isinstance(e, ev.Stock)]
    assert {e.zone for e in ruptures} == {"pharmacie_1", "pharmacie_2"}


def test_fake_run_with_repeated_zones_is_valid_and_replayable() -> None:
    config = _config({"plage": 3, "pharmacie": 2, "domicile": 2})
    agents, acteurs = population_factice(config)
    orch, recorder = orchestrateur(agents, config)
    orch.acteurs_monde = acteurs
    orch.commerce = construire_commerce(config, agents)
    orch.routines = Routines(config.routines, config.run.seed)
    bilan = asyncio.run(orch.executer(6))
    zones = {e.zone for e in recorder.events if e.zone}
    assert {"plage_1", "plage_2", "plage_3"} <= zones
    assert zones <= set(config.lieux) | {z.value for z in Zone}
    rejoue = bilan_depuis(recorder.events, len(agents))
    assert rejoue["decisions"] == bilan.decisions and rejoue["deplacements"] == bilan.deplacements


# --- pont : lieux du run, paramétrage et lancement ----------------------------------------------


class EtatAvecLieux(EtatEnMemoire):
    async def monde(self, run_id):
        return {"jour": "2", "minute": "2040", "meteo": "", "lieux": json.dumps(["plage_1",
                "plage_2", "pharmacie", "piscine", "reseaux_sociaux", "centre_commercial",
                "terrain_sport", "domicile"])}  # fmt: skip


def _client(tmp_path: Path, statut: str = "termine", etat=None, commande=None):
    publies: list[ev.Envelope] = []

    async def publier(event: ev.Envelope) -> None:
        publies.append(event)

    lanceur = Lanceur(tmp_path / "lancements", CONFIGS, commande or (lambda c: [sys.executable,
                      "-c", "print('Run 2026-test-interface-s7 : 3 agents')"]))  # fmt: skip
    services = Services(etat or EtatEnMemoire(statut), publier, VueMonde(), Diffusion(),
                        lanceur=lanceur)  # fmt: skip
    return TestClient(creer_app(services)), publies, lanceur


def _demande(**changes) -> dict:
    demande = {
        "base": "dev.yaml",
        "population": {"pharmacien": 1, "baigneur_occasionnel": 2, "vendeur": 1, "meteo": 1},
        "zones": {"plage": 3, "pharmacie": 2},
        "mode": "asynchrone",
        "moteur": "regles",
        "duree_jours": 5,
        "graine": 7,
    }
    return demande | changes


def test_snapshot_lists_the_places_and_injections_must_target_one(tmp_path: Path) -> None:
    client, publies, _ = _client(tmp_path, etat=EtatAvecLieux())
    with client:
        assert client.get("/etat").json()["lieux"][:2] == ["plage_1", "plage_2"]
        corps = {"nature": "polemique", "description": "Une rumeur."}
        assert client.post("/injections", json=corps | {"zone": "plage_2"}).status_code == 202
        assert client.post("/injections", json=corps | {"zone": "plage"}).status_code == 202
        assert client.post("/injections", json=corps | {"zone": "plage_4"}).status_code == 422
        assert client.post("/injections", json=corps | {"zone": "pharmacie_2"}).status_code == 422
    assert [i.zone for i in publies] == ["plage_2", "plage"]
    with _client(tmp_path)[0] as ancien:  # run sans plan : les sept zones d'origine
        assert ancien.get("/etat").json()["lieux"] == list(lieux_du_monde())


def test_a_new_run_is_announced_by_its_plan() -> None:
    services = Services(etat=None, publier=None, vue=VueMonde(), diffusion=Diffusion())
    suivi = SuiviDirect.__new__(SuiviDirect)
    suivi.services = services
    file = services.diffusion.abonner()
    plan = ev.WorldPlan(run_id="run_z", sim_time_occurred=0, sim_time_processed=0,
                        source_agent_id="orchestrateur",
                        payload=ev.WorldPlanPayload(lieux=("plage_1", "plage_2")))  # fmt: skip
    suivi.recevoir(plan, ev.encode(plan).decode())
    messages = [json.loads(file.get_nowait()) for _ in range(file.qsize())]
    assert [m["type"] for m in messages] == ["pont.nouveau_run", "monde.plan"]


def test_parameters_describe_roles_models_and_zones(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    with client:
        p = client.get("/parametres", params={"base": "default.yaml"}).json()
        assert client.get("/parametres", params={"base": "inconnue.yaml"}).status_code == 404
    roles = {r["role"]: r for r in p["roles"]}
    assert roles["pharmacien"]["modele"] == "qwen3:8b" and roles["pharmacien"]["nombre"] == 6
    assert roles["meteo"] == {"role": "meteo", "palier": "regles", "modele": None, "nombre": 1,
                              "max": 1}  # fmt: skip
    assert sum(r["nombre"] for r in p["roles"]) == DEFAULT.nb_agents
    assert [r["palier"] for r in p["roles"]][0] == "grand"
    assert {z["zone"] for z in p["zones"]} == {z.value for z in Zone}
    assert all(z["nombre"] == 1 and z["max"] == 4 for z in p["zones"])
    assert p["modes"] == ["synchrone", "asynchrone"] and "dev.yaml" in p["bases"]


def test_requested_configuration_is_written_inheriting_and_valid(tmp_path: Path) -> None:
    chemin = ecrire_configuration(DemandeLancement(**_demande()), tmp_path / "l", CONFIGS)
    texte = chemin.read_text(encoding="utf-8")
    assert texte.startswith("# Écrite par l'interface") and "herite_de:" in texte
    config = load_config(chemin)
    assert config.lieux.count("plage_3") == 1 and "pharmacie_2" in config.lieux
    assert (config.simulation.mode.value, config.agents.moteur.value) == ("asynchrone", "regles")
    assert (config.run.seed, config.simulation.duree_jours, config.nb_agents) == (7, 5, 5)
    assert config.llm == load_config(CONFIGS / "dev.yaml").llm  # le reste vient de la base
    with pytest.raises(LancementRefuse, match="aucun agent"):
        ecrire_configuration(DemandeLancement(**_demande(population={})), tmp_path / "v", CONFIGS)


def test_launch_starts_one_run_process_and_reports_its_output(tmp_path: Path) -> None:
    client, _, lanceur = _client(tmp_path)
    with client:
        r = client.post("/lancements", json=_demande())
        assert r.status_code == 202, r.text
        assert r.json()["configuration"].endswith("interface.yaml")
        for _ in range(100):
            etat = client.get("/lancements").json()
            if etat["statut"] != "en_cours":
                break
            time.sleep(0.05)
    assert etat["statut"] == "termine" and etat["run_id"] == "2026-test-interface-s7"
    assert etat["dernieres_lignes"] == ["Run 2026-test-interface-s7 : 3 agents"]
    assert len(list((tmp_path / "lancements").glob("*/sortie.log"))) == 1
    assert lanceur.etat()["code"] == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"zones": {"plage": 5}}, 422),
        ({"population": {"meteo": 1}, "zones": {}}, 202),  # la météo seule reste une population
        ({"population": {"pharmacien": 61}}, 422),
        ({"base": "../secrets.yaml"}, 422),
        ({"base": "absente.yaml"}, 422),
        ({"mode": "parallele"}, 422),
    ],
)
def test_invalid_launch_requests_are_refused(tmp_path: Path, changes: dict, code: int) -> None:
    client, _, _ = _client(tmp_path)
    with client:
        assert client.post("/lancements", json=_demande(**changes)).status_code == code


def test_only_one_run_at_a_time(tmp_path: Path) -> None:
    lent = lambda c: [sys.executable, "-c", "import time; time.sleep(30)"]  # noqa: E731
    client, _, lanceur = _client(tmp_path, commande=lent)
    with client:
        assert client.post("/lancements", json=_demande()).status_code == 202
        assert client.post("/lancements", json=_demande()).status_code == 409
    lanceur._processus.kill()
    lanceur._processus.wait()
    with _client(tmp_path, statut="en_cours")[0] as occupe:  # run lancé depuis un terminal
        assert occupe.post("/lancements", json=_demande()).status_code == 409
