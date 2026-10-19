"""Couche LLM sans Ollama : schéma de sortie, routeur, relance, repli, incidents, coûts."""

import asyncio
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from agents.agent_llm import AgentLLM
from agents.base import Decision, Perception, porteur_apres
from agents.prompts import MESSAGES_MAX, prompt_jour, prompt_systeme
from agents.regles import repli_seuil_continuite, tirer_seuil
from agents.roles import PALIER_PAR_ROLE, Palier, Role
from bus import events as ev
from llm.client import Reponse
from llm.routeur import DelaiDepasse, Routeur, graine_appel
from llm.sortie import SortieInvalide, lire_sortie, schema_sortie
from sim.config import Config, MondeConfig, SeuilConfig, load_config
from sim.domaine import Action, Canal, Sens, Zone
from tests.test_orchestrateur import Recorder

DEFAULT = Path(__file__).parent.parent / "configs" / "default.yaml"
CONTACTS = ["sceptique_01", "pharmacien_02"]


def _config(**llm) -> Config:
    cfg = load_config(DEFAULT)
    return cfg.model_copy(update={"llm": cfg.llm.model_copy(update=llm)})


def _json(sens: str = "efficace", action: str = "appliquer", message=None) -> str:
    return json.dumps(
        {"justification": "Une amie l'utilise.", "sens": sens, "action": action, "message": message}
    )


class ClientScripte:
    """Client LLM de test : renvoie, appel après appel, les réponses prévues. Une exception dans
    la liste est levée ; un nombre est une attente, interrompue si le délai expire."""

    def __init__(self, *reponses: object) -> None:
        self.reponses = list(reponses)
        self.appels: list[dict] = []
        self.charges: list[str] = []

    async def generer(self, modele, systeme, prompt, schema, graine) -> Reponse:
        self.appels.append(
            {
                "modele": modele,
                "systeme": systeme,
                "prompt": prompt,
                "graine": graine,
                "schema": schema,
            }
        )
        reponse = self.reponses.pop(0)
        if isinstance(reponse, float):
            await asyncio.sleep(reponse)
            reponse = self.reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return Reponse(modele, str(reponse), tokens_entree=600, tokens_sortie=80, duree_s=1.0)

    async def charger(self, modele: str) -> None:
        self.charges.append(modele)


def _agent(client: ClientScripte, role: Role = Role.HABITUE_SOIN, **cfg) -> AgentLLM:
    config = _config(**cfg)
    return AgentLLM(
        f"{role.value}_01",
        role,
        Zone.PLAGE,
        random.Random(0),
        Routeur(client, config.llm),
        config.monde,
        seed=42,
        contacts=CONTACTS,
        seuil=0.3,
    )


def _perception(**kwargs) -> Perception:
    return Perception(jour=3, minute=0, zone=Zone.PLAGE, **kwargs)


def _message(contenu: str, source: str = "sceptique_01") -> ev.AgentMessage:
    return ev.AgentMessage(
        run_id="test",
        sim_time_occurred=0,
        sim_time_processed=0,
        source_agent_id=source,
        zone=Zone.PLAGE,
        payload=ev.AgentMessagePayload(canal=Canal.ZONE, contenu=contenu, livraison=0),
    )


def decider(agent: AgentLLM, perception: Perception | None = None) -> Decision:
    return asyncio.run(agent.decider(perception or _perception()))


# --- schéma de sortie ------------------------------------------------------------------------


def test_schema_uses_closed_categories() -> None:
    schema = schema_sortie(CONTACTS)
    props = schema["properties"]
    assert props["sens"]["enum"] == ["efficace", "tendance", "banal", "demode"]  # pas « depasse »
    assert props["action"]["enum"] == [a.value for a in Action]
    assert list(props)[0] == "justification"  # la raison est écrite avant le choix
    direct = props["message"]["anyOf"][2]
    assert direct["properties"]["destinataire"]["enum"] == CONTACTS


def test_schema_without_contacts_has_no_direct_message() -> None:
    formes = schema_sortie([])["properties"]["message"]["anyOf"]
    assert len(formes) == 2
    assert "direct" not in json.dumps(formes)


