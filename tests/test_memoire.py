"""Mémoire, oubli et lassitude (début de l'étape 16) : notoriété A, lassitude L, score de sélection
du RAG, mémoire collective, sens partagé sans les agents qui ont oublié le produit."""

import asyncio
import random
from collections import Counter
from dataclasses import replace
from uuid import uuid4

from agents.base import Decision, MessageIntent, Perception, SouvenirRetenu
from agents.notoriete import EtatProduit, etat_initial, niveau_de_lassitude, vivre_jour
from agents.population import construire_population
from agents.prompts import prompt_jour
from agents.regles import AgentRegleC0
from agents.roles import Role
from bus import events as ev
from indicators.calcul import OUBLI, SENS_PARTAGE, EtatPopulation
from rag.base import Memoires, Passage, client_memoire_vive
from rag.collectif import MemoireCollective, faits_du_jour
from rag.recuperation import souvenir
from rag.selection import Candidat, Historique, choisir
from replay.bilan import COMPARABLES, bilan_depuis
from sim.config import Config, MemoireConfig
from sim.domaine import Action, Canal, Sens, Zone, debut_du_jour
from sim.orchestrateur import Orchestrateur
from tests.test_indicateurs import _decision
from tests.test_orchestrateur import RUN, AgentScripte, Recorder, config, decision
from tests.test_rag import CONFIG, MONDE, _embeddeur, _message, _perception
from tests.test_regles import _run_c0

OUBLI_ACTIF = CONFIG.oubli
LASSITUDE_ACTIVE = CONFIG.lassitude


def _p(jour: int, messages=(), **kw) -> Perception:
    return Perception(jour, debut_du_jour(jour) + 600, Zone.PLAGE, messages=messages, **kw)


def _zone(de: str, contenu: str, jour: int, canal=Canal.RESEAUX, zone="plage") -> ev.AgentMessage:
    t = debut_du_jour(jour) + 600
    return ev.AgentMessage(
        run_id="r", sim_time_occurred=t, sim_time_processed=t, source_agent_id=de,
        zone=zone if canal is Canal.ZONE else None,
        payload=ev.AgentMessagePayload(
            canal=canal, contenu=contenu, livraison=t + 60,
            destinataires=("sceptique_01",) if canal is Canal.DIRECT else (),
        ),
    )  # fmt: skip


# --- notoriété A et lassitude L ----------------------------------------------------------


def test_notoriety_fades_with_the_half_life_and_a_carrier_never_forgets() -> None:
    etat = EtatProduit(1.0, connu=True, deja_connu=True, demi_vie=10)
    for jour in range(1, 11):
        vivre_jour(etat, _p(jour), porteur=False, contacts=[], oubli=OUBLI_ACTIF,
                   lassitude=LASSITUDE_ACTIVE)  # fmt: skip
    assert abs(etat.notoriete - 0.5) < 1e-5  # une demi-vie
    for jour in range(11, 25):
        vivre_jour(etat, _p(jour), porteur=False, contacts=[], oubli=OUBLI_ACTIF,
                   lassitude=LASSITUDE_ACTIVE)  # fmt: skip
        if etat.transition is ev.Notoriete.OUBLI:
            break
    assert not etat.connu and etat.notoriete < OUBLI_ACTIF.seuil
    porteur = EtatProduit(1.0, connu=True, deja_connu=True, demi_vie=2)
    for jour in range(1, 30):
        vivre_jour(porteur, _p(jour), porteur=True, contacts=[], oubli=OUBLI_ACTIF,
                   lassitude=LASSITUDE_ACTIVE)  # fmt: skip
    assert porteur.notoriete == 1.0 and porteur.connu  # décision du binôme : il n'oublie pas


def test_seeing_carriers_brings_the_product_back_up_to_a_daily_cap() -> None:
    """M2 (4 nov.) : dix porteurs autour de lui ne valent pas plus que trois."""
    etat = EtatProduit(0.0, connu=False, deja_connu=True, demi_vie=10)
    vivre_jour(etat, _p(5, porteurs_vus=10), porteur=False, contacts=[], oubli=OUBLI_ACTIF,
               lassitude=LASSITUDE_ACTIVE)  # fmt: skip
    assert etat.notoriete == OUBLI_ACTIF.renforts.porteurs_vus_max == 0.3


