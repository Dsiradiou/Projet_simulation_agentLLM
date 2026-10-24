"""Indicateurs d'émergence (étape 14) : formules, traçabilité, dérives, run témoin, trace."""

import asyncio
import math
import random
from dataclasses import replace

from agents.base import MessageIntent, Perception
from agents.prompts import PROFILS, prompt_jour, prompt_systeme
from agents.roles import Role
from bus import events as ev
from indicators.calcul import (
    ADOPTION,
    DERIVES,
    INFORMATION_MUTUELLE,
    INFORMATION_MUTUELLE_P,
    SENS_PARTAGE,
    EtatPopulation,
    convergence,
    information_mutuelle,
    p_permutation,
)
from indicators.derives import Derive, detecter
from indicators.rapport import avec_fins_de_jour
from indicators.service import CalculateurDuRun
from indicators.trace import jour_de_variation, serie_de, tracer
from sim.domaine import Action, Canal, Sens, Zone, debut_du_jour
from tests.test_orchestrateur import AgentScripte, config, decision, orchestrateur
from tests.test_regles import _run_c0


def _decision(agent_id: str, sens: Sens, jour: int = 1, porteur: bool = False, zone=Zone.PLAGE):
    return ev.AgentDecision(
        run_id="r",
        sim_time_occurred=debut_du_jour(jour) + 600,
        sim_time_processed=debut_du_jour(jour) + 600,
        source_agent_id=agent_id,
        zone=zone,
        payload=ev.AgentDecisionPayload(
            action=Action.ACHETER if porteur else Action.IGNORER,
            sens=sens,
            justification="test",
            porteur=porteur,
            moteur=ev.Moteur.REGLE,
        ),
    )


# --- formules ------------------------------------------------------------------------------


def test_convergence_and_mutual_information_bounds() -> None:
    assert convergence([10]) == 1.0
    assert math.isclose(convergence([5, 5, 5, 5]), 0.0, abs_tol=1e-12)
    assert 0 < convergence([8, 2]) < 1
    # Chaque groupe a son sens : le rôle explique tout.
    separes = [("a", "efficace")] * 5 + [("b", "banal")] * 5
    assert math.isclose(information_mutuelle(separes)[1], 1.0)
    # Même répartition dans chaque groupe : le rôle n'explique rien.
    meles = [("a", "efficace"), ("a", "banal"), ("b", "efficace"), ("b", "banal")]
    assert information_mutuelle(meles)[1] == 0.0
    assert information_mutuelle([("a", "banal")] * 3) == (0.0, 0.0)  # un seul sens


def test_permutation_separates_subcultures_from_chance() -> None:
    separes = [("a", "efficace")] * 10 + [("b", "banal")] * 10
    assert p_permutation(separes, random.Random(1)) < 0.01
    rng = random.Random(2)
    hasard = [(g, rng.choice(["efficace", "banal", "tendance"])) for g in "ab" * 15]
    p = p_permutation(hasard, random.Random(3))
    assert p > 0.05
    assert p == p_permutation(hasard, random.Random(3))  # reproductible


def test_population_state_counts_last_opinion_and_traces_its_decisions() -> None:
    etat = EtatPopulation()
    premiere = _decision("pharmacien_01", Sens.BANAL)
    derniere = _decision("pharmacien_01", Sens.EFFICACE, jour=2, porteur=True)
    sceptique = _decision("sceptique_01", Sens.EFFICACE, zone=Zone.PHARMACIE)
    marque = _decision("responsable_marque_01", Sens.TENDANCE)
    for e in (premiere, derniere, sceptique, marque):
        etat.appliquer(e)
    mesures = {(m.nom, m.groupe): m for m in etat.mesurer(2, graine=42)}
    globale = mesures[(SENS_PARTAGE, None)]
    assert globale.valeur == 1.0 and globale.effectif == 2  # la marque n'est pas le public
    assert globale.caused_by == (derniere.event_id, sceptique.event_id)  # dernier avis, trié
    assert globale.repartition == {"efficace": 2}
    assert mesures[(ADOPTION, None)].valeur == 0.5
    assert (SENS_PARTAGE, "role:pharmacien") in mesures
    assert mesures[(SENS_PARTAGE, "zone:pharmacie")].caused_by == (sceptique.event_id,)
    etat.appliquer(
        ev.AgentMove(
            run_id="r", sim_time_occurred=debut_du_jour(3) + 480,
            sim_time_processed=debut_du_jour(3) + 480, source_agent_id="sceptique_01",
            zone=Zone.PLAGE, payload=ev.AgentMovePayload(depuis=Zone.PHARMACIE, vers=Zone.PLAGE),
        )
    )  # fmt: skip
    groupes = {m.groupe for m in etat.mesurer(3, graine=42)}
    assert "zone:pharmacie" not in groupes and "zone:plage" in groupes


