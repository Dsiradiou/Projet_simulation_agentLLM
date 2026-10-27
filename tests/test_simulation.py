"""Test d'intégration : un run factice complet sur le vrai Kafka et le vrai Redis."""

import asyncio
from datetime import UTC, datetime

import pytest
from aiokafka import AIOKafkaConsumer
from redis.asyncio import Redis

from agents.factices import population_factice
from bus import events as ev
from bus.producer import EventProducer
from bus.topics import Topic, ensure_topics
from sim.config import Config, load_config
from sim.etat import EtatMonde
from sim.orchestrateur import Orchestrateur
from sim.run import RunInfo
from sim.settings import load_settings
from tests.test_infra import _services_up

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    return settings


def _petite_config() -> Config:
    data = load_config("configs/default.yaml").model_dump(mode="json")
    data["population"] = {
        "pharmacien": 1,
        "habitue_soin": 2,
        "sceptique": 3,
        "vendeur": 2,
        "meteo": 1,
    }
    return Config.model_validate(data)


def test_factice_run_goes_through_kafka_and_redis(settings) -> None:
    config = _petite_config()
    run = RunInfo(f"test-{datetime.now(UTC):%H%M%S%f}", 42, "test", datetime.now(UTC))

    async def scenario():
        await ensure_topics(settings.kafka_bootstrap_servers)
        lecteur = AIOKafkaConsumer(
            Topic.AGENT_DECISIONS.value,
            Topic.WORLD_EVENTS.value,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            auto_offset_reset="latest",
        )
        await lecteur.start()
        await lecteur.getmany(timeout_ms=1000)  # positionne le lecteur en fin de topic
        redis = Redis.from_url(settings.redis_url)
        etat = EtatMonde(redis, run.run_id)
        try:
            agents, acteurs = population_factice(config)
            async with EventProducer(settings.kafka_bootstrap_servers) as producer:
                bilan = await Orchestrateur(config, run, agents, acteurs, producer, etat).executer(
                    jours=3
                )
                publies = producer.published
            lus: list[ev.Envelope] = []
            async with asyncio.timeout(20):
                attendus = bilan.decisions + bilan.deplacements + 3 + 3  # + pas + météo
                while len(lus) < attendus:
                    lot = await lecteur.getmany(timeout_ms=500)
                    lus += [
                        e
                        for records in lot.values()
                        for r in records
                        if (e := ev.decode(r.value)).run_id == run.run_id
                    ]
            zones = await etat.lire_zones()
            premier = await etat.lire_agent(agents[0].agent_id)
            await etat.reinitialiser()
            return bilan, publies, lus, zones, premier, agents
        finally:
            await lecteur.stop()
            await redis.aclose()

    bilan, publies, lus, zones, premier, agents = asyncio.run(scenario())

    assert bilan.jours == 3 and bilan.incidents == 0
    # Le plan du monde (étape 15 bis) ; par jour : début, météo et fin du jour (étape 14), puis
    # les faits des agents.
    assert publies == 1 + 3 * 3 + bilan.decisions + bilan.deplacements + bilan.messages
    assert [e.payload.jour for e in lus if isinstance(e, ev.ClockStep)] == [1, 2, 3]
    assert sum(isinstance(e, ev.AgentDecision) for e in lus) == bilan.decisions
    assert sum(z["presents"] for z in zones.values()) == len(agents)
    assert premier["jour"] == "3" and premier["zone"] == agents[0].zone
