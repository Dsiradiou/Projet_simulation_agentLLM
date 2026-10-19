"""Tranche verticale (étape 7) : population réelle, console, et run de bout en bout."""

import asyncio
from datetime import UTC, datetime

import pytest
from aiokafka import AIOKafkaConsumer

from agents.agent_llm import AgentLLM
from agents.factices import population_factice
from agents.population import construire_population, identites
from agents.regles import AgentAuSeuil
from agents.roles import Palier, Role
from bus import events as ev
from bus.console import formater
from bus.producer import EventProducer
from bus.topics import Topic, ensure_topics
from llm.client import ClientOllama
from llm.routeur import Routeur, options_de
from sim.config import Config, load_config
from sim.orchestrateur import Orchestrateur
from sim.run import RunInfo
from sim.settings import load_settings
from tests.exemples_evenements import exemples
from tests.test_infra import _services_up
from tests.test_llm import ClientScripte

TRANCHE = "configs/tranche.yaml"


def test_tranche_config_has_one_llm_agent_per_tier_and_one_rule_agent() -> None:
    config = load_config(TRANCHE)
    agents, acteurs = construire_population(config, Routeur(ClientScripte(), config.llm))
    llm = [a for a in agents if isinstance(a, AgentLLM)]
    assert sorted(a.palier for a in llm) == sorted(Palier)
    assert [type(a) for a in agents if a not in llm] == [AgentAuSeuil]  # fidèle, à règles
    assert [a.actor_id for a in acteurs] == ["meteo"]


def test_population_follows_roles_and_is_reproducible() -> None:
    config = load_config("configs/default.yaml")
    routeur = Routeur(ClientScripte(), config.llm)
    agents, _ = construire_population(config, routeur)
    assert len(agents) == config.nb_agents - config.population[Role.METEO]
    for agent in agents:
        assert isinstance(agent, AgentLLM) is (agent.palier is not None)
        if agent.role is not Role.MARQUE_CONCURRENTE:  # la marque n'adopte pas, elle annonce
            assert 0 <= agent.seuil <= 1
    encore, _ = construire_population(config, routeur)
    cles = lambda a: (a.agent_id, a.zone, getattr(a, "seuil", None))  # noqa: E731
    assert [cles(a) for a in agents] == [cles(a) for a in encore]
    # Agents factices et réels partagent identités, zones et contacts : on teste le même monde.
    factices, _ = population_factice(config)
    assert [(a.agent_id, a.zone, a.contacts) for a in factices] == [
        (x.agent_id, x.zone, x.contacts) for x in identites(config)
    ]


def test_console_formats_every_event_type() -> None:
    for event in exemples().values():
        assert formater(event)
        if isinstance(event, ev.AgentDecision | ev.AgentMessage | ev.AgentMove):
            assert event.source_agent_id in formater(event)


# --- de bout en bout : Ollama, Kafka et le client console -----------------------------------


@pytest.mark.integration
@pytest.mark.ollama
def test_vertical_slice_end_to_end() -> None:
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    config: Config = load_config(TRANCHE)
    try:
        import httpx

        httpx.get(f"{settings.ollama_host}/api/tags", timeout=3)
    except httpx.HTTPError:
        pytest.skip("Ollama injoignable")
    run = RunInfo(f"test-tranche-{datetime.now(UTC):%H%M%S%f}", 42, "tranche", datetime.now(UTC))

    async def scenario() -> tuple:
        await ensure_topics(settings.kafka_bootstrap_servers)
        lecteur = AIOKafkaConsumer(
            *(t.value for t in Topic),
            bootstrap_servers=settings.kafka_bootstrap_servers,
            auto_offset_reset="latest",
        )
        await lecteur.start()
        await lecteur.getmany(timeout_ms=1000)
        routeur = Routeur(ClientOllama(settings.ollama_host, options_de(config.llm)), config.llm)
        agents, acteurs = construire_population(config, routeur)
        async with EventProducer(settings.kafka_bootstrap_servers) as producer:
            orch = Orchestrateur(config, run, agents, acteurs, producer, preparer=routeur.preparer)
            bilan = await orch.executer(2)
            publies = producer.published
        lus: list[ev.Envelope] = []
        async with asyncio.timeout(20):
            while len(lus) < publies:
                lot = await lecteur.getmany(timeout_ms=500)
                lus += [
                    e
                    for records in lot.values()
                    for r in records
                    if (e := ev.decode(r.value)).run_id == run.run_id
                ]
        await lecteur.stop()
        return bilan, lus

    bilan, lus = asyncio.run(scenario())
    decisions = [e for e in lus if isinstance(e, ev.AgentDecision)]
    assert len(decisions) == bilan.decisions
    assert sum(d.payload.appel_llm.tentatives for d in decisions if d.payload.appel_llm) == (
        bilan.appels_llm
    )
    for d in decisions:  # chaque décision LLM vient du modèle de son palier (A5)
        if d.payload.palier:
            assert d.payload.appel_llm.modele == config.llm.modeles[d.payload.palier]
        else:
            assert d.payload.moteur is ev.Moteur.REGLE
    assert all(formater(e) for e in lus)  # la console sait tout afficher
