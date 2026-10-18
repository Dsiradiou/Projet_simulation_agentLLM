"""Tests d'intégration de la couche LLM : nécessitent Ollama lancé et les trois modèles Qwen3.

Ils sont ignorés, avec un message, si Ollama ne répond pas ou si un modèle manque.
"""

import asyncio
import random

import httpx
import pytest

from agents.agent_llm import AgentLLM
from agents.base import Perception
from agents.roles import Palier, Role
from bus.events import Moteur
from llm.client import ClientOllama
from llm.routeur import Routeur, options_de
from sim.config import Config, load_config
from sim.domaine import Zone
from sim.settings import load_settings

pytestmark = pytest.mark.ollama

UN_ROLE_PAR_PALIER = {
    Palier.PETIT: Role.SCEPTIQUE,
    Palier.MOYEN: Role.HABITUE_SOIN,
    Palier.GRAND: Role.PHARMACIEN,
}


@pytest.fixture(scope="module")
def config() -> Config:
    config = load_config("configs/default.yaml")
    host = load_settings().ollama_host
    try:
        noms = {m["name"] for m in httpx.get(f"{host}/api/tags", timeout=3).json()["models"]}
    except httpx.HTTPError:
        pytest.skip("Ollama injoignable : lancer Ollama")
    manquants = set(config.llm.modeles.values()) - noms
    if manquants:
        pytest.skip(f"modèles absents : {', '.join(sorted(manquants))} (ollama pull)")
    return config


@pytest.mark.parametrize("palier", list(Palier))
def test_real_model_returns_a_valid_decision(config: Config, palier: Palier) -> None:
    routeur = Routeur(ClientOllama(load_settings().ollama_host, options_de(config.llm)), config.llm)
    role = UN_ROLE_PAR_PALIER[palier]
    agent = AgentLLM(
        f"{role.value}_01", role, Zone.PLAGE, random.Random(0), routeur, config.monde,
        config.run.seed, ["baigneur_occasionnel_01"], 0.3,
    )  # fmt: skip
    perception = Perception(jour=1, minute=600, zone=Zone.PLAGE, part_porteurs_zone=0.2)
    decision = asyncio.run(agent.decider(perception))
    assert decision.moteur is Moteur.LLM  # valide en deux tentatives au plus, sans repli
    assert decision.appel_llm and decision.appel_llm.modele == config.llm.modeles[palier]
    assert decision.appel_llm.tokens_entree > 0 and decision.appel_llm.tokens_sortie > 0