@pytest.mark.parametrize(
    "texte",
    [
        "pas du json",
        _json(sens="genial"),  # hors des catégories fermées (B1)
        _json(action="danser"),
        _json(message={"canal": "direct", "contenu": "Essaie-la."}),  # destinataire manquant
        _json(message={"canal": "direct", "contenu": "Essaie.", "destinataire": "inconnu_09"}),
        _json(message={"canal": "zone", "contenu": "Essaie.", "destinataire": "sceptique_01"}),
        json.dumps({"sens": "efficace", "action": "appliquer"}),  # justification manquante
        _json()[:-1] + ', "humeur": "joyeux"}',  # champ inconnu
    ],
)
def test_invalid_outputs_are_rejected(texte: str) -> None:
    with pytest.raises(SortieInvalide):
        lire_sortie(texte, CONTACTS)


def test_valid_output_is_read() -> None:
    sortie = lire_sortie(
        _json(message={"canal": "direct", "contenu": "Essaie.", "destinataire": "sceptique_01"}),
        CONTACTS,
    )
    assert sortie.sens is Sens.EFFICACE
    assert sortie.message and sortie.message.destinataire == "sceptique_01"


# --- routeur ---------------------------------------------------------------------------------


def test_router_maps_tiers_to_configured_models() -> None:
    client = ClientScripte(_json(), _json())
    routeur = Routeur(client, _config().llm)
    assert {p: routeur.modele(p) for p in Palier} == {
        Palier.GRAND: "qwen3:8b",
        Palier.MOYEN: "qwen3:4b",
        Palier.PETIT: "qwen3:1.7b",
    }
    # Configuration C2 : tous les paliers sur le grand modèle, sans toucher au code.
    c2 = Routeur(client, _config(modeles={p: "qwen3:8b" for p in Palier}).llm)
    assert {c2.modele(p) for p in Palier} == {"qwen3:8b"}
    asyncio.run(c2.preparer(Palier.PETIT))
    assert client.charges == ["qwen3:8b"]


def test_router_enforces_timeout() -> None:
    routeur = Routeur(ClientScripte(1.0, _json()), _config(delai_max_s=0.05).llm)
    with pytest.raises(DelaiDepasse):
        asyncio.run(routeur.generer(Palier.PETIT, "s", "p", {}, 1))


def test_call_seed_is_stable_across_processes() -> None:
    code = "from llm.routeur import graine_appel; print(graine_appel(42, 'sceptique_01', 3, 1))"
    sorties = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": graine},
        ).stdout.strip()
        for graine in ("1", "2")
    }
    assert sorties == {str(graine_appel(42, "sceptique_01", 3, 1))}
    assert graine_appel(42, "sceptique_01", 3, 1) != graine_appel(42, "sceptique_01", 3, 2)


# --- agent LLM : appel, relance, repli -------------------------------------------------------


def test_valid_answer_gives_llm_decision_with_cost() -> None:
    client = ClientScripte(_json(sens="tendance", action="acheter"))
    agent = _agent(client)
    perception = _perception(messages=(_message("Tout le monde l'a à la plage."),))
    d = decider(agent, perception)
    assert (d.moteur, d.sens, d.action, d.porteur) == (
        ev.Moteur.LLM,
        Sens.TENDANCE,
        Action.ACHETER,
        True,
    )
    assert d.palier is Palier.MOYEN
    assert d.appel_llm == ev.LlmCall(
        modele="qwen3:4b", tokens_entree=600, tokens_sortie=80, duree_s=1.0, tentatives=1
    )
    assert d.caused_by == (perception.messages[0].event_id,)
    assert d.incidents == ()
    assert client.appels[0]["modele"] == "qwen3:4b"


def test_message_intent_is_built_from_output() -> None:
    message = {"canal": "direct", "contenu": "Essaie-la.", "destinataire": "pharmacien_02"}
    d = decider(_agent(ClientScripte(_json(message=message))))
    assert len(d.messages) == 1
    assert (d.messages[0].canal, d.messages[0].destinataires) == (Canal.DIRECT, ("pharmacien_02",))


