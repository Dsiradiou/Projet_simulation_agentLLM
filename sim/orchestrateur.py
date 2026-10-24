"""Orchestrateur (C4) : fait avancer le temps jour par jour et déclenche les agents, en mode
synchrone ou asynchrone (`simulation.mode`).

Avant chaque jour, l'orchestrateur relève les commandes et injections de l'interface (D3) :
pause et reprise, arrêt propre, perturbations à appliquer ce jour-là.

Déroulé d'un jour d :
1. début du jour (00:00) : `horloge.pas`, les événements des acteurs du monde (météo...), puis
   les perturbations injectées depuis la veille, éventuellement limitées à une zone ; à 08:00,
   les déplacements de routine (règles, selon le rôle et le temps) ;
2. les décisions, selon le mode (ci-dessous) ;
3. stocks du soir, écriture de l'état courant dans Redis, puis `horloge.fin_jour`.

Mode synchrone :
- perception de chaque agent à 10:00, construite sur l'état de la veille : c'est la barrière
  synchrone, aucun agent ne voit ce que les autres décident le jour même ;
- décision des seuls agents déclenchés (règle 10), les agents à règles d'abord, puis les
  agents LLM regroupés par palier dans l'ordre de `ordre_paliers` ; le modèle d'un palier est
  chargé avant son groupe (`preparer`), pour mesurer la bascule à part ;
- publication, dans l'ordre des identifiants d'agents pour un run reproductible : décisions,
  incidents de la décision (relance, repli), déplacements, messages (datés de leur livraison).

Mode asynchrone (étape 15) : pas de barrière, chacun décide à son rythme.
- Le 8b ne tient pas en VRAM avec les deux autres : la journée garde deux phases, 1.7b et 4b
  ensemble puis 8b (ordre alterné chaque jour). L'heure de chaque phase part de 08:30 et suit
  le temps réel de calcul, hors chargement de modèle (`HorlogePhase`).
- Dans une phase, chaque agent LLM passe quand une place se libère pour son modèle, dans un
  ordre tiré avec la graine. Sa perception est prise à cet instant (`sim_time_occurred`) ; sa
  décision est datée de la fin de son appel (`sim_time_processed`) et s'applique aussitôt :
  achat, messages livrés le jour même si leur délai le permet.
- Les agents à règles passent à une heure tirée dans la matinée, pendant la première phase.
- Un agent ne voit que ce qui est daté avant son passage, quelle que soit la phase qui l'a
  calculé (C6). Un message calculé dans une phase suivante mais livré avant son passage est une
  « information manquée » : il la lira à sa prochaine décision. Ces cas sont comptés et cités
  par `horloge.fin_jour`.
- Une seule décision par agent et par jour, comme en synchrone : seul le modèle de temps change.
- Les événements de la journée sont publiés à sa fin, dans l'ordre de leur heure simulée.
"""

import asyncio
import dataclasses
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from agents.base import Agent, Decision, Perception, WorldActor
from agents.roles import Palier
from bus.events import (
    AgentDecision,
    AgentDecisionPayload,
    AgentMessage,
    AgentMessagePayload,
    AgentMove,
    AgentMovePayload,
    ClockEnd,
    ClockEndPayload,
    ClockStep,
    ClockStepPayload,
    Disturbance,
    DisturbancePayload,
    Envelope,
    Incident,
    IncidentPayload,
    Injection,
    LlmCall,
    Moteur,
    NatureIncident,
    Weather,
    WeatherPayload,
)
from sim.commerce import Commerce
from sim.config import Config, Mode
from sim.controle import SourceControle
from sim.domaine import MINUTES_PAR_JOUR, Canal, Zone
from sim.etat import EtatMonde
from sim.horloge import HorlogePhase, SimClock, ordre_paliers
from sim.livraison import Boite
from sim.routines import HEURE_DEPLACEMENT, Routines
from sim.run import RunInfo

HEURE_DU_SOIR = 18 * 60  # stocks et ruptures du jour, publiés après les décisions


class Publisher(Protocol):
    async def publish(self, event: Envelope) -> None: ...