def test_an_exposure_brings_the_product_back_and_counts_once() -> None:
    etat = EtatProduit(0.05, connu=False, deja_connu=True, demi_vie=10)
    proche = _message("pharmacien_02", "Vaelys tient bien à la plage.", 5)
    vivre_jour(etat, _p(5, (proche,)), porteur=False, contacts=["pharmacien_02"],
               oubli=OUBLI_ACTIF, lassitude=LASSITUDE_ACTIVE)  # fmt: skip
    assert etat.transition is ev.Notoriete.REDECOUVERTE and etat.connu
    assert etat.causes == (proche.event_id,)  # F6 : la redécouverte cite son exposition
    avant = etat.notoriete
    vivre_jour(
        etat,
        _p(6, (proche,)),
        porteur=False,
        contacts=["pharmacien_02"],
        oubli=OUBLI_ACTIF,
        lassitude=LASSITUDE_ACTIVE,
    )  # même message, relu
    assert etat.notoriete < avant and etat.transition is None  # pas compté deux fois


def test_initial_awareness_is_individual_reproducible_and_off_by_default() -> None:
    connus = [etat_initial(OUBLI_ACTIF, 42, f"sceptique_{i:03d}").connu for i in range(400)]
    assert 0.15 < sum(connus) / len(connus) < 0.25  # connus_jour_1 : 0,2
    assert connus == [etat_initial(OUBLI_ACTIF, 42, f"sceptique_{i:03d}").connu for i in range(400)]
    sans = Config.model_validate(CONFIG.model_dump(mode="json") | {"oubli": {}})
    assert not sans.oubli.actif and etat_initial(sans.oubli, 42, "x").connu  # comme avant


def test_same_seed_gives_same_initial_notoriety_in_c0_and_c1() -> None:
    """E1 : C0 et C1 tirent la notoriété de la même façon ; seule la décision change."""
    c1 = config()
    c0 = config(agents={"moteur": "regles"})
    llm, _ = construire_population(c1, routeur=object())  # aucun appel à la construction
    regles, _ = construire_population(c0, routeur=None)

    def etats(agents: list) -> dict:  # la marque concurrente n'a pas de notoriété
        return {a.agent_id: (a.produit.connu, a.produit.demi_vie) for a in agents if a.produit}

    assert etats(llm) == etats(regles)


def test_forgotten_agents_are_not_triggered_and_rediscovery_skips_the_delay() -> None:
    agent = AgentScripte("sceptique_01", Role.SCEPTIQUE, lambda a, p: decision(a), toujours=False)
    agent.declencheurs = CONFIG.declencheurs
    agent.produit = EtatProduit(0.0, connu=False, deja_connu=False)
    message = _message("pharmacien_02", "Vaelys ?", 1)
    assert not agent.doit_decider(_p(1, (message,)))  # inconnu : rien ne le déclenche
    agent.produit.connu, agent.produit.transition = True, ev.Notoriete.DECOUVERTE
    assert agent.doit_decider(_p(2)) and agent.motif is ev.Motif.PREMIERE_DECISION
    agent.noter_decision(decision(agent), _p(2))
    agent.produit.connu, agent.produit.transition = False, ev.Notoriete.OUBLI
    assert not agent.doit_decider(_p(30))  # oublié : plus de relance
    agent.produit.connu, agent.produit.transition = True, ev.Notoriete.REDECOUVERTE
    assert agent.doit_decider(_p(31, (message,))) and agent.motif is ev.Motif.REDECOUVERTE
    agent.produit.transition = None
    assert agent.doit_decider(_p(45)) and agent.motif is ev.Motif.RELANCE  # ancien « lassitude »


def test_lassitude_grows_with_exposure_and_shows_in_the_prompt() -> None:
    etat = EtatProduit(1.0, connu=True, deja_connu=True)
    messages = tuple(_message(f"habitue_soin_0{i}", "Vaelys partout !", 1) for i in range(3))
    for jour in range(1, 15):
        vivre_jour(etat, _p(jour, messages, part_porteurs_zone=0.8), porteur=True, contacts=[],
                   oubli=OUBLI_ACTIF, lassitude=LASSITUDE_ACTIVE)  # fmt: skip
        messages = tuple(m.model_copy(update={"event_id": uuid4()}) for m in messages)  # neufs
    assert etat.lassitude > LASSITUDE_ACTIVE.niveaux[1]
    niveau = niveau_de_lassitude(etat.lassitude, LASSITUDE_ACTIVE.niveaux)
    prompt, _ = prompt_jour(_p(15, niveau_lassitude=niveau), None, [], MONDE)
    assert "Vaelys" in prompt and ("se répètent" in prompt or "en as assez" in prompt)


