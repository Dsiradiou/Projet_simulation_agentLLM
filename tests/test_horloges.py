import asyncio
import dataclasses

from agents.base import Decision, Perception
from agents.roles import Role
from bus import events as ev
from replay.horloges import mesurer, rapport
from sim.domaine import Canal
from tests.test_orchestrateur import (
    DEBUT,
    AgentScripte,
    _emetteur,
    config_async,
    orchestrateur,
    simple,
)


def test_same_day_reads_reflection_and_missed_information_are_measured() -> None:
    """Jour 1 : le 4b (phase A) parle à 08:30 et le 8b (phase B) le lit le jour même ; le 8b
    parle aussi, trop tard pour le vendeur, passé à 11:12 pendant la phase A."""
    createur = AgentScripte(
        "createur_contenu_01", Role.CREATEUR_CONTENU, _emetteur(Canal.ZONE), toujours=True
    )
    parle = _emetteur(Canal.ZONE)

    def parle_et_cite(agent: AgentScripte, perception: Perception) -> Decision:
        lus = tuple(m.event_id for m in perception.messages)
        return dataclasses.replace(parle(agent, perception), caused_by=lus)

    pharmacien = AgentScripte("pharmacien_01", Role.PHARMACIEN, parle_et_cite, toujours=True)
    vendeur = AgentScripte("vendeur_01", Role.VENDEUR, simple, toujours=True)
    orch, rec = orchestrateur([createur, pharmacien, vendeur], config_async(0.001))
    asyncio.run(orch.executer(jours=1))

    h = mesurer("run_test", rec.events)
    assert h.lus_jour_meme[("moyen", "grand")] == 1
    assert h.heures["grand"] == [DEBUT] and h.reflexions["grand"] == [0]
    assert h.manquees == 1  # le créateur, passé à 08:30 comme le pharmacien, ne l'a pas manqué
    assert h.manquees_par_role == {"pharmacien": 1}
    assert h.messages_emis == {"moyen": 1, "grand": 1}
    assert "Informations manquées : **1**" in rapport([h], "test")
    assert len(rec.of(ev.ClockEnd)) == 1