@dataclass
class Bilan:
    """Compteurs d'un run, tenus pendant l'exécution (E4)."""

    jours: int = 0
    decisions: int = 0
    appels_llm: int = 0
    decisions_conservees: int = 0
    messages: int = 0
    deplacements: int = 0
    incidents: int = 0
    violations_temporelles: int = 0
    informations_manquees: int = 0  # mode asynchrone : calculées trop tard pour leur lecteur
    incidents_par_nature: dict[str, int] = field(default_factory=dict)
    injections: int = 0  # perturbations appliquées à la demande de l'interface (D3)
    relances: int = 0  # tentatives au-delà de la première
    replis: int = 0  # décisions d'agents LLM prises par la règle de repli
    declenchements: dict[str, int] = field(default_factory=dict)  # décisions par motif
    # Commerce (étape 13) : état du monde, pas recalculé par le rejeu (voir replay.bilan).
    achats: int = 0
    achats_manques: int = 0
    ruptures: int = 0
    reassorts: int = 0
    bascules_palier: int = 0
    tokens_entree: int = 0
    tokens_sortie: int = 0
    duree_llm_s: float = 0.0  # somme des durées d'appel (en parallèle, dépasse le temps réel)
    duree_chargement_s: float = 0.0  # chargements de modèles avant les groupes de palier
    cout_par_modele: dict[str, dict[str, float]] = field(default_factory=dict)
    duree_reelle_s: float = 0.0
    sequence_paliers: list[str] = field(default_factory=list)

    def compter_appel(self, appel: LlmCall) -> None:
        self.appels_llm += appel.tentatives
        self.relances += appel.tentatives - 1
        self.tokens_entree += appel.tokens_entree
        self.tokens_sortie += appel.tokens_sortie
        self.duree_llm_s += appel.duree_s
        cout = self.cout_par_modele.setdefault(
            appel.modele, {"appels": 0, "tokens_entree": 0, "tokens_sortie": 0, "duree_s": 0.0}
        )
        cout["appels"] += appel.tentatives
        cout["tokens_entree"] += appel.tokens_entree
        cout["tokens_sortie"] += appel.tokens_sortie
        cout["duree_s"] = round(cout["duree_s"] + appel.duree_s, 3)


@dataclass
class Journee:
    """Ce que la phase de décision d'un jour laisse au reste du jour."""

    fin: int  # heure simulée de la dernière décision
    decisions: int  # agents déclenchés
    perceptions: dict[str, Perception]
    manquees: list[UUID] = field(default_factory=list)  # mode asynchrone, un par lecteur


@dataclass
class _JourneeAsync:
    """État d'une journée asynchrone : ce que chacun était au début des décisions, et ce qui a
    changé depuis, daté. La perception à l'heure s ne retient que les changements datés de s
    ou avant, même si une phase de modèles les a calculés plus tard (C6)."""

    matin: dict[str, tuple[Zone, bool]]  # zone et porteur de chacun au début des décisions
    regles: list[tuple[int, str]]  # agents à règles pas encore passés : (heure, identifiant)
    changements: dict[str, tuple[int, Zone, bool]] = field(default_factory=dict)
    passages: dict[str, int] = field(default_factory=dict)  # heure de passage de chacun
    perceptions: dict[str, Perception] = field(default_factory=dict)
    manquees: list[UUID] = field(default_factory=list)
    declenches: int = 0
    fin: int = 0

    def etat_a(self, agent_id: str, minute: int) -> tuple[Zone, bool]:
        change = self.changements.get(agent_id)
        if change is not None and change[0] <= minute:
            return change[1], change[2]
        return self.matin[agent_id]


@dataclass(frozen=True)
class ResumeJour:
    jour: int
    decisions: int
    conservees: int
    porteurs: int
    population: int
    sens: dict[str, int]