def test_c0_carrier_abandons_out_of_lassitude_without_extra_draws_otherwise() -> None:
    def agent(abandon: float) -> AgentRegleC0:
        a = AgentRegleC0("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(3), 0.9,
                         CONFIG.regles, MONDE, [], set(), 0, abandon)  # fmt: skip
        a.derniere_decision = Decision(Action.APPLIQUER, Sens.TENDANCE, "x", True, ev.Moteur.REGLE)
        return a

    lasse = asyncio.run(agent(1.0).decider(_p(5, part_porteurs_zone=0.5, lassitude=1.0)))
    assert lasse.action is Action.ABANDONNER and lasse.sens is Sens.DEPASSE
    sans, temoin = agent(1.0), agent(0.0)
    asyncio.run(sans.decider(_p(5, part_porteurs_zone=0.5, lassitude=0.0)))
    asyncio.run(temoin.decider(_p(5, part_porteurs_zone=0.5, lassitude=0.0)))
    assert sans.rng.getstate() == temoin.rng.getstate()  # L = 0 : la baseline ne bouge pas


def test_run_publishes_threshold_crossings_and_replay_recounts_them() -> None:
    events, bilan, population = _run_c0(40)
    notoriete = [e for e in events if isinstance(e, ev.AgentNotoriete)]
    natures = Counter(e.payload.nature for e in notoriete)
    assert natures[ev.Notoriete.DECOUVERTE] and natures[ev.Notoriete.OUBLI]
    assert (bilan.decouvertes, bilan.oublis) == (
        natures[ev.Notoriete.DECOUVERTE],
        natures[ev.Notoriete.OUBLI],
    )
    assert bilan_depuis(events, population) == {k: getattr(bilan, k) for k in COMPARABLES}
    decisions = [e for e in events if isinstance(e, ev.AgentDecision)]
    jour_1 = {e.source_agent_id for e in decisions if e.sim_time_occurred < debut_du_jour(2)}
    assert len(jour_1) < population / 2  # au jour 1, seule une part connaît le produit
    decouvertes = [e for e in notoriete if e.payload.nature is not ev.Notoriete.OUBLI]
    assert any(e.caused_by for e in decouvertes)  # une découverte cite ses expositions (F6)


# --- sélection : le score ------------------------------------------------------------------


def _c(source: str, distance: float, jour: int = 1, vecteur=(1.0, 0.0), **kw) -> Candidat:
    return Candidat(Passage(source, source, jour, "souvenir", distance), tuple(vecteur), **kw)


def test_each_term_of_the_score_moves_the_choice() -> None:
    r = MemoireConfig()
    h = Historique()
    proche = _c("a", 0.1, vecteur=(1, 0, 0))
    efface = _c("b", 0.1, retention=0.2, vecteur=(0, 1, 0))
    assert [c.passage.source for c in choisir([efface, proche], 1, 10, r, h)] == ["a"]
    marquant = _c("c", 0.3, importance=1.0, retention=1.0, vecteur=(0, 0, 1))
    banal = _c("d", 0.2, importance=0.0, retention=1.0, vecteur=(1, 1, 0))
    assert choisir([banal, marquant], 1, 10, r, h)[0].passage.source == "c"
    h.noter(9, [banal])  # montré hier : déjà vu
    assert choisir([banal, _c("e", 0.25, vecteur=(0, 1, 1))], 1, 10, r, h)[0].passage.source == "e"
    note = choisir([proche], 1, 10, r, h)[0].passage
    assert note.score is not None and note.retention == 1.0
    assert choisir([proche], 1, 10, r, h, acces=0.0) == []  # produit oublié : rien ne remonte


