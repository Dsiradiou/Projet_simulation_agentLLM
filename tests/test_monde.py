"""Le monde (étape 11) : configuration héritée, réseau social, routines, déclencheurs, budget de
messages, prompts par rôle et compatibilité des schémas d'événements."""

import asyncio
import json
import random
from collections import Counter
from pathlib import Path
from statistics import mean

import pytest

from agents.agent_llm import AgentLLM
from agents.base import Agent, Decision, Perception
from agents.factices import population_factice
from agents.population import identites
from agents.prompts import PROFILS, prompt_jour, prompt_systeme
from agents.roles import PALIER_PAR_ROLE, Role
from bus import events as ev
from llm.routeur import Routeur
from replay.bilan import bilan_depuis
from sim.config import ConfigError, load_config
from sim.domaine import Action, Canal, Sens, Zone, debut_du_jour
from sim.orchestrateur import Orchestrateur
from sim.routines import HEURE_DEPLACEMENT, Routines
from sim.run import new_run
from tests.exemples_evenements import exemples
from tests.test_llm import ClientScripte, _json
from tests.test_orchestrateur import AgentScripte, Recorder, decision

CONFIGS = Path(__file__).parent.parent / "configs"
DEFAULT = CONFIGS / "default.yaml"


# --- configuration -------------------------------------------------------------------------


def test_world_names_are_the_chosen_fictional_ones() -> None:
    monde = load_config(DEFAULT).monde
    assert (monde.brand_name, monde.ingredient) == ("Vaelys", "nacrelle")
    assert monde.indice_protection.startswith("Halo 4")


def test_variants_inherit_from_default() -> None:
    base, dev, tranche = (
        load_config(CONFIGS / f) for f in ("default.yaml", "dev.yaml", "tranche.yaml")
    )
    assert (base.nb_agents, base.simulation.duree_jours) == (120, 90)
    assert dev.simulation.duree_jours == 60
    assert dev.nb_agents - dev.population[Role.METEO] == 60
    assert dev.llm == base.llm and dev.routines == base.routines  # hérités tels quels
    assert Role.VENDEUR not in tranche.population  # la population est remplacée, pas fusionnée
    llm = lambda c: sum(n for r, n in c.population.items() if r in PALIER_PAR_ROLE)  # noqa: E731
    assert abs(llm(dev) / 60 - llm(base) / 119) < 0.03  # même part d'agents LLM


def test_circular_inheritance_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("herite_de: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("herite_de: a.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="circulaire"):
        load_config(tmp_path / "a.yaml")


# --- réseau social et routines ---------------------------------------------------------------


def test_social_network_favours_creators_and_is_reproducible() -> None:
    config = load_config(DEFAULT)
    tous = list(identites(config))
    assert [x.abonnements for x in tous] == [x.abonnements for x in identites(config)]
    abonnes = Counter(suivi for x in tous for suivi in x.abonnements)
    for x in tous:
        assert len(x.abonnements) == config.reseau.abonnements_par_agent
        assert x.agent_id not in x.abonnements and x.agent_id not in x.contacts
        assert len(x.contacts) >= config.reseau.contacts_directs
    par_role = lambda r: mean(abonnes[x.agent_id] for x in tous if x.role is r)  # noqa: E731
    assert par_role(Role.CREATEUR_CONTENU) > 3 * par_role(Role.BAIGNEUR_OCCASIONNEL)


def test_starting_zone_follows_role_routine() -> None:
    config = load_config(DEFAULT)
    for x in identites(config):
        if x.role in config.routines.roles:
            assert x.zone in config.routines.roles[x.role]["beau"]


def _meteo(conditions: str, uv: int = 6) -> ev.WeatherPayload:
    return ev.WeatherPayload(indice_uv=uv, temperature_c=28, conditions=conditions)


def test_routines_depend_on_weather_and_are_reproducible() -> None:
    config = load_config(DEFAULT)
    agents, _ = population_factice(config)
    routines = Routines(config.routines, 42)
    pluie = routines.deplacements(agents, _meteo("nuageux"))
    baigneurs = [z for a, z in pluie.items() if a.startswith("baigneur")]
    assert baigneurs and Zone.PLAGE not in baigneurs  # pas de plage par mauvais temps
    assert pluie == Routines(config.routines, 42).deplacements(agents, _meteo("nuageux"))
    fixes = config.routines.model_copy(update={"stabilite": 1.0})
    assert Routines(fixes, 42).deplacements(agents, _meteo("ensoleille")) == {}


