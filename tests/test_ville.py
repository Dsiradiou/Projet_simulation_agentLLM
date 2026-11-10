"""Ville à taille et plan urbain choisis (décidé le 10 nov., `sim/ville.py`)."""

import asyncio
import itertools
import math
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.population import construire_commerce, construire_population, identites
from bridge.lancement import DemandeLancement, ecrire_configuration, parametres
from bus import events as ev
from sim.config import Config, load_config
from sim.domaine import Zone, type_de_lieu
from sim.orchestrateur import Orchestrateur
from sim.routines import Routines, attraits_par_quartier
from sim.ville import (
    AGENTS_PAR_TAILLE,
    LIEUX_PAR_TAILLE,
    PLANS_REELS,
    TAILLES_REELLES,
    Plan,
    Taille,
    construire_plan,
    plan_effectif,
    quartiers_des_agents,
    taille_pour,
)
from tests.test_orchestrateur import RUN, Recorder

CONFIGS = Path(__file__).parent.parent / "configs"
DEV = load_config(CONFIGS / "dev.yaml")


def _avec_ville(base: Config = DEV, **ville) -> Config:
    data = base.model_dump(mode="json")
    data["ville"] = {"taille": "auto", "plan": "hasard", **ville}
    return Config.model_validate(data)


# --- taille et plan ------------------------------------------------------------------------------


def test_size_follows_the_number_of_agents() -> None:
    assert [taille_pour(n) for n in (20, 40, 41, 90, 91, 300)] == [
        Taille.PETITE, Taille.PETITE, Taille.MOYENNE, Taille.MOYENNE, Taille.GRANDE, Taille.GRANDE,
    ]  # fmt: skip
    assert _avec_ville().plan_ville.taille is Taille.MOYENNE  # dev : 60 habitants
    assert len(_avec_ville(taille="grande").lieux) == sum(LIEUX_PAR_TAILLE[Taille.GRANDE].values())


def test_too_few_agents_for_the_size_is_refused() -> None:
    """Décision du 10 nov. : la taille suit la population, sinon des lieux restent vides."""
    data = DEV.model_dump(mode="json")
    data["population"] = {"sceptique": 20, "meteo": 1}
    data["ville"] = {"taille": "grande", "plan": "etoile"}
    with pytest.raises(ValidationError, match="au moins 60 agents"):
        Config.model_validate(data)
    data["ville"]["taille"] = "auto"
    assert Config.model_validate(data).plan_ville.taille is Taille.PETITE


def test_city_and_zone_counts_exclude_each_other() -> None:
    data = DEV.model_dump(mode="json")
    data["zones"] = {"plage": 2}
    data["ville"] = {"taille": "petite", "plan": "lineaire"}
    with pytest.raises(ValidationError, match="s'excluent"):
        Config.model_validate(data)


def test_random_plan_depends_only_on_the_seed() -> None:
    tires = {plan_effectif(Plan.HASARD, g) for g in range(40)}
    assert tires == set(PLANS_REELS)  # les quatre morphologies sortent
    assert plan_effectif(Plan.HASARD, 7) == plan_effectif(Plan.HASARD, 7)
    assert plan_effectif(Plan.ETOILE, 7) is Plan.ETOILE


# --- géographie ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("taille", "plan"), list(itertools.product(TAILLES_REELLES, PLANS_REELS)))
def test_every_plan_places_each_place_once_without_overlap(taille: Taille, plan: Plan) -> None:
    ville = construire_plan(taille, plan)
    assert set(ville.positions) == set(ville.lieux) and len(ville.lieux) == len(set(ville.lieux))
    terres = {
        lieu: p for lieu, p in ville.positions.items() if type_de_lieu(lieu) is not Zone.PLAGE
    }
    ecart = min(math.dist(a, b) for a, b in itertools.combinations(terres.values(), 2))
    assert ecart >= 35  # les bâtiments de deux lieux ne se chevauchent pas
    assert all(z < 0 for _, z in terres.values())  # dans les terres
    plages = [p for lieu, p in ville.positions.items() if type_de_lieu(lieu) is Zone.PLAGE]
    assert all(z == 11.2 for _, z in plages)  # sur la côte
    assert ville.routes


@pytest.mark.parametrize(("taille", "plan"), list(itertools.product(TAILLES_REELLES, PLANS_REELS)))
def test_each_place_serves_at_least_one_neighbourhood(taille: Taille, plan: Plan) -> None:
    """Aucun lieu délaissé : chaque exemplaire de chaque type sert au moins un quartier."""
    ville = construire_plan(taille, plan)
    servis = {lieu for frequentes in ville.frequentes.values() for lieu in frequentes.values()}
    assert servis == set(ville.lieux)
    for quartier, frequentes in ville.frequentes.items():
        assert set(frequentes) == set(Zone) and frequentes[Zone.DOMICILE] == quartier


def test_centre_gets_shops_and_the_periphery_neighbourhoods() -> None:
    ville = construire_plan(Taille.GRANDE, Plan.RADIOCONCENTRIQUE)
    pos = ville.positions
    centre = pos["reseaux_sociaux"]
    loin = [math.dist(pos[q], centre) for q in ville.quartiers]
    pres = [math.dist(pos[p], centre) for p in ("pharmacie_1", "pharmacie_2", "pharmacie_3")]
    assert min(loin) > max(pres)