def test_duplicates_are_skipped_and_lassitude_penalises_what_was_seen() -> None:
    r = MemoireConfig()
    jumeau1, jumeau2 = _c("a", 0.1, vecteur=(1, 0)), _c("b", 0.11, vecteur=(1, 0.01))
    autre = _c("c", 0.4, vecteur=(0, 1))
    choisis = choisir([jumeau1, jumeau2, autre], 2, 10, r, Historique())
    assert [c.passage.source for c in choisis] == ["a", "c"]  # b double a
    h = Historique()
    h.noter(9, [_c("vu", 0.0, vecteur=(1, 0))])
    neuf = _c("neuf", 0.3, vecteur=(0, 1))
    redite = _c("redite", 0.2, vecteur=(1, 0.05))
    assert choisir([redite, neuf], 1, 10, r, h, lassitude=0.0)[0].passage.source == "redite"
    assert choisir([redite, neuf], 1, 10, r, h, lassitude=1.0)[0].passage.source == "neuf"


def test_old_memories_fade_out_but_stay_in_base_and_a_recall_refreshes_them() -> None:
    memoires = Memoires(client_memoire_vive(), _embeddeur(), "oubli")
    asyncio.run(memoires.ajouter("sceptique_01", 1, "Vaelys colle au sable.", 1))
    (vecteur,) = asyncio.run(memoires.embeddeur.questions(["Vaelys sable"]))
    reglage = MemoireConfig()
    assert memoires.candidats(vecteur, "sceptique_01", 5, reglage)
    assert memoires.candidats(vecteur, "sceptique_01", 60, reglage) == []  # oublié
    assert memoires.rechercher_vecteur(vecteur, "sceptique_01", 60)  # mais toujours en base
    memoires.rappeler(["memoire:sceptique_01:j1"], 50)
    (rappele,) = memoires.candidats(vecteur, "sceptique_01", 60, reglage)
    assert 0.1 < rappele.retention < 1  # sa rétention compte depuis le rappel


def test_importance_counts_a_change_of_mind_and_a_missed_purchase() -> None:
    avant = Decision(Action.IGNORER, Sens.BANAL, "Bof.", False, ev.Moteur.LLM)
    apres = Decision(Action.ACHETER, Sens.TENDANCE, "Tout le monde l'a.", True, ev.Moteur.LLM)
    _, stable = souvenir(_perception(3), avant, MONDE, avant)
    _, revirement = souvenir(_perception(3), apres, MONDE, avant)
    _, rate = souvenir(replace(_perception(3), achat_rate=True), avant, MONDE, avant)
    assert (stable, revirement, rate) == (1, 3, 3)


# --- mémoire collective ---------------------------------------------------------------------


def _collectif() -> tuple[MemoireCollective, list]:
    publication = _zone("createur_contenu_01", "Vaelys, la crème de l'été !", 3)
    propos = _zone("baigneur_occasionnel_02", "Vaelys sent bon le monoï.", 3, Canal.ZONE)
    direct = _zone("habitue_soin_01", "Secret : j'adore Vaelys.", 3, Canal.DIRECT)
    t = debut_du_jour(3) + 500
    article = ev.Disturbance(
        run_id="r", sim_time_occurred=t, sim_time_processed=t, source_agent_id="orchestrateur",
        payload=ev.DisturbancePayload(nature="article_presse", description="Vaelys en rupture."),
    )  # fmt: skip
    sens = {
        "sceptique_02": Sens.TENDANCE,
        "sceptique_03": Sens.BANAL,
        "sceptique_04": Sens.TENDANCE,
    }
    decisions = [_decision(agent_id, s, jour=3) for agent_id, s in sens.items()]
    events = [publication, propos, direct, article, *decisions]
    memoire = MemoireCollective(client_memoire_vive(), _embeddeur(), "collectif")
    asyncio.run(memoire.consigner(faits_du_jour(3, events)))
    return memoire, events


def test_public_facts_of_the_day_exclude_private_messages_and_summarise_each_place() -> None:
    _, events = _collectif()
    faits = faits_du_jour(3, events)
    textes = " ".join(f.texte for f in faits)
    assert "la crème de l'été" in textes and "monoï" in textes and "rupture" in textes
    assert "Secret" not in textes  # un message direct reste privé
    (resume,) = [f for f in faits if ":resume:" in f.source]
    assert (
        "3 avis donnés" in resume.texte and "tendance" in resume.texte and "2 sur 3" in resume.texte
    )
    assert all(f.cause for f in faits if f is not resume)  # F6 : chaque fait cite son événement