def test_morning_moves_are_published_before_decisions() -> None:
    config = load_config(DEFAULT)
    agents, acteurs = population_factice(config)
    recorder = Recorder()
    orch = Orchestrateur(
        config, new_run(config, DEFAULT), agents, acteurs, recorder,
        routines=Routines(config.routines, 42),
    )  # fmt: skip
    asyncio.run(orch.executer(1))
    (meteo,) = recorder.of(ev.Weather)
    matin = [m for m in recorder.of(ev.AgentMove) if m.sim_time_occurred == HEURE_DEPLACEMENT]
    assert len(matin) > 20
    assert all(m.caused_by == (meteo.event_id,) for m in matin)  # le temps explique le trajet
    premiere = recorder.events.index(recorder.of(ev.AgentDecision)[0])
    assert all(recorder.events.index(m) < premiere for m in matin)


def test_network_posts_reach_only_followers() -> None:
    def emet(a, p):
        return decision(a, messages=(ev_intent(),)) if p.jour == 1 else decision(a)

    def ev_intent():
        from agents.base import MessageIntent

        return MessageIntent(Canal.RESEAUX, "Ma nouvelle crème préférée !")

    createur = AgentScripte("createur_contenu_01", Role.CREATEUR_CONTENU, emet, Zone.PLAGE)
    fan = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a), Zone.PISCINE)
    autre = AgentScripte("sceptique_02", Role.SCEPTIQUE, lambda a, p: decision(a), Zone.PISCINE)
    createur.abonnements, fan.abonnements, autre.abonnements = (), ("createur_contenu_01",), ()
    config = load_config(DEFAULT)
    orch = Orchestrateur(config, new_run(config, DEFAULT), [createur, fan, autre], [], Recorder())
    asyncio.run(orch.executer(2))
    assert len(fan.perceptions) == 2 and fan.perceptions[1].messages  # abonné : reçoit et décide
    assert len(autre.perceptions) == 1  # non abonné : rien reçu, aucun nouvel appel


# --- déclencheurs ----------------------------------------------------------------------------


class Temoin(Agent):
    async def decider(self, perception: Perception) -> Decision:
        return Decision(Action.IGNORER, Sens.BANAL, "x", False, ev.Moteur.REGLE)


def _temoin() -> Temoin:
    agent = Temoin("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0))
    declencheurs = load_config(DEFAULT).declencheurs  # délai fixe de 3 jours pour ce test
    agent.declencheurs = declencheurs.model_copy(update={"delai_reflexion_ecart": 0})
    return agent


def _p(jour: int, part: float = 0.2, meteo: ev.WeatherPayload | None = None, **kw) -> Perception:
    meteo = meteo or _meteo("nuageux", 5)
    minute = debut_du_jour(jour)
    return Perception(jour, minute, Zone.PLAGE, part_porteurs_zone=part, meteo=meteo, **kw)


def _decide(agent: Agent, p: Perception) -> ev.Motif | None:
    if agent.doit_decider(p):
        agent.noter_decision(Decision(Action.IGNORER, Sens.BANAL, "x", False, ev.Moteur.REGLE), p)
    return agent.motif


def test_each_trigger_has_its_motive_and_respects_the_reflection_delay() -> None:
    a = _temoin()  # délai de réflexion : 3 jours
    canicule = _meteo("canicule", 10)
    message = exemples()[ev.AgentMessage]
    assert _decide(a, _p(1)) is ev.Motif.PREMIERE_DECISION
    assert _decide(a, _p(2)) is None  # rien de changé : aucun appel
    assert _decide(a, _p(3, part=0.40)) is None  # +20 points, mais encore dans le délai
    assert _decide(a, _p(4, part=0.40)) is ev.Motif.EXPOSITION  # délai écoulé
    assert _decide(a, _p(5, part=0.40, meteo=canicule)) is None  # dans le délai
    assert _decide(a, _p(7, part=0.40)) is None
    assert _decide(a, _p(8, part=0.40, meteo=canicule)) is ev.Motif.METEO  # début de canicule
    assert _decide(a, _p(9, part=0.40, messages=(message,))) is None  # le message attendra
    assert _decide(a, _p(11, part=0.40, messages=(message,))) is ev.Motif.MESSAGE
    assert _decide(a, _p(20, part=0.40)) is None
    assert _decide(a, _p(21, part=0.40)) is ev.Motif.LASSITUDE  # 10 jours sans y repenser
    perturbation = exemples()[ev.Disturbance]
    assert _decide(a, _p(22, part=0.4, evenements=(perturbation,))) is ev.Motif.PERTURBATION


def test_messages_received_during_the_delay_are_read_at_the_next_decision() -> None:
    from agents.base import MessageIntent

    def ecrit(agent, perception):
        message = MessageIntent(Canal.DIRECT, f"Avis du jour {perception.jour}", ("sceptique_01",))
        return decision(agent, messages=(message,))

    emetteur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, ecrit, toujours=True)
    lecteur = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a))
    lecteur.declencheurs = load_config(DEFAULT).declencheurs.model_copy(
        update={"delai_reflexion_ecart": 0}
    )
    config = load_config(DEFAULT)
    orch = Orchestrateur(config, new_run(config, DEFAULT), [emetteur, lecteur], [], Recorder())
    asyncio.run(orch.executer(4))
    assert [p.jour for p in lecteur.perceptions] == [1, 4]  # une seule reconsidération
    lus = [m.payload.contenu for m in lecteur.perceptions[1].messages]
    assert lus == ["Avis du jour 1", "Avis du jour 2", "Avis du jour 3"]  # rien n'est perdu