def test_invalid_answer_is_retried_once_with_the_reason() -> None:
    client = ClientScripte(_json(sens="genial"), _json())
    d = decider(_agent(client))
    assert d.moteur is ev.Moteur.LLM
    assert d.appel_llm and d.appel_llm.tentatives == 2
    assert d.appel_llm.tokens_entree == 1200  # la tentative ratée compte dans le coût (E4)
    assert [(i.nature, i.tentative) for i in d.incidents] == [
        (ev.NatureIncident.SORTIE_INVALIDE, 1)
    ]
    assert "genial" in d.incidents[0].detail  # la réponse brute est gardée pour l'analyse
    assert "refusée" in client.appels[1]["prompt"]
    assert client.appels[0]["graine"] != client.appels[1]["graine"]


def test_two_failures_fall_back_to_rule() -> None:
    client = ClientScripte("pas du json", _json(action="danser"))
    d = decider(_agent(client))
    assert d.moteur is ev.Moteur.REPLI
    assert [i.nature for i in d.incidents] == [
        ev.NatureIncident.SORTIE_INVALIDE,
        ev.NatureIncident.SORTIE_INVALIDE,
        ev.NatureIncident.REPLI,
    ]
    assert d.appel_llm and d.appel_llm.tentatives == 2
    assert d.caused_by == ()  # la règle ne lit pas les messages


def test_timeout_then_success() -> None:
    client = ClientScripte(1.0, _json(sens="banal"))  # le 1er appel expire avant de répondre
    d = decider(_agent(client, delai_max_s=0.05))
    assert d.moteur is ev.Moteur.LLM and d.sens is Sens.BANAL
    assert [i.nature for i in d.incidents] == [ev.NatureIncident.SANS_REPONSE]
    assert d.appel_llm and d.appel_llm.tokens_entree == 600  # rien de compté pour l'appel perdu


def test_unreachable_model_falls_back_to_rule() -> None:
    client = ClientScripte(ConnectionError("refusé"), ConnectionError("refusé"))
    d = decider(_agent(client))
    assert d.moteur is ev.Moteur.REPLI
    assert [i.nature for i in d.incidents][:2] == [ev.NatureIncident.SANS_REPONSE] * 2
    assert "ConnectionError" in d.incidents[0].detail


# --- règle de repli « seuil + continuité » ---------------------------------------------------


def _precedente(sens: Sens, porteur: bool) -> Decision:
    return Decision(Action.ACHETER, sens, "x", porteur, ev.Moteur.LLM)


def test_fallback_keeps_previous_opinion() -> None:
    choix = repli_seuil_continuite(_precedente(Sens.EFFICACE, True), _perception(), seuil=0.9)
    assert (choix.sens, choix.porteur, choix.action) == (Sens.EFFICACE, True, Action.APPLIQUER)


@pytest.mark.parametrize(("part", "porteur"), [(0.5, True), (0.2, False)])
def test_fallback_without_opinion_uses_threshold(part: float, porteur: bool) -> None:
    choix = repli_seuil_continuite(None, _perception(part_porteurs_zone=part), seuil=0.3)
    assert choix.porteur is porteur
    assert choix.sens is (Sens.TENDANCE if porteur else Sens.BANAL)


def test_threshold_is_bounded_and_reproducible() -> None:
    loi = SeuilConfig(moyenne=0.35, ecart_type=0.5)
    seuils = [tirer_seuil(random.Random(i), loi) for i in range(200)]
    assert all(0 <= s <= 1 for s in seuils)
    assert seuils == [tirer_seuil(random.Random(i), loi) for i in range(200)]


@pytest.mark.parametrize(
    ("action", "avant", "apres"),
    [
        (Action.ACHETER, False, True),
        (Action.APPLIQUER, False, True),
        (Action.ABANDONNER, True, False),
        (Action.EN_PARLER, True, True),
        (Action.IGNORER, False, False),
    ],
)
def test_porteur_follows_action(action: Action, avant: bool, apres: bool) -> None:
    assert porteur_apres(action, avant) is apres