def test_linear_city_spreads_along_the_coast() -> None:
    lineaire = construire_plan(Taille.MOYENNE, Plan.LINEAIRE).positions
    radio = construire_plan(Taille.MOYENNE, Plan.RADIOCONCENTRIQUE).positions
    largeur = lambda pos: max(x for x, _ in pos.values()) - min(x for x, _ in pos.values())  # noqa: E731
    profondeur = lambda pos: -min(z for _, z in pos.values())  # noqa: E731
    assert largeur(lineaire) / profondeur(lineaire) > largeur(radio) / profondeur(radio)


# --- habitants -----------------------------------------------------------------------------------


def test_neighbourhoods_are_balanced_role_by_role() -> None:
    roles = {f"sceptique_{i:02d}": "sceptique" for i in range(1, 8)} | {
        f"pharmacien_{i:02d}": "pharmacien" for i in range(1, 4)
    }
    quartiers = ("domicile_1", "domicile_2", "domicile_3")
    q = quartiers_des_agents(roles, quartiers, 42)
    assert q == quartiers_des_agents(roles, quartiers, 42)  # reproductible
    effectifs = Counter(q.values())
    assert max(effectifs.values()) - min(effectifs.values()) <= 1
    assert len({q[a] for a in roles if a.startswith("pharmacien")}) == 3  # un par quartier


def test_agents_live_in_a_neighbourhood_and_use_its_places() -> None:
    config = _avec_ville(taille="moyenne", plan="lineaire")
    ville = config.plan_ville
    for x in identites(config):
        quartier = x.lieux[Zone.DOMICILE]
        assert quartier in ville.quartiers
        assert x.lieux == dict(ville.frequentes[quartier])
        assert x.zone in ville.lieux


def test_distant_places_attract_less() -> None:
    ville = construire_plan(Taille.GRANDE, Plan.LINEAIRE)
    attraits = attraits_par_quartier(ville, 120.0)
    for par_type in attraits.values():
        assert par_type[Zone.DOMICILE] == 1.0
        assert all(0 < a <= 1 for a in par_type.values())
    # Dans une ville linéaire, le centre est plus loin des quartiers des extrémités.
    centre = {q: attraits[q][Zone.RESEAUX_SOCIAUX] for q in ville.quartiers}
    assert max(centre.values()) > min(centre.values())


def test_a_city_run_publishes_its_geography_and_leaves_no_place_empty() -> None:
    config = _avec_ville(taille="moyenne", plan="etoile")
    data = config.model_dump(mode="json")
    data["agents"]["moteur"] = "regles"
    config = Config.model_validate(data)
    agents, acteurs = construire_population(config, routeur=None)
    recorder, presents = Recorder(), Counter()

    def soir(_r) -> None:
        presents.update({a.zone for a in agents})

    orch = Orchestrateur(
        config, RUN, agents, acteurs, recorder, on_jour=soir,
        routines=Routines.du_monde(config), commerce=construire_commerce(config, agents),
    )  # fmt: skip
    asyncio.run(orch.executer(20))
    (plan,) = recorder.of(ev.WorldPlan)
    assert plan.schema_version == "1.1" and plan.payload.ville is not None
    assert (plan.payload.ville.taille, plan.payload.ville.plan) == ("moyenne", "etoile")
    assert set(plan.payload.ville.positions) == set(plan.payload.lieux) == set(config.lieux)
    assert all(presents[lieu] > 0 for lieu in config.lieux)  # aucun lieu toujours vide
    assert {e.zone for e in recorder.of(ev.AgentMove)} <= set(config.lieux)


def test_same_seed_same_city_in_c0_and_c1() -> None:
    """E1 : la ville et les quartiers ne dépendent que de la graine, pas du moteur."""
    c1 = _avec_ville()
    data = c1.model_dump(mode="json")
    data["agents"]["moteur"] = "regles"
    c0 = Config.model_validate(data)
    assert c0.plan_ville == c1.plan_ville
    assert [(x.agent_id, x.lieux) for x in identites(c0)] == [
        (x.agent_id, x.lieux) for x in identites(c1)
    ]


def test_old_worlds_without_a_city_are_unchanged() -> None:
    assert DEV.ville is None and DEV.plan_ville is None
    assert Routines.du_monde(DEV)._attraits == {}


# --- pont ----------------------------------------------------------------------------------------


def test_bridge_describes_and_writes_the_city(tmp_path: Path) -> None:
    p = parametres("dev.yaml", CONFIGS)
    assert (p["ville"]["taille"], p["ville"]["plan"]) == ("auto", "hasard")
    assert [t["taille"] for t in p["ville"]["tailles"]] == ["petite", "moyenne", "grande"]
    assert p["ville"]["tailles"][0]["minimum"] == AGENTS_PAR_TAILLE[Taille.PETITE].minimum
    assert p["ville"]["plans"] == [pl.value for pl in PLANS_REELS]
    demande = DemandeLancement(
        base="dev.yaml",
        population={"sceptique": 30, "vendeur": 4, "meteo": 1},
        ville={"taille": "petite", "plan": "radioconcentrique"},
        mode="synchrone",
        moteur="regles",
        duree_jours=3,
    )
    config = load_config(ecrire_configuration(demande, tmp_path / "l", CONFIGS))
    assert config.zones == {} and config.plan_ville.plan is Plan.RADIOCONCENTRIQUE
    assert len(config.lieux) == sum(LIEUX_PAR_TAILLE[Taille.PETITE].values())
