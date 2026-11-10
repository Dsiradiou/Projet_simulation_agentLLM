"""Premier contact (X2, décidé le 10 nov.) : seuls les innovateurs achètent dès leur première
décision ; les autres découvrent d'abord, en C1 comme en C0."""

import asyncio
import json
import random
from pathlib import Path

import pytest

from agents.agent_llm import AgentLLM
from agents.base import Decision, Perception
from agents.population import construire_population, designer_innovateurs
from agents.regles import AgentRegleC0
from agents.roles import Role, palier_du_role
from bus import events as ev
from llm.routeur import Routeur
from llm.sortie import SortieInvalide, lire_sortie, schema_sortie
from sim.config import Config, load_config
from sim.domaine import Action, Sens, Zone, debut_du_jour
from tests.test_llm import ClientScripte, _json

CONFIGS = Path(__file__).parent.parent / "configs"
DEV = load_config(CONFIGS / "dev.yaml")


def _p(jour: int = 3, part: float = 0.9, **kw) -> Perception:
    return Perception(jour, debut_du_jour(jour), Zone.PLAGE, part_porteurs_zone=part, **kw)


def _moteur(config: Config, moteur: str) -> Config:
    data = config.model_dump(mode="json")
    data["agents"]["moteur"] = moteur
    return Config.model_validate(data)


def test_first_contact_closes_buying_in_the_schema_and_the_reading() -> None:
    actions = schema_sortie([], adoption_possible=False)["properties"]["action"]["enum"]
    assert "acheter" not in actions and "appliquer" not in actions and "en_parler" in actions
    assert "acheter" in schema_sortie([])["properties"]["action"]["enum"]
    with pytest.raises(SortieInvalide, match="premier contact"):
        lire_sortie(_json(action="acheter"), [], adoption_possible=False)
    assert (
        lire_sortie(_json(action="en_parler"), [], adoption_possible=False).action
        is Action.EN_PARLER
    )


def test_innovators_are_the_lowest_thresholds_of_llm_roles_same_in_c0_and_c1() -> None:
    c0 = _moteur(DEV, "regles")  # mêmes identités et seuils qu'en C1 : seul le moteur change
    agents, _ = construire_population(c0, routeur=None)
    llm = [a for a in agents if palier_du_role(a.role) is not None]
    innovateurs = [a for a in llm if a.innovateur]
    assert len(innovateurs) == round(DEV.agents.innovateurs * len(llm)) == 4
    assert max(a.seuil for a in innovateurs) <= min(a.seuil for a in llm if not a.innovateur)
    regles = [a for a in agents if palier_du_role(a.role) is None]
    assert all(a.innovateur for a in regles)  # fidèles, vendeurs : aucune contrainte
    designer_innovateurs(agents, 1.0)
    assert all(a.innovateur for a in agents)  # 1 : comme avant le 10 nov.


def _c0(innovateur: bool) -> AgentRegleC0:
    agent = AgentRegleC0(
        "sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0), 0.2, DEV.regles,
        DEV.monde, [], set(), 0,
    )  # fmt: skip
    agent.innovateur = innovateur
    return agent


def test_baseline_waits_at_first_contact_unless_innovator() -> None:
    attend = _c0(innovateur=False)
    premiere = asyncio.run(attend.decider(_p(part=0.9)))  # pression bien au-dessus du seuil
    assert not premiere.porteur and premiere.action is Action.IGNORER
    assert "premier contact" in premiere.justification
    attend.derniere_decision = premiere
    assert asyncio.run(attend.decider(_p(4, part=0.9))).porteur  # le lendemain, il peut adopter
    assert asyncio.run(_c0(innovateur=True).decider(_p(part=0.9))).porteur


def _llm(client: ClientScripte, innovateur: bool) -> AgentLLM:
    agent = AgentLLM("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0),
                     Routeur(client, DEV.llm), DEV.monde, 42, [], 0.2)  # fmt: skip
    agent.innovateur = innovateur
    agent.motif = ev.Motif.PREMIERE_DECISION
    return agent


def test_llm_agent_cannot_buy_at_first_contact_unless_innovator() -> None:
    client = ClientScripte(_json(sens="banal", action="en_parler"))
    decision = asyncio.run(_llm(client, innovateur=False).decider(_p()))
    (appel,) = client.appels
    assert "acheter" not in appel["schema"]["properties"]["action"]["enum"]
    assert "tu ne l'achètes pas et tu ne l'essaies pas encore" in appel["prompt"]
    assert not decision.porteur
    client = ClientScripte(_json(action="acheter"))
    assert asyncio.run(_llm(client, innovateur=True).decider(_p())).porteur
    assert "achètes pas" not in client.appels[0]["prompt"]


def test_second_decision_is_free_again() -> None:
    client = ClientScripte(_json(action="acheter"))
    agent = _llm(client, innovateur=False)
    agent.derniere_decision = Decision(Action.IGNORER, Sens.BANAL, "x", False, ev.Moteur.LLM)
    assert asyncio.run(agent.decider(_p(5))).porteur
    assert "acheter" in client.appels[0]["schema"]["properties"]["action"]["enum"]


def test_fallback_rule_respects_first_contact() -> None:
    """Deux réponses invalides : le repli par règle n'achète pas non plus (A4 et X2)."""
    client = ClientScripte("pas du json", json.dumps({"x": 1}))
    decision = asyncio.run(_llm(client, innovateur=False).decider(_p(part=0.9)))
    assert decision.moteur is ev.Moteur.REPLI and not decision.porteur