def test_collective_memory_respects_date_social_reach_and_perception() -> None:
    memoire, events = _collectif()
    (vecteur,) = asyncio.run(memoire.embeddeur.questions(["Vaelys crème plage"]))
    r = MemoireConfig()

    def lus(jour=4, lieu="plage", abonnements=(), contacts=(), percus=frozenset(), agent="x_01"):
        return {c.passage.source.split(":")[2] if "resume" not in c.passage.source else "resume"
                for c in memoire.candidats(vecteur, agent_id=agent, jour=jour, lieu=lieu,
                                           abonnements=tuple(abonnements), contacts=list(contacts),
                                           deja_percus=set(percus), reglage=r)}  # fmt: skip

    publication, propos, _, article = events[:4]
    assert lus(jour=3) == set()  # le jour même : déjà dans la perception (C6)
    assert lus() == {str(propos.event_id), str(article.event_id), "resume"}  # pas abonné
    assert str(publication.event_id) in lus(abonnements=["createur_contenu_01"])
    assert lus(lieu="piscine") == {str(article.event_id)}  # ni les propos ni le résumé de la plage
    assert str(propos.event_id) not in lus(percus={str(propos.event_id)})
    assert str(propos.event_id) not in lus(agent="baigneur_occasionnel_02")  # ses propres mots
    (de_proche,) = [c for c in memoire.candidats(
        vecteur, agent_id="x_01", jour=4, lieu="piscine", abonnements=(),
        contacts=["createur_contenu_01"], deja_percus=set(), reglage=r)
        if c.passage.source.endswith(str(publication.event_id))]  # fmt: skip
    assert de_proche.proximite == r.proximite.proche


def test_agent_reads_collective_memory_and_the_control_run_writes_none() -> None:
    from tests.test_rag import _recuperateur

    rec = _recuperateur()
    rec.collectif, _ = _collectif()
    rec.config = rec.config.model_copy(update={"collectif": 2})
    contexte = asyncio.run(rec.contexte("sceptique_01", Role.SCEPTIQUE, _perception(4)))
    assert contexte.collectif and all(s.startswith("collectif:") for s in contexte.sources[-2:])
    prompt, _ = prompt_jour(_perception(4), None, [], MONDE, contexte=contexte)
    assert "Ce qui s'est dit en ville" in prompt

    class Consigne:
        def __init__(self) -> None:
            self.faits: list = []

        async def consigner(self, faits) -> int:
            self.faits += faits
            return len(faits)

    def parle(agent: AgentScripte, perception: Perception) -> Decision:
        return replace(decision(agent), messages=(MessageIntent(Canal.ZONE, "Vaelys sent bon."),))

    for diffusion in (True, False):
        cfg = config(simulation={"diffusion": diffusion})
        consigne = Consigne()
        agents = [AgentScripte("sceptique_01", Role.SCEPTIQUE, parle, toujours=True)]
        orch = Orchestrateur(cfg, RUN, agents, [], Recorder(), collectif=consigne)
        asyncio.run(orch.executer(2))
        assert bool(consigne.faits) is diffusion  # témoin B3 : mémoire collective vide


# --- sens partagé sans les agents qui ont oublié ------------------------------------------


def test_forgotten_agents_leave_the_shared_meaning_and_are_counted_apart() -> None:
    etat = EtatPopulation()
    a, b = _decision("sceptique_01", Sens.TENDANCE), _decision("sceptique_02", Sens.BANAL)
    for e in (a, b):
        etat.appliquer(e)
    t = debut_du_jour(2) + 600
    oubli = ev.AgentNotoriete(run_id="r", sim_time_occurred=t, sim_time_processed=t,
                              source_agent_id="sceptique_02",
                              payload=ev.AgentNotorietePayload(nature="oubli", notoriete=0.1,
                                                               lassitude=0.0))  # fmt: skip
    etat.appliquer(oubli)
    mesures = {(m.nom, m.groupe): m for m in etat.mesurer(2, graine=1)}
    assert mesures[(SENS_PARTAGE, None)].caused_by == (a.event_id,)  # b ne compte plus
    assert mesures[(SENS_PARTAGE, None)].valeur == 1.0
    assert mesures[(OUBLI, None)].valeur == 0.5 and mesures[(OUBLI, None)].caused_by == (
        oubli.event_id,
    )
    sans_suivi = EtatPopulation()
    sans_suivi.appliquer(a)
    assert OUBLI not in {m.nom for m in sans_suivi.mesurer(1, graine=1)}  # runs d'avant


