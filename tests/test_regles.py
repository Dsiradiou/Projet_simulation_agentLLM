"""Agents à règles et baseline (étape 13) : météo saisonnière, commerce et flacons, fidèles,
concurrent, baseline C0 (E1), et effets mécaniques des perturbations."""

import asyncio
import random
from collections import Counter
from pathlib import Path
from statistics import mean

from agents.base import Decision, Perception
from agents.factices import population_factice
from agents.population import construire_commerce, construire_population, jours_de_campagne
from agents.prompts import prompt_jour
from agents.regles import AgentAuSeuil, AgentConcurrent, AgentRegleC0
from agents.roles import PALIER_PAR_ROLE, Role
from bus import events as ev
from replay.bilan import COMPARABLES, bilan_depuis
from sim.commerce import Commerce
from sim.config import load_config
from sim.domaine import Action, Canal, Sens, Zone, debut_du_jour
from sim.meteo import MeteoSaison
from sim.orchestrateur import Orchestrateur
from sim.routines import Routines
from sim.run import new_run
from tests.test_orchestrateur import AgentScripte, Recorder

CONFIGS = Path(__file__).parent.parent / "configs"
CONFIG = load_config(CONFIGS / "default.yaml")


def _perturbation(nature: str, zone: Zone | None = None) -> ev.Disturbance:
    return ev.Disturbance(
        run_id="r", sim_time_occurred=0, sim_time_processed=0, source_agent_id="orchestrateur",
        zone=zone, payload=ev.DisturbancePayload(nature=nature, description="Test."),
    )  # fmt: skip


# --- météo saisonnière -----------------------------------------------------------------------


def _bulletins(meteo: MeteoSaison, jours: int) -> list[ev.Weather]:
    return [meteo.evenements_du_jour(j, debut_du_jour(j), "r")[0] for j in range(1, jours + 1)]


def test_weather_is_coherent_seasonal_and_reproducible() -> None:
    bulletins = _bulletins(MeteoSaison(CONFIG.meteo, random.Random(1)), 90)
    assert [b.payload for b in bulletins] == [
        b.payload for b in _bulletins(MeteoSaison(CONFIG.meteo, random.Random(1)), 90)
    ]
    for b in bulletins:  # constat de l'étape 7 : plus de « canicule à UV 6 »
        p = b.payload
        if p.conditions == "canicule":
            assert p.temperature_c >= CONFIG.meteo.seuil_canicule and p.indice_uv >= 9
        elif p.conditions == "pluie":
            assert p.indice_uv <= 2
        elif p.conditions == "nuageux":
            assert 2 <= p.indice_uv <= 4
        else:
            assert 6 <= p.indice_uv <= 9
    debut = mean(b.payload.temperature_c for b in bulletins[:7])
    plateau = mean(b.payload.temperature_c for b in bulletins[30:90])
    assert plateau > debut + 2  # l'été monte, puis reste chaud (pas de rentrée)
    assert {b.payload.conditions for b in bulletins} >= {"ensoleille", "nuageux", "canicule"}