def test_indicators_are_computed_at_each_day_end_and_reproducible() -> None:
    events, _, _ = _run_c0(6)
    calculs = []
    for _ in range(2):
        calcul = CalculateurDuRun("run", graine=42)
        calculs.append([i for e in events for i in calcul.appliquer(e)])
    premier, second = calculs
    assert [(i.payload, i.caused_by) for i in premier] == [(i.payload, i.caused_by) for i in second]
    globales = [i for i in premier if i.payload.nom == SENS_PARTAGE and i.payload.groupe is None]
    assert len(globales) == 6  # une valeur par fin de jour
    decisions = {e.event_id for e in events if isinstance(e, ev.AgentDecision)}
    assert all(set(i.caused_by) <= decisions for i in premier)  # F6 : vers des faits bruts
    noms = {i.payload.nom for i in premier}
    assert {ADOPTION, INFORMATION_MUTUELLE, INFORMATION_MUTUELLE_P} <= noms
    assert DERIVES not in noms  # aucune décision LLM dans la baseline
    for i in premier:  # chaque indicateur passe le contrat d'événements
        ev.decode(ev.encode(i))


# --- dérives -------------------------------------------------------------------------------


def test_drift_detectors() -> None:
    habitants = {"pharmacien_02", "sceptique_01", "habitue_soin_03"}

    def signes(action, sens, texte, correspondants=frozenset()) -> set[Derive]:
        return set(detecter(action, sens, [texte], "sceptique_01", set(correspondants), habitants))

    propre = "Le soleil tape, je garde ma crème habituelle."
    assert signes(Action.IGNORER, Sens.BANAL, propre) == set()
    assert signes(Action.ACHETER, Sens.BANAL, propre) == set()  # achat sans enthousiasme
    assert Derive.INCOHERENCE in signes(Action.ACHETER, Sens.DEPASSE, propre)
    assert Derive.INCOHERENCE in signes(Action.CRITIQUER, Sens.EFFICACE, propre)
    cite = "pharmacien_02 m'a dit qu'elle tient bien."
    assert Derive.INVENTION_PERSONNE in signes(Action.IGNORER, Sens.BANAL, cite)
    assert not signes(Action.IGNORER, Sens.BANAL, cite, {"pharmacien_02"})
    assert Derive.SANTE in signes(
        Action.IGNORER, Sens.BANAL, "Il paraît qu'elle a des effets secondaires."
    )
    assert Derive.LANGUE in signes(Action.IGNORER, Sens.BANAL, "Elle tient bien 今天.")
    assert Derive.ASSISTANT in signes(Action.IGNORER, Sens.BANAL, "N'hésitez pas à l'essayer !")
    assert Derive.ASSISTANT in signes(Action.IGNORER, Sens.BANAL, "En tant qu'IA, je ne juge pas.")


def test_correspondents_come_from_events_and_drifts_are_published() -> None:
    def emetteur(agent, p: Perception):
        if p.jour == 1:
            message = MessageIntent(Canal.DIRECT, "Essaie-la.", ("sceptique_01",))
            return decision(agent, messages=(message,))
        return decision(agent)

    def lecteur(agent, p: Perception):
        if p.jour == 2:
            vus = tuple(m.event_id for m in p.messages)
            texte = "habitue_soin_01 me l'a conseillée, et pharmacien_01 aussi."
            return replace(decision(agent, caused_by=vus), justification=texte)
        return decision(agent)

    agents = [
        AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, emetteur, toujours=True),
        AgentScripte("sceptique_01", Role.SCEPTIQUE, lecteur, toujours=True),
        AgentScripte("pharmacien_01", Role.PHARMACIEN, lambda a, p: decision(a), toujours=True),
    ]
    orch, rec = orchestrateur(agents)
    asyncio.run(orch.executer(jours=2))
    calcul = CalculateurDuRun("run_test", graine=1)
    derives = [i for e in rec.events for i in calcul.appliquer(e) if i.payload.nom == DERIVES]
    jour2 = derives[-1]
    assert jour2.payload.repartition == {"invention_personne": 1}  # pharmacien_01 seulement
    signalee = next(e for e in rec.of(ev.AgentDecision) if e.event_id in jour2.caused_by)
    assert signalee.source_agent_id == "sceptique_01"
    assert jour2.payload.effectif == 3 and math.isclose(jour2.payload.valeur, 1 / 3, abs_tol=1e-6)