# --- consolidation et souvenirs publiés -------------------------------------------------------


def test_a_weekly_review_sums_up_past_weeks_and_is_read_from_the_next_one() -> None:
    """M6 (4 nov.) : bilan par règle de chaque semaine passée, daté de son dernier jour."""
    from tests.test_rag import _recuperateur

    rec = _recuperateur()
    choix = [
        (2, Decision(Action.IGNORER, Sens.BANAL, "Bof.", False, ev.Moteur.LLM)),
        (5, Decision(Action.ACHETER, Sens.TENDANCE, "Tout le monde l'a.", True, ev.Moteur.LLM)),
        (6, Decision(Action.APPLIQUER, Sens.TENDANCE, "Je l'essaie.", True, ev.Moteur.LLM)),
    ]
    for jour, d in choix:
        message = _message("pharmacien_02", "Vaelys tient bien.", jour)
        retenu = asyncio.run(rec.retenir("sceptique_01", _perception(jour, (message,)), d))
        assert retenu.nature is ev.NatureSouvenir.EPISODE and retenu.jour == jour
        assert asyncio.run(rec.consolider("sceptique_01", jour + 1)) == ()  # semaine en cours
    (bilan,) = asyncio.run(rec.consolider("sceptique_01", 10))
    assert (bilan.source, bilan.jour, bilan.nature) == ("bilan:sceptique_01:j7", 7, "bilan")
    assert bilan.texte.startswith("Semaine 1 (jours 1 à 7) : 3 décisions.")
    assert "ignorer, acheter, appliquer" in bilan.texte and "« tendance »" in bilan.texte
    assert "Je l'utilise." in bilan.texte and "pharmacien_02 (3)" in bilan.texte
    assert asyncio.run(rec.consolider("sceptique_01", 11)) == ()  # une seule fois
    (vecteur,) = asyncio.run(rec.memoires.embeddeur.questions(["Vaelys"]))
    lus = {p.source for p in rec.memoires.rechercher_vecteur(vecteur, "sceptique_01", 8, k=10)}
    assert "bilan:sceptique_01:j7" in lus  # lisible à partir du jour 8 (C6)
    lus = {p.source for p in rec.memoires.rechercher_vecteur(vecteur, "sceptique_01", 7, k=10)}
    assert "bilan:sceptique_01:j7" not in lus


def test_memories_are_published_with_their_decision_and_recounted_by_the_replay() -> None:
    """M7 (4 nov.) : le texte des souvenirs est dans le journal du run (D2, E3)."""
    souvenirs = (
        SouvenirRetenu("bilan:sceptique_01:j7", ev.NatureSouvenir.BILAN, 7, "Semaine 1…", 3),
        SouvenirRetenu("memoire:sceptique_01:j1", ev.NatureSouvenir.EPISODE, 1, "Jour 1…", 1),
    )

    def retient(agent: AgentScripte, perception: Perception) -> Decision:
        return replace(decision(agent), souvenirs=souvenirs)

    recorder = Recorder()
    agents = [AgentScripte("sceptique_01", Role.SCEPTIQUE, retient, toujours=True)]
    orch = Orchestrateur(config(), RUN, agents, [], recorder)
    asyncio.run(orch.executer(2))
    (premiere, _) = recorder.of(ev.AgentDecision)
    publies = recorder.of(ev.AgentSouvenir)
    assert [e.payload.source for e in publies[:2]] == [s.source for s in souvenirs]
    assert publies[0].caused_by == (premiere.event_id,)  # F6 : rattaché à sa décision
    assert (orch.bilan.souvenirs, orch.bilan.bilans_semaine) == (2, 2)
    assert bilan_depuis(recorder.events, 1) == {k: getattr(orch.bilan, k) for k in COMPARABLES}