class Orchestrateur:
    def __init__(
        self,
        config: Config,
        run: RunInfo,
        agents: list[Agent],
        acteurs_monde: list[WorldActor],
        publisher: Publisher,
        etat: EtatMonde | None = None,
        on_jour: Callable[[ResumeJour], None] | None = None,
        preparer: Callable[[Palier], Awaitable[float]] | None = None,
        controle: SourceControle | None = None,
        routines: Routines | None = None,
        commerce: Commerce | None = None,
    ) -> None:
        self.config = config
        self.run = run
        self.agents = sorted(agents, key=lambda a: a.agent_id)
        self._par_id = {a.agent_id: a for a in self.agents}
        if len(self._par_id) != len(agents):
            raise ValueError("deux agents portent le même identifiant")
        self.acteurs_monde = acteurs_monde
        self.publisher = publisher
        self.etat = etat
        self.on_jour = on_jour
        self.preparer = preparer
        self.controle = controle
        self.routines = routines
        self.commerce = commerce
        # Ce qui réagit mécaniquement aux perturbations : météo, commerce (étape 13).
        self._reactifs = [a for a in acteurs_monde if hasattr(a, "reagir")]
        if commerce is not None:
            self._reactifs.append(commerce)
        # Abonnés de chaque compte : destinataires de ses publications « réseaux ».
        self._abonnes = {
            emetteur: [
                a.agent_id
                for a in self.agents
                if a.abonnements is None or emetteur in a.abonnements
            ]
            for emetteur in self._par_id
        }
        self._injections: list[Injection] = []  # reçues, appliquées au début du jour suivant
        self._en_pause = False
        self._arret = False
        self.horloge = SimClock(config.simulation.duree_jours)
        self.boite = Boite()
        self.bilan = Bilan()
        self.meteo: WeatherPayload | None = None
        self._perturbations: list[Envelope] = []  # survenues depuis la dernière perception
        self._perturbations_vues: set[UUID] = set()
        self._meteo_event: Weather | None = None
        self._non_lus: dict[str, tuple[AgentMessage, ...]] = {}  # reçus, pas encore lus
        self._phase: str | None = None  # "A" (petit + moyen) ou "B" (grand) : modèles chargés
        # Mode asynchrone : événements de la journée, publiés à sa fin dans l'ordre de leur heure.
        self._tampon: list[Envelope] | None = None

    # --- boucle principale ------------------------------------------------------------------

    async def executer(self, jours: int | None = None) -> Bilan:
        debut = time.perf_counter()
        await self._statut("en_cours")
        for jour in range(1, (jours or self.config.simulation.duree_jours) + 1):
            if not await self._relever_controle():
                break
            await self.jour(jour)
        await self._statut("arrete" if self._arret else "termine")
        if self.commerce is not None:
            c = self.commerce
            self.bilan.achats, self.bilan.achats_manques = c.achats, c.achats_manques
            self.bilan.ruptures, self.bilan.reassorts = c.ruptures, c.reassorts
        self.bilan.duree_reelle_s = time.perf_counter() - debut
        self.bilan.duree_llm_s = round(self.bilan.duree_llm_s, 3)
        self.bilan.duree_chargement_s = round(self.bilan.duree_chargement_s, 3)
        return self.bilan

    async def jour(self, jour: int) -> None:
        minute0 = self.horloge.commencer_jour(jour)
        await self._publier(
            ClockStep(
                **self._dates(minute0),
                source_agent_id="horloge",
                payload=ClockStepPayload(jour=jour),
            )
        )
        # Les perturbations injectées agissent d'abord : une vague de chaleur fait la météo du jour.
        await self._appliquer_injections(minute0, jour)
        for acteur in self.acteurs_monde:
            for event in acteur.evenements_du_jour(jour, minute0, self.run.run_id):
                await self._publier(event)
                if isinstance(event, Weather):
                    self.meteo = event.payload
                    self._meteo_event = event
                elif isinstance(event, Disturbance):
                    self._perturbations.append(event)
                    self._perturbations_vues.add(event.event_id)
                    self._reagir(event, jour)
        if self.commerce is not None:  # réassort du vendredi, puis un jour d'usage des flacons
            for event in self.commerce.debut_du_jour(jour, minute0, self.run.run_id):
                await self._publier(event)
            for agent in self.agents:
                if agent.flacon_jours:
                    agent.flacon_jours -= 1
        await self._deplacements_du_matin(minute0)
        await self._statut("en_cours")  # heure simulée courante, lue par le pont (C3)

        if self.config.simulation.mode is Mode.ASYNCHRONE:
            journee = await self._decisions_asynchrones(jour, minute0)
        else:
            journee = await self._decisions_synchrones(jour, minute0)

        if self.commerce is not None:  # stocks du soir, ruptures perçues le lendemain
            soir = max(minute0 + HEURE_DU_SOIR, journee.fin + 1)
            for event in self.commerce.fin_du_jour(soir, self.run.run_id):
                await self._publier(event)
                if isinstance(event, Disturbance):
                    self._perturbations.append(event)
                    self._perturbations_vues.add(event.event_id)

        self.bilan.jours = jour
        self.bilan.informations_manquees += len(journee.manquees)
        if self.etat:
            await self.etat.ecrire_jour(
                jour, journee.fin, self.agents, self.meteo, journee.perceptions
            )
        # Tous les faits du jour sont publiés : les indicateurs du jour peuvent être calculés.
        await self._publier(
            ClockEnd(
                **self._dates(minute0 + MINUTES_PAR_JOUR - 1),
                source_agent_id="horloge",
                caused_by=tuple(dict.fromkeys(journee.manquees)),
                payload=ClockEndPayload(jour=jour, informations_manquees=len(journee.manquees)),
            )
        )
        if self.on_jour:
            self.on_jour(self._resume(jour, journee.decisions))

    async def _decisions_synchrones(self, jour: int, minute0: int) -> Journee:
        t = self.horloge.heure_decision()
        perceptions = self._percevoir(jour, t, barriere=minute0)
        self._perturbations.clear()
        declenches = [a for a in self.agents if a.doit_decider(perceptions[a.agent_id])]
        for agent in self.agents:  # sans décision aujourd'hui, ses messages attendent la suivante
            if agent not in declenches and perceptions[agent.agent_id].messages:
                self._non_lus[agent.agent_id] = perceptions[agent.agent_id].messages
        self.bilan.decisions_conservees += len(self.agents) - len(declenches)

        resultats: dict[str, Decision | None] = {}
        groupes: list[tuple[Palier | None, list[Agent]]] = [
            (None, [a for a in declenches if a.palier is None])
        ]
        groupes += [(p, [a for a in declenches if a.palier is p]) for p in ordre_paliers(jour)]
        for palier, groupe in groupes:
            if groupe:
                self._noter_palier(palier)
                if palier is not None and self.preparer:
                    self.bilan.duree_chargement_s += await self.preparer(palier)
                resultats |= await self._decider_groupe(groupe, palier, perceptions)

        achetes = self._acheter(resultats, jour) if self.commerce is not None else set()
        messages: list[AgentMessage] = []
        for agent_id in sorted(resultats):
            agent, decision = self._par_id[agent_id], resultats[agent_id]
            messages += await self._appliquer(agent, decision, t, perceptions[agent_id])
            self._regler_flacon(agent, decision, agent_id in achetes)
        if self.config.simulation.diffusion:  # run témoin (B3) : personne ne les reçoit
            for message in messages:  # zones résolues après les déplacements du jour
                self.boite.deposer(message, self._destinataires(message))
        return Journee(fin=t, decisions=len(resultats), perceptions=perceptions)

    # --- mode asynchrone (étape 15) -----------------------------------------------------------

    async def _decisions_asynchrones(self, jour: int, minute0: int) -> Journee:
        reglages = self.config.simulation.asynchrone
        debut = minute0 + reglages.debut_min
        limite = minute0 + MINUTES_PAR_JOUR - 2  # avant les stocks du soir et la fin du jour
        regles = []
        for agent in self.agents:
            if agent.palier is None:
                tirage = random.Random(f"{self.run.seed}:reveil:{jour}:{agent.agent_id}")
                heure = debut + tirage.randint(0, reglages.fenetre_regles_min)
                regles.append((heure, agent.agent_id))
        journee = _JourneeAsync(
            matin={a.agent_id: (a.zone, a.porteur) for a in self.agents},
            regles=sorted(regles),
            fin=debut,
        )
        self._tampon = []

        ordre = ordre_paliers(jour)
        phases = [[p for p in ordre if p is not Palier.GRAND], [Palier.GRAND]]
        if ordre[0] is Palier.GRAND:
            phases.reverse()
        verrou = asyncio.Lock()  # perceptions et applications : jamais au milieu d'une autre
        for numero, paliers in enumerate(phases):
            horloge = HorlogePhase(debut, reglages.minutes_par_seconde, limite)
            await self._phase_de_modeles(
                jour, paliers, horloge, journee, verrou, premiere=numero == 0
            )
            if numero == 0:  # agents à règles dont l'heure n'a pas été atteinte pendant la phase
                await self._regles_jusqu_a(limite, jour, journee)

        self._perturbations.clear()
        tampon, self._tampon = self._tampon, None
        tampon.sort(key=lambda e: e.sim_time_processed)  # tri stable : une décision et ses suites
        for event in tampon:
            await self._publier(event)
        self.horloge.minute = journee.fin
        return Journee(
            fin=journee.fin,
            decisions=journee.declenches,
            perceptions=journee.perceptions,
            manquees=journee.manquees,
        )

    async def _phase_de_modeles(
        self,
        jour: int,
        paliers: list[Palier],
        horloge: HorlogePhase,
        journee: _JourneeAsync,
        verrou: asyncio.Lock,
        premiere: bool,
    ) -> None:
        """Une phase de modèles : pour chaque palier, autant d'ouvriers que de requêtes
        simultanées permises ; chaque ouvrier fait passer les agents de sa file un par un."""
        prets: set[Palier] = set()
        chargement = asyncio.Lock()

        async def preparer(palier: Palier) -> None:
            async with chargement:
                if palier in prets:
                    return
                prets.add(palier)
                self._noter_palier(palier)
                if self.preparer:
                    duree = await self.preparer(palier)
                    self.bilan.duree_chargement_s += duree
                    horloge.decaler(duree)  # le chargement ne compte pas dans l'heure

        async def ouvrier(file: list[Agent]) -> None:
            while file:
                agent = file.pop(0)
                async with verrou:
                    s = horloge.maintenant()
                    if premiere:
                        await self._regles_jusqu_a(s, jour, journee)
                    perception = self._passer(agent, jour, s, journee)
                if perception is None:
                    continue
                assert agent.palier is not None
                await preparer(agent.palier)
                decision = await self._decider_un(agent, perception, s)
                async with verrou:
                    t = max(s, horloge.maintenant())
                    await self._appliquer_async(agent, decision, s, t, perception, journee)

        ouvriers = []
        for palier in paliers:
            file = [a for a in self.agents if a.palier is palier]
            random.Random(f"{self.run.seed}:file:{jour}:{palier.value}").shuffle(file)
            nombre = min(self.config.llm.parallelisme[palier], len(file))
            ouvriers += [ouvrier(file) for _ in range(nombre)]
        await asyncio.gather(*ouvriers)

    async def _regles_jusqu_a(self, minute: int, jour: int, journee: _JourneeAsync) -> None:
        """Fait passer, dans l'ordre de leur heure, les agents à règles dont l'heure est atteinte.
        Ils ne coûtent aucun temps de calcul : leur décision est datée de leur heure."""
        while journee.regles and journee.regles[0][0] <= minute:
            heure, agent_id = journee.regles.pop(0)
            agent = self._par_id[agent_id]
            perception = self._passer(agent, jour, heure, journee)
            if perception is not None:
                decision = await self._decider_un(agent, perception, heure)
                await self._appliquer_async(agent, decision, heure, heure, perception, journee)

    def _passer(
        self, agent: Agent, jour: int, minute: int, journee: _JourneeAsync
    ) -> Perception | None:
        """Passage d'un agent à `minute` : sa perception, et s'il doit décider (règle 10)."""
        presents, porteurs = Counter(), Counter()
        for autre in self.agents:
            zone, porteur = journee.etat_a(autre.agent_id, minute)
            presents[zone] += 1
            porteurs[zone] += int(porteur)
        perception = self._perception(agent, jour, minute, minute + 1, presents, porteurs)
        journee.perceptions[agent.agent_id] = perception
        journee.passages[agent.agent_id] = minute
        if not agent.doit_decider(perception):
            if perception.messages:  # ils attendent sa prochaine décision
                self._non_lus[agent.agent_id] = perception.messages
            self.bilan.decisions_conservees += 1
            return None
        journee.declenches += 1
        return perception

    async def _appliquer_async(
        self,
        agent: Agent,
        decision: Decision | None,
        debut: int,
        t: int,
        perception: Perception,
        journee: _JourneeAsync,
    ) -> None:
        """Applique une décision dès qu'elle est prise : achat, état daté, messages déposés
        (lisibles le jour même si leur délai de livraison le permet)."""
        journee.fin = max(journee.fin, t)
        if decision is None:
            return
        achete = False
        if self.commerce is not None:
            resultats: dict[str, Decision | None] = {agent.agent_id: decision}
            achete = bool(self._acheter(resultats, perception.jour))
            decision = resultats[agent.agent_id]
        self.horloge.minute = t  # incidents de la décision datés avec elle
        messages = await self._appliquer(agent, decision, t, perception, debut=debut)
        self._regler_flacon(agent, decision, achete)
        journee.changements[agent.agent_id] = (t, agent.zone, agent.porteur)
        if not self.config.simulation.diffusion:
            return
        for message in messages:
            destinataires = self._destinataires(message)
            self.boite.deposer(message, destinataires)
            for lecteur in destinataires:
                passage = journee.passages.get(lecteur)
                if (
                    lecteur != message.source_agent_id
                    and passage is not None
                    and message.payload.livraison < passage  # simultanés : pas un retard
                ):
                    journee.manquees.append(message.event_id)

    # --- contrôle depuis l'interface (D3) ------------------------------------------------------

    async def _relever_controle(self) -> bool:
        """Traite les commandes et garde les injections pour le jour qui commence. En pause,
        attend une reprise ou un arrêt. Renvoie False si le run doit s'arrêter."""
        if self.controle is None:
            return True
        while True:
            for message in await self.controle.relever():
                if isinstance(message, Injection):
                    self._injections.append(message)
                elif message.payload.commande == "pause":
                    self._en_pause = True
                elif message.payload.commande == "reprise":
                    self._en_pause = False
                else:
                    self._arret = True
            if self._arret:
                return False
            if not self._en_pause:
                await self._statut("en_cours")
                return True
            await self._statut("pause")
            await asyncio.sleep(0.2)

    async def _appliquer_injections(self, minute0: int, jour: int) -> None:
        """Chaque injection devient une perturbation au début du jour : elle a été demandée la
        veille (`sim_time_occurred` de l'injection) et prend effet maintenant (C3, C6)."""
        for injection in self._injections:
            event = Disturbance(
                **self._dates(minute0),
                source_agent_id="orchestrateur",
                zone=injection.zone,
                caused_by=(injection.event_id,),
                payload=DisturbancePayload(
                    nature=injection.payload.nature, description=injection.payload.description
                ),
            )
            await self._publier(event)
            self._perturbations.append(event)
            self._perturbations_vues.add(event.event_id)
            self._reagir(event, jour)
            self.bilan.injections += 1
        self._injections.clear()

    async def _statut(self, statut: str) -> None:
        if self.etat:
            await self.etat.ecrire_statut(statut, self.horloge.jour, self.horloge.maintenant())

    # --- étapes du jour ---------------------------------------------------------------------

    def _percevoir(self, jour: int, t: int, barriere: int) -> dict[str, Perception]:
        presents, porteurs = Counter(), Counter()
        for agent in self.agents:  # état de la veille : aucune décision du jour n'est appliquée
            presents[agent.zone] += 1
            porteurs[agent.zone] += int(agent.porteur)
        return {
            agent.agent_id: self._perception(agent, jour, t, barriere, presents, porteurs)
            for agent in self.agents
        }

    def _perception(
        self,
        agent: Agent,
        jour: int,
        t: int,
        barriere: int,
        presents: Counter,
        porteurs: Counter,
    ) -> Perception:
        """Ce que l'agent sait à `t` : messages livrés et émis avant `barriere`, perturbations du
        jour dans sa zone, météo, part de porteurs autour de lui (`presents`, `porteurs`)."""
        return Perception(
            jour=jour,
            minute=t,
            zone=agent.zone,
            messages=self._non_lus.pop(agent.agent_id, ())
            + self.boite.relever(agent.agent_id, t, barriere),
            evenements=tuple(
                e for e in self._perturbations if e.zone is None or e.zone is agent.zone
            ),
            meteo=self.meteo,
            # Run témoin (B3) : l'agent ne voit pas qui utilise le produit autour de lui.
            part_porteurs_zone=porteurs[agent.zone] / presents[agent.zone]
            if self.config.simulation.diffusion
            else None,
            flacon_vide=agent.flacon_jours == 0
            and bool(agent.derniere_decision and agent.derniere_decision.porteur),
            achat_rate=bool(self.commerce and agent.agent_id in self.commerce.rates_veille),
            promotion_concurrente=bool(self.commerce and self.commerce.promotion_concurrente(jour)),
        )

    def _regler_flacon(self, agent: Agent, decision: Decision | None, achete: bool) -> None:
        """Un achat remplit le flacon ; une décision qui n'utilise plus le produit le retire."""
        if self.commerce and decision is not None and agent.derniere_decision is decision:
            if achete:
                agent.flacon_jours = self.commerce.config.duree_flacon_jours
            elif not decision.porteur:
                agent.flacon_jours = None

    def _acheter(self, resultats: dict[str, Decision | None], jour: int) -> set[str]:
        """Achats du jour, dans un ordre tiré avec la graine (l'ordre alphabétique servirait
        toujours les mêmes) : un nouveau porteur, ou un porteur dont le flacon est fini, doit
        trouver un flacon en rayon. En rupture, sa décision est gardée mais il ne devient pas
        porteur. Renvoie les agents qui ont acheté."""
        assert self.commerce is not None
        acheteurs = sorted(
            agent_id
            for agent_id, d in resultats.items()
            if d is not None and d.porteur and not self._par_id[agent_id].porteur
        )
        random.Random(f"{self.run.seed}:achats:{jour}").shuffle(acheteurs)
        achetes = set()
        for agent_id in acheteurs:
            if self.commerce.acheter(agent_id, self._par_id[agent_id].zone) is None:
                resultats[agent_id] = dataclasses.replace(resultats[agent_id], porteur=False)
            else:
                achetes.add(agent_id)
        return achetes

    def _reagir(self, perturbation: Disturbance, jour: int) -> None:
        for reactif in self._reactifs:
            reactif.reagir(perturbation, jour)

    def _noter_palier(self, palier: Palier | None) -> None:
        if palier is None:
            return
        self.bilan.sequence_paliers.append(palier.value)
        phase = "B" if palier is Palier.GRAND else "A"
        if self._phase is not None and phase != self._phase:
            self.bilan.bascules_palier += 1
        self._phase = phase

    async def _decider_groupe(
        self, groupe: list[Agent], palier: Palier | None, perceptions: dict[str, Perception]
    ) -> dict[str, Decision | None]:
        limite = self.config.llm.parallelisme[palier] if palier else len(groupe)
        semaphore = asyncio.Semaphore(limite)

        async def decider(agent: Agent) -> Decision | None:
            async with semaphore:
                return await self._decider_un(agent, perceptions[agent.agent_id])

        decisions = await asyncio.gather(*(decider(agent) for agent in groupe))
        return {agent.agent_id: d for agent, d in zip(groupe, decisions, strict=True)}

    async def _decider_un(
        self, agent: Agent, perception: Perception, minute: int | None = None
    ) -> Decision | None:
        try:
            return await agent.decider(perception)
        except Exception as exc:  # un agent en panne ne doit pas arrêter le monde (A4)
            await self._incident(
                NatureIncident.SANS_REPONSE,
                agent.agent_id,
                f"{type(exc).__name__}: {exc}",
                minute=minute,
            )
            return None

    async def _deplacements_du_matin(self, minute0: int) -> None:
        """Routines (règles) : chacun gagne la zone du jour avant de décider. La décision du
        déplacement dépend du temps qu'il fait : il cite la météo du jour (F6)."""
        if self.routines is None:
            return
        cause = (self._meteo_event.event_id,) if self._meteo_event else ()
        heure = minute0 + HEURE_DEPLACEMENT
        for agent_id, zone in self.routines.deplacements(self.agents, self.meteo).items():
            agent = self._par_id[agent_id]
            await self._publier(
                AgentMove(
                    run_id=self.run.run_id,
                    sim_time_occurred=heure,
                    sim_time_processed=heure,
                    source_agent_id=agent_id,
                    zone=zone,
                    caused_by=cause,
                    payload=AgentMovePayload(depuis=agent.zone, vers=zone),
                )
            )
            agent.zone = zone
            self.bilan.deplacements += 1

    async def _appliquer(
        self,
        agent: Agent,
        decision: Decision | None,
        t: int,
        perception: Perception,
        debut: int | None = None,
    ) -> list[AgentMessage]:
        """Publie une décision prise à `t` et ce qui en découle. `debut` : moment de la perception,
        quand l'agent a commencé à réfléchir (mode asynchrone) ; sinon `t`."""
        if decision is None:  # l'agent garde sa décision précédente
            return []
        debut = t if debut is None else debut
        try:
            event = AgentDecision(
                **self._dates(t, debut),
                source_agent_id=agent.agent_id,
                zone=agent.zone,
                caused_by=decision.caused_by,
                payload=AgentDecisionPayload(
                    action=decision.action,
                    sens=decision.sens,
                    justification=decision.justification,
                    porteur=decision.porteur,
                    moteur=decision.moteur,
                    palier=decision.palier,
                    appel_llm=decision.appel_llm,
                    motif=agent.motif,
                    sources=decision.sources,
                ),
            )
            suites = self._suites(agent, decision, event.event_id, t)
        except ValidationError as exc:
            await self._incidents_agent(agent, decision)
            await self._incident(NatureIncident.SORTIE_INVALIDE, agent.agent_id, str(exc))
            return []

        await self._controler_c6(agent, event, debut)
        await self._publier(event)
        await self._incidents_agent(agent, decision, caused_by=(event.event_id,))
        agent.noter_decision(decision, perception)
        self.bilan.decisions += 1
        if agent.motif is not None:
            par_motif = self.bilan.declenchements
            par_motif[agent.motif.value] = par_motif.get(agent.motif.value, 0) + 1
        if decision.appel_llm:  # y compris un repli : ses tentatives échouées ont coûté
            self.bilan.compter_appel(decision.appel_llm)
        if decision.moteur is Moteur.REPLI:
            self.bilan.replis += 1

        messages = []
        for suite in suites:
            await self._publier(suite)
            if isinstance(suite, AgentMove):
                agent.zone = suite.payload.vers
                self.bilan.deplacements += 1
            else:
                messages.append(suite)
                self.bilan.messages += 1
        return messages

    def _suites(
        self, agent: Agent, decision: Decision, decision_id: UUID, t: int
    ) -> list[AgentMove | AgentMessage]:
        """Déplacement et messages qui découlent d'une décision (construits avant publication,
        pour qu'une décision mal formée n'en publie qu'une partie)."""
        suites: list[AgentMove | AgentMessage] = []
        zone = agent.zone
        if decision.deplacement and decision.deplacement is not zone:
            suites.append(
                AgentMove(
                    **self._dates(t),
                    source_agent_id=agent.agent_id,
                    zone=decision.deplacement,
                    caused_by=(decision_id,),
                    payload=AgentMovePayload(depuis=zone, vers=decision.deplacement),
                )
            )
            zone = decision.deplacement
        delais = self.config.simulation.delais_livraison_min
        for intent in decision.messages:
            suites.append(
                AgentMessage(
                    **self._dates(t),
                    source_agent_id=agent.agent_id,
                    zone=zone,
                    caused_by=(decision_id,),
                    payload=AgentMessagePayload(
                        canal=intent.canal,
                        destinataires=intent.destinataires,
                        contenu=intent.contenu,
                        livraison=t + delais[intent.canal],
                    ),
                )
            )
        return suites

    async def _controler_c6(self, agent: Agent, event: AgentDecision, t: int) -> None:
        """Une décision ne peut citer que ce que l'agent avait reçu à ce moment (C6) : un message
        qui lui a été remis (aujourd'hui ou un jour précédent) ou une perturbation déjà survenue."""
        for cause in event.caused_by:
            recu = self.boite.remis_avant(agent.agent_id, cause, t)
            if not recu and cause not in self._perturbations_vues:
                self.bilan.violations_temporelles += 1
                await self._incident(
                    NatureIncident.VIOLATION_TEMPORELLE,
                    agent.agent_id,
                    f"la décision cite {cause}, que l'agent n'avait pas reçu à t={t}",
                    caused_by=(event.event_id,),
                )

    def _destinataires(self, message: AgentMessage) -> list[str]:
        canal = message.payload.canal
        if canal is Canal.DIRECT:
            return [a for a in message.payload.destinataires if a in self._par_id]
        if canal is Canal.ZONE:
            return [a.agent_id for a in self.agents if a.zone is message.zone]
        return self._abonnes.get(message.source_agent_id, [])  # réseaux : ses abonnés

    # --- utilitaires ------------------------------------------------------------------------

    def _dates(self, minute: int, debut: int | None = None) -> dict:
        # En mode synchrone, le temps est figé pendant le pas : traité au moment où il se produit.
        # En mode asynchrone, une décision commence à la perception (`debut`) et se termine à la
        # fin de l'appel au modèle (C3).
        return {
            "run_id": self.run.run_id,
            "sim_time_occurred": minute if debut is None else debut,
            "sim_time_processed": minute,
        }

    async def _publier(self, event: Envelope) -> None:
        if self._tampon is not None:  # journée asynchrone : publiée à sa fin, dans l'ordre
            self._tampon.append(event)
            return
        await self.publisher.publish(event)

    async def _incidents_agent(
        self, agent: Agent, decision: Decision, caused_by: tuple[UUID, ...] = ()
    ) -> None:
        for incident in decision.incidents:
            await self._incident(
                incident.nature, agent.agent_id, incident.detail, caused_by, incident.tentative
            )

    async def _incident(
        self,
        nature: NatureIncident,
        agent_id: str | None,
        detail: str,
        caused_by: tuple[UUID, ...] = (),
        tentative: int = 0,
        minute: int | None = None,
    ) -> None:
        self.bilan.incidents += 1
        par_nature = self.bilan.incidents_par_nature
        par_nature[nature.value] = par_nature.get(nature.value, 0) + 1
        await self._publier(
            Incident(
                **self._dates(self.horloge.maintenant() if minute is None else minute),
                source_agent_id="orchestrateur",
                caused_by=caused_by,
                payload=IncidentPayload(
                    nature=nature, agent_id=agent_id, detail=detail[:2000], tentative=tentative
                ),
            )
        )

    def _resume(self, jour: int, decisions: int) -> ResumeJour:
        return ResumeJour(
            jour=jour,
            decisions=decisions,
            conservees=len(self.agents) - decisions,
            porteurs=sum(a.porteur for a in self.agents),
            population=len(self.agents),
            sens=dict(Counter(a.sens.value for a in self.agents if a.sens)),
        )