def test_heat_wave_and_storm_change_the_weather() -> None:
    meteo = MeteoSaison(CONFIG.meteo, random.Random(1))
    vague, orage = _perturbation("vague_chaleur"), _perturbation("orage")
    meteo.reagir(vague, 10)
    meteo.reagir(orage, 20)
    bulletins = {b.sim_time_occurred // 1440 + 1: b for b in _bulletins(meteo, 22)}
    for j in (10, 11, 12):
        assert bulletins[j].payload.conditions == "canicule"
        assert bulletins[j].caused_by == (vague.event_id,)  # le bulletin cite sa cause (F6)
    assert bulletins[20].payload.conditions == "pluie"
    assert bulletins[20].caused_by == (orage.event_id,)


# --- commerce ----------------------------------------------------------------------------------


def _commerce(population: int = 100) -> Commerce:
    config = CONFIG.commerce.model_copy(
        update={"stock_initial_pour_100": 2, "reassort_pour_100": 3}
    )
    return Commerce(config, population, {Zone.PHARMACIE: "vendeur_01"})


def test_purchases_follow_stock_and_report_ruptures_once() -> None:
    c = _commerce()
    assert c.acheter("a", Zone.PHARMACIE) == Zone.PHARMACIE
    assert c.acheter("b", Zone.PLAGE) == Zone.CENTRE_COMMERCIAL  # le mieux fourni
    assert c.acheter("c", Zone.PHARMACIE) == Zone.PHARMACIE
    assert c.acheter("d", Zone.PHARMACIE) == Zone.CENTRE_COMMERCIAL
    assert c.acheter("e", Zone.PHARMACIE) is None  # rupture partout
    assert (c.achats, c.achats_manques) == (4, 1)
    soir = c.fin_du_jour(18 * 60, "r")
    stocks = [e for e in soir if isinstance(e, ev.Stock)]
    ruptures = [e for e in soir if isinstance(e, ev.Disturbance)]
    assert {(s.zone, s.payload.quantite) for s in stocks} == {
        (Zone.PHARMACIE, 0),
        (Zone.CENTRE_COMMERCIAL, 0),
    }
    assert {r.zone for r in ruptures} == {Zone.PHARMACIE, Zone.CENTRE_COMMERCIAL}
    assert all(r.caused_by for r in ruptures)  # la rupture remonte au niveau de stock (F6)
    assert c.fin_du_jour(18 * 60, "r") == []  # annoncée une seule fois
    assert c.debut_du_jour(2, 0, "r") == [] and c.rates_veille == {"e"}


def test_weekly_restock_and_injected_rupture() -> None:
    c = _commerce()
    c.acheter("a", Zone.PHARMACIE)
    c.acheter("b", Zone.PHARMACIE)  # 2 ventes à la pharmacie cette semaine
    reassorts = c.debut_du_jour(5, 0, "r")  # vendredi
    assert [e.source_agent_id for e in reassorts] == ["vendeur_01", "commerce"]
    # Commande = ventes × 1,2 (2 → 2), mais jamais sous le minimum de 3.
    assert c.stock == {Zone.PHARMACIE: 0 + 3, Zone.CENTRE_COMMERCIAL: 2 + 3}
    for i in range(3):
        c.acheter(f"x{i}", Zone.CENTRE_COMMERCIAL)
    c.acheter("y", Zone.PHARMACIE)
    c.debut_du_jour(12, 0, "r")  # 3 vendus au centre commercial : commande de 4 (3 × 1,2 arrondi)
    assert c.stock == {Zone.PHARMACIE: 2 + 3, Zone.CENTRE_COMMERCIAL: 2 + 4}
    c.reagir(_perturbation("rupture_stock", Zone.PHARMACIE), 6)
    assert c.stock[Zone.PHARMACIE] == 0 and c.stock[Zone.CENTRE_COMMERCIAL] == 6  # constat 9
    c.reagir(_perturbation("promotion_concurrente"), 21)
    assert c.promotion_concurrente(27) and not c.promotion_concurrente(28)


def _achat(sens: Sens):
    """Script d'agent de test : il veut acheter chaque fois qu'il décide."""

    def script(agent, perception):
        appel = ev.LlmCall(modele="test", tokens_entree=1, tokens_sortie=1, duree_s=0)
        return Decision(Action.ACHETER, sens, "x", True, ev.Moteur.LLM, agent.palier, appel)

    return script


def _orchestrateur(agents, commerce):
    recorder = Recorder()
    orch = Orchestrateur(CONFIG, new_run(CONFIG, CONFIGS / "default.yaml"), agents, [], recorder,
                         commerce=commerce)  # fmt: skip
    return orch, recorder


def test_bottle_runs_out_and_must_be_bought_again() -> None:
    achete = _achat(Sens.EFFICACE)
    agent = AgentScripte("sceptique_01", Role.SCEPTIQUE, achete)
    agent.declencheurs = CONFIG.declencheurs.model_copy(update={"delai_reflexion_ecart": 0})
    orch, recorder = _orchestrateur([agent], _commerce())
    duree = CONFIG.commerce.duree_flacon_jours
    asyncio.run(orch.executer(duree + 2))
    motifs = [d.payload.motif for d in recorder.of(ev.AgentDecision)]
    assert motifs[0] is ev.Motif.PREMIERE_DECISION
    assert ev.Motif.FLACON_VIDE in motifs  # au bout de 12 jours, il faut racheter
    jour_vide = [d.sim_time_occurred // 1440 + 1 for d in recorder.of(ev.AgentDecision)
                 if d.payload.motif is ev.Motif.FLACON_VIDE][0]  # fmt: skip
    assert jour_vide == 1 + duree
    assert orch.bilan.achats == 2


def test_failed_purchase_keeps_the_decision_but_not_the_product() -> None:
    achete = _achat(Sens.TENDANCE)
    agents = [AgentScripte(f"sceptique_{i:02d}", Role.SCEPTIQUE, achete) for i in range(1, 7)]
    orch, recorder = _orchestrateur(agents, _commerce())  # 4 flacons pour 6 acheteurs
    asyncio.run(orch.executer(2))
    jour1 = [d for d in recorder.of(ev.AgentDecision) if d.sim_time_occurred < 1440]
    assert sorted(d.payload.porteur for d in jour1) == [False, False, True, True, True, True]
    assert all(d.payload.action is Action.ACHETER for d in jour1)
    rates = {d.source_agent_id for d in jour1 if not d.payload.porteur}
    assert orch.commerce.rates_veille == rates  # ils le sauront le lendemain
    assert {d.zone for d in recorder.of(ev.Disturbance)} == {Zone.PHARMACIE, Zone.CENTRE_COMMERCIAL}


def test_prompt_mentions_empty_bottle_and_missed_purchase() -> None:
    p = Perception(5, 0, Zone.PLAGE, flacon_vide=True, achat_rate=True)
    texte, _ = prompt_jour(p, None, [], CONFIG.monde, ev.Motif.FLACON_VIDE)
    assert "flacon de Vaelys est fini" in texte and "plus en rayon" in texte


# --- agents à règles -------------------------------------------------------------------------


def _p(jour: int = 5, part: float = 0.0, **kw) -> Perception:
    return Perception(jour, debut_du_jour(jour), Zone.PLAGE, part_porteurs_zone=part, **kw)


def test_loyal_customers_need_more_and_more_during_a_promotion() -> None:
    bonus = CONFIG.regles.bonus_fidelite
    role = Role.FIDELE_AUTRE_MARQUE
    fidele = AgentAuSeuil(
        f"{role}_01", role, Zone.PLAGE, random.Random(0), 0.3, CONFIG.regles, bonus
    )
    assert asyncio.run(fidele.decider(_p(part=0.5))).porteur is False  # 0,5 < 0,3 + 0,25
    assert asyncio.run(fidele.decider(_p(part=0.6))).porteur is True
    assert asyncio.run(fidele.decider(_p(part=0.6, promotion_concurrente=True))).porteur is False
    adopte = asyncio.run(fidele.decider(_p(part=0.6)))
    fidele.noter_decision(adopte, _p(part=0.6))
    retour = asyncio.run(fidele.decider(_p(part=0.4)))  # la mode recule de 20 points
    assert (retour.action, retour.porteur) == (Action.ABANDONNER, False)


def test_competitor_speaks_only_on_its_calendar() -> None:
    concurrent = AgentConcurrent("marque_concurrente_01", Role.MARQUE_CONCURRENTE, Zone.PLAGE,
                                 random.Random(0), CONFIG.monde)  # fmt: skip
    assert concurrent.doit_decider(_p(1))
    concurrent.noter_decision(asyncio.run(concurrent.decider(_p(1))), _p(1))
    jours = [j for j in range(2, 60) if concurrent.doit_decider(_p(j))]
    assert jours == [21, 45]
    annonce = asyncio.run(concurrent.decider(_p(21)))
    assert annonce.messages[0].canal is Canal.RESEAUX and "Zelvane" in annonce.messages[0].contenu
    assert annonce.porteur is False


def _c0(seuil: float = 0.5, **regles) -> AgentRegleC0:
    r = CONFIG.regles.model_copy(update=regles)
    role = Role.CREATEUR_CONTENU
    return AgentRegleC0(
        f"{role}_01", role, Zone.PLAGE, random.Random(3), seuil, r, CONFIG.monde, [], {7}, 5
    )


def test_baseline_adopts_by_imitation_and_external_influence() -> None:
    imitation = asyncio.run(_c0().decider(_p(part=0.55)))
    assert (imitation.action, imitation.sens, imitation.porteur) == (
        Action.ACHETER,
        Sens.TENDANCE,
        True,
    )
    assert imitation.messages and imitation.messages[0].canal is Canal.RESEAUX  # rôle créateur
    assert asyncio.run(_c0(p_externe=0.0).decider(_p(part=0.1))).porteur is False
    campagne = asyncio.run(_c0(p_campagne=1.0).decider(_p(7, part=0.1)))  # jour de communiqué
    assert (campagne.sens, campagne.porteur) == (Sens.EFFICACE, True)


def test_baseline_abandons_when_the_fashion_recedes() -> None:
    agent = _c0(p_abandon_recul=1.0)
    adoption = asyncio.run(agent.decider(_p(part=0.6)))
    agent.noter_decision(adoption, _p(part=0.6))
    abandon = asyncio.run(agent.decider(_p(10, part=0.3)))  # 30 points de recul
    assert (abandon.action, abandon.sens, abandon.porteur) == (
        Action.ABANDONNER,
        Sens.DEPASSE,
        False,
    )


def test_baseline_uses_the_same_world_and_no_model() -> None:
    base = load_config(CONFIGS / "baseline.yaml")
    agents, acteurs = construire_population(base, routeur=None)
    assert not any(a.palier for a in agents)  # aucun modèle à charger
    assert {type(a).__name__ for a in agents if a.role in PALIER_PAR_ROLE} == {"AgentRegleC0"}
    assert [a.actor_id for a in acteurs] == ["meteo", "marque_concurrente_01"]
    assert jours_de_campagne(base) == {1, 14, 25, 30, 35, 75}
    factices, _ = population_factice(CONFIG)
    assert [(a.agent_id, a.zone) for a in agents] == [(a.agent_id, a.zone) for a in factices]


def _run_c0(jours: int) -> tuple[list, dict]:
    base = load_config(CONFIGS / "baseline-dev.yaml")
    agents, acteurs = construire_population(base, routeur=None)
    recorder = Recorder()
    orch = Orchestrateur(base, new_run(base, CONFIGS / "baseline-dev.yaml"), agents, acteurs,
                         recorder, routines=Routines(base.routines, base.run.seed),
                         commerce=construire_commerce(base, agents))  # fmt: skip
    bilan = asyncio.run(orch.executer(jours))
    return recorder.events, bilan, len(agents)


def test_baseline_run_is_reproducible_and_replayable() -> None:
    events, bilan, population = _run_c0(30)
    autres, _, _ = _run_c0(30)
    trace = lambda es: [(e.type, e.source_agent_id, e.sim_time_occurred) for e in es]  # noqa: E731
    assert trace(events) == trace(autres)
    assert bilan.appels_llm == 0 and bilan.achats > 0 and bilan.reassorts > 0
    rejeu = bilan_depuis(events, population)
    assert rejeu == {k: getattr(bilan, k) for k in COMPARABLES}
    porteurs = Counter(
        d.payload.porteur for d in (e for e in events if isinstance(e, ev.AgentDecision))
    )
    assert porteurs[True] and porteurs[False]
    motifs = {d.payload.motif for d in events if isinstance(d, ev.AgentDecision)}
    assert ev.Motif.FLACON_VIDE in motifs


def test_baseline_and_mixed_configurations_draw_the_same_thresholds() -> None:
    """C0 et C1 de même graine : mêmes agents, mêmes zones, mêmes seuils ; seule la décision
    change (E1 : « le même monde, soumis aux mêmes scénarios »)."""
    from llm.routeur import Routeur
    from tests.test_llm import ClientScripte

    c0, _ = construire_population(load_config(CONFIGS / "baseline.yaml"), routeur=None)
    c1, _ = construire_population(CONFIG, Routeur(ClientScripte(), CONFIG.llm))
    cle = lambda a: (a.agent_id, a.zone, getattr(a, "seuil", None))  # noqa: E731
    assert [cle(a) for a in c0] == [cle(a) for a in c1]