# --- run témoin (B3) -----------------------------------------------------------------------


def test_witness_run_delivers_no_message_and_hides_the_carriers() -> None:
    def parle(agent, p: Perception):
        return decision(agent, messages=(MessageIntent(Canal.ZONE, "Elle tient bien."),))

    parleur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, parle, toujours=True)
    voisin = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a), toujours=True)
    orch, rec = orchestrateur([parleur, voisin], config(simulation={"diffusion": False}))
    asyncio.run(orch.executer(jours=3))
    assert rec.of(ev.AgentMessage)  # il parle, mais personne ne l'entend
    assert all(not p.messages for p in voisin.perceptions)
    assert all(p.part_porteurs_zone is None for p in voisin.perceptions)
    texte, _ = prompt_jour(voisin.perceptions[-1], None, [], config().monde)
    assert "Autour de toi" not in texte

    parleur = AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, parle, toujours=True)
    voisin = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a), toujours=True)
    orch, _ = orchestrateur([parleur, voisin])  # avec diffusion : le message arrive
    asyncio.run(orch.executer(jours=3))
    assert any(p.messages for p in voisin.perceptions)


# --- prompts : un habitant découvre le produit ----------------------------------------------


def test_each_role_says_how_it_welcomes_a_new_product() -> None:
    monde = config().monde
    for role, profil in PROFILS.items():
        assert profil.nouveaute in prompt_systeme(role, monde)
    p = Perception(1, 600, Zone.PLAGE)
    texte, _ = prompt_jour(p, None, [], monde, ev.Motif.PREMIERE_DECISION)
    assert "jamais essayée" in texte and "tu ne sais pas encore si elle est efficace" in texte


# --- traçabilité et explication d'une variation (F6, B4) ------------------------------------


def test_trace_goes_back_from_a_value_to_the_messages_before_it() -> None:
    def convaincu(agent, p: Perception):
        if p.jour == 1:
            message = MessageIntent(Canal.ZONE, "Elle tient vraiment à la baignade.")
            return decision(agent, sens=Sens.EFFICACE, messages=(message,))
        return decision(agent, sens=Sens.EFFICACE)

    def suiveur(agent, p: Perception):
        if p.messages:
            vus = tuple(m.event_id for m in p.messages)
            return decision(agent, sens=Sens.EFFICACE, caused_by=vus)
        return decision(agent, sens=Sens.BANAL if p.jour == 1 else agent.sens)

    agents = [AgentScripte("habitue_soin_01", Role.HABITUE_SOIN, convaincu, toujours=True)]
    agents += [
        AgentScripte(f"sceptique_0{i}", Role.SCEPTIQUE, suiveur, toujours=True) for i in (1, 2, 3)
    ]
    orch, rec = orchestrateur(agents)
    asyncio.run(orch.executer(jours=3))
    calcul = CalculateurDuRun("run_test", graine=1)
    indicateurs = [i for e in rec.events for i in calcul.appliquer(e)]
    serie = serie_de(indicateurs, SENS_PARTAGE, None)
    assert serie[1].payload.valeur < serie[2].payload.valeur == 1.0
    assert jour_de_variation(serie) == 2
    texte = tracer(rec.events, indicateurs, SENS_PARTAGE, None, 2)
    assert "banal → efficace" in texte
    assert "Elle tient vraiment à la baignade." in texte and "habitue_soin_01" in texte


def test_older_runs_get_day_ends_before_each_new_day() -> None:
    events, _, _ = _run_c0(3)
    anciens = [e for e in events if not isinstance(e, ev.ClockEnd)]
    complets = avec_fins_de_jour("run", anciens)
    fins = [e.payload.jour for e in complets if isinstance(e, ev.ClockEnd)]
    assert fins == [1, 2, 3]
    assert avec_fins_de_jour("run", events) is events