# --- prompts ---------------------------------------------------------------------------------


def test_prompts_take_the_brand_from_config() -> None:
    monde = MondeConfig(
        brand_name="Kelvora", ingredient="okarine", indice_protection="IP 42", concurrent="Brizo"
    )
    for role in PALIER_PAR_ROLE:
        systeme = prompt_systeme(role, monde)
        assert "Kelvora" in systeme and "okarine" in systeme and "IP 42" in systeme
        assert "PROVISOIRE" not in systeme
    jour, _ = prompt_jour(_perception(), None, CONTACTS, monde)
    assert "Kelvora" in jour and "sceptique_01" in jour


def test_prompt_shows_only_the_latest_messages() -> None:
    messages = tuple(_message(f"avis numéro {i}") for i in range(MESSAGES_MAX + 3))
    prompt, montres = prompt_jour(_perception(messages=messages), None, [], _config().monde)
    assert montres == messages[-MESSAGES_MAX:]
    assert "avis numéro 2" not in prompt and f"avis numéro {MESSAGES_MAX + 2}" in prompt
    assert "3 messages plus anciens" in prompt


# --- orchestrateur : publication des incidents et bilan --------------------------------------


def test_orchestrator_publishes_agent_incidents_and_costs() -> None:
    from sim.orchestrateur import Orchestrateur
    from sim.run import new_run

    cfg = _config()
    # Jour 1 : le petit palier passe avant le moyen (ordre_paliers).
    client = ClientScripte("x", "y", _json(sens="genial"), _json())
    routeur = Routeur(client, cfg.llm)
    agents = [
        AgentLLM(f"{r.value}_01", r, Zone.PLAGE, random.Random(0), routeur, cfg.monde, 42, [], 0.3)
        for r in (Role.HABITUE_SOIN, Role.SCEPTIQUE)
    ]
    recorder = Recorder()
    orch = Orchestrateur(
        cfg, new_run(cfg, DEFAULT), agents, [], recorder, preparer=routeur.preparer
    )
    bilan = asyncio.run(orch.executer(1))

    decisions = {d.source_agent_id: d for d in recorder.of(ev.AgentDecision)}
    incidents = recorder.of(ev.Incident)
    assert [(i.payload.agent_id, i.payload.nature) for i in incidents] == [
        ("habitue_soin_01", ev.NatureIncident.SORTIE_INVALIDE),
        ("sceptique_01", ev.NatureIncident.SORTIE_INVALIDE),
        ("sceptique_01", ev.NatureIncident.SORTIE_INVALIDE),
        ("sceptique_01", ev.NatureIncident.REPLI),
    ]
    for incident in incidents:  # chaque incident remonte à sa décision (F6)
        assert incident.caused_by == (decisions[incident.payload.agent_id].event_id,)
    assert decisions["sceptique_01"].payload.moteur is ev.Moteur.REPLI
    assert (bilan.appels_llm, bilan.relances, bilan.replis, bilan.incidents) == (4, 2, 1, 4)
    assert bilan.incidents_par_nature == {"sortie_invalide": 3, "repli": 1}
    assert set(bilan.cout_par_modele) == {"qwen3:4b", "qwen3:1.7b"}
    assert bilan.cout_par_modele["qwen3:1.7b"]["appels"] == 2
    assert client.charges == ["qwen3:1.7b", "qwen3:4b"]  # jour 1 : petit puis moyen


def test_outdated_meaning_is_asked_with_an_unambiguous_word() -> None:
    """« depasse » se lisait « dépasse (mes attentes) » : le modèle répond « demode »."""
    assert lire_sortie(_json(sens="demode"), CONTACTS).sens is Sens.DEPASSE
    with pytest.raises(SortieInvalide):
        lire_sortie(_json(sens="depasse"), CONTACTS)
    assert "demode (démodé" in prompt_systeme(Role.SCEPTIQUE, _config().monde)