def test_reflection_delays_are_desynchronised() -> None:
    config = load_config(DEFAULT)
    agents, acteurs = population_factice(config)
    recorder = Recorder()
    orch = Orchestrateur(config, new_run(config, DEFAULT), agents, acteurs, recorder,
                         routines=Routines(config.routines, 42))  # fmt: skip
    asyncio.run(orch.executer(12))
    par_jour = Counter(d.sim_time_occurred // 1440 + 1 for d in recorder.of(ev.AgentDecision))
    suite = [par_jour[j] for j in range(5, 13)]
    assert min(suite) > 0  # chaque jour, quelqu'un reconsidère
    assert max(suite) < 0.5 * sum(suite)  # pas de vague où tout le monde décide ensemble


def test_agents_without_triggers_keep_the_old_behaviour() -> None:
    a = Temoin("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0))
    assert _decide(a, _p(1)) is ev.Motif.PREMIERE_DECISION
    assert _decide(a, _p(30, part=0.9, meteo=_meteo("canicule", 11))) is None


def test_motive_is_published_and_counted_in_replay() -> None:
    config = load_config(DEFAULT)
    agents, acteurs = population_factice(config)
    recorder = Recorder()
    orch = Orchestrateur(config, new_run(config, DEFAULT), agents, acteurs, recorder,
                         routines=Routines(config.routines, 42))  # fmt: skip
    bilan = asyncio.run(orch.executer(4))
    motifs = Counter(d.payload.motif.value for d in recorder.of(ev.AgentDecision))
    assert motifs == Counter(bilan.declenchements)
    assert motifs["premiere_decision"] == len(agents)
    assert bilan_depuis(recorder.events, len(agents))["declenchements"] == bilan.declenchements


# --- budget de messages et prompts ----------------------------------------------------------


def test_message_budget_closes_the_message_field() -> None:
    config = load_config(DEFAULT)
    parle = {"canal": "zone", "contenu": "Elle tient bien."}
    client = ClientScripte(_json(message=parle), _json(), _json(message=parle))
    agent = AgentLLM("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0),
                     Routeur(client, config.llm), config.monde, 42, [], 0.3,
                     messages_par_semaine=1)  # fmt: skip
    for jour in (1, 3, 8):
        agent.motif = ev.Motif.MESSAGE
        asyncio.run(agent.decider(_p(jour)))
    formes = [json.dumps(a["schema"]["properties"]["message"]) for a in client.appels]
    assert "zone" in formes[0] and "zone" not in formes[1]  # J3 : budget épuisé, null seulement
    assert "zone" in formes[2]  # J8 : une semaine plus tard, il peut reparler
    assert "assez parlé" in client.appels[1]["prompt"]


def test_role_prompts_carry_speech_habits_and_the_no_invention_rule() -> None:
    monde = load_config(DEFAULT).monde
    for role, profil in PROFILS.items():
        systeme = prompt_systeme(role, monde)
        assert "Vaelys" in systeme and "nacrelle" in systeme and "Halo 4" in systeme
        assert profil.parole in systeme
        assert "N'invente ni message" in systeme
    jour, _ = prompt_jour(_p(5), None, [], monde, motif=ev.Motif.EXPOSITION, messages_restants=2)
    assert "la part de gens qui l'utilisent a nettement changé" in jour
    assert "aucun message" in jour and "encore envoyer 2 message(s)" in jour


# --- compatibilité des schémas ---------------------------------------------------------------


def test_version_1_0_events_from_older_runs_still_decode() -> None:
    decision_v10 = exemples()[ev.AgentDecision].model_copy(update={"schema_version": "1.0"})
    brut = json.loads(ev.encode(decision_v10))
    brut["payload"].pop("motif")
    assert ev.decode(json.dumps(brut)).payload.motif is None
    nouvelle = ev.Disturbance(
        run_id="r", sim_time_occurred=0, sim_time_processed=0, source_agent_id="orchestrateur",
        payload=ev.DisturbancePayload(nature="publication_virale", description="Vidéo virale."),
    )  # fmt: skip
    assert nouvelle.schema_version == "1.1"
    assert ev.decode(ev.encode(nouvelle)) == nouvelle
