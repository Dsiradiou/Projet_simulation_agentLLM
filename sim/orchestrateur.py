"""Orchestrateur, mode synchrone (C4) : fait avancer le temps jour par jour et déclenche les agents.

Avant chaque jour, l'orchestrateur relève les commandes et injections de l'interface (D3) :
pause et reprise, arrêt propre, perturbations à appliquer ce jour-là.

Déroulé d'un jour d :
1. début du jour (00:00) : `horloge.pas`, les événements des acteurs du monde (météo...), puis
   les perturbations injectées depuis la veille, éventuellement limitées à une zone ; à 08:00,
   les déplacements de routine (règles, selon le rôle et le temps) ;
2. perception de chaque agent, construite sur l'état de la veille : c'est la barrière
   synchrone, aucun agent ne voit ce que les autres décident le jour même ;
3. décision des seuls agents déclenchés (règle 10), les agents à règles d'abord, puis les
   agents LLM regroupés par palier dans l'ordre de `ordre_paliers` ; le modèle d'un palier est
   chargé avant son groupe (`preparer`), pour mesurer la bascule à part ;
4. publication, dans l'ordre des identifiants d'agents pour un run reproductible : décisions,
   incidents de la décision (relance, repli), déplacements, messages (datés de leur livraison) ;
5. écriture de l'état courant dans Redis.
"""

import asyncio
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
from sim.config import Config
from sim.controle import SourceControle
from sim.domaine import Canal
from sim.etat import EtatMonde
from sim.horloge import SimClock, ordre_paliers
from sim.livraison import Boite
from sim.routines import HEURE_DEPLACEMENT, Routines
from sim.run import RunInfo


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
    incidents_par_nature: dict[str, int] = field(default_factory=dict)
    injections: int = 0  # perturbations appliquées à la demande de l'interface (D3)
    relances: int = 0  # tentatives au-delà de la première
    replis: int = 0  # décisions d'agents LLM prises par la règle de repli
    declenchements: dict[str, int] = field(default_factory=dict)  # décisions par motif
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

    # --- boucle principale ------------------------------------------------------------------

    async def executer(self, jours: int | None = None) -> Bilan:
        debut = time.perf_counter()
        await self._statut("en_cours")
        for jour in range(1, (jours or self.config.simulation.duree_jours) + 1):
            if not await self._relever_controle():
                break
            await self.jour(jour)
        await self._statut("arrete" if self._arret else "termine")
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
        for acteur in self.acteurs_monde:
            for event in acteur.evenements_du_jour(jour, minute0, self.run.run_id):
                await self._publier(event)
                if isinstance(event, Weather):
                    self.meteo = event.payload
                    self._meteo_event = event
                elif isinstance(event, Disturbance):
                    self._perturbations.append(event)
                    self._perturbations_vues.add(event.event_id)
        await self._appliquer_injections(minute0)
        await self._deplacements_du_matin(minute0)
        await self._statut("en_cours")  # heure simulée courante, lue par le pont (C3)

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

        messages: list[AgentMessage] = []
        for agent_id in sorted(resultats):
            agent = self._par_id[agent_id]
            messages += await self._appliquer(agent, resultats[agent_id], t, perceptions[agent_id])
        for message in messages:  # zones résolues après les déplacements du jour
            self.boite.deposer(message, self._destinataires(message))

        self.bilan.jours = jour
        if self.etat:
            await self.etat.ecrire_jour(jour, t, self.agents, self.meteo, perceptions)
        if self.on_jour:
            self.on_jour(self._resume(jour, len(resultats)))

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

    async def _appliquer_injections(self, minute0: int) -> None:
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
            agent.agent_id: Perception(
                jour=jour,
                minute=t,
                zone=agent.zone,
                messages=self._non_lus.pop(agent.agent_id, ())
                + self.boite.relever(agent.agent_id, t, barriere),
                evenements=tuple(
                    e for e in self._perturbations if e.zone is None or e.zone is agent.zone
                ),
                meteo=self.meteo,
                part_porteurs_zone=porteurs[agent.zone] / presents[agent.zone],
            )
            for agent in self.agents
        }

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
                try:
                    return await agent.decider(perceptions[agent.agent_id])
                except Exception as exc:  # un agent en panne ne doit pas arrêter le monde (A4)
                    await self._incident(
                        NatureIncident.SANS_REPONSE, agent.agent_id, f"{type(exc).__name__}: {exc}"
                    )
                    return None

        decisions = await asyncio.gather(*(decider(agent) for agent in groupe))
        return {agent.agent_id: d for agent, d in zip(groupe, decisions, strict=True)}

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
        self, agent: Agent, decision: Decision | None, t: int, perception: Perception
    ) -> list[AgentMessage]:
        if decision is None:  # l'agent garde sa décision précédente
            return []
        try:
            event = AgentDecision(
                **self._dates(t),
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
                ),
            )
            suites = self._suites(agent, decision, event.event_id, t)
        except ValidationError as exc:
            await self._incidents_agent(agent, decision)
            await self._incident(NatureIncident.SORTIE_INVALIDE, agent.agent_id, str(exc))
            return []

        await self._controler_c6(agent, event, t)
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

    def _dates(self, minute: int) -> dict:
        # En mode synchrone, le temps est figé pendant le pas : traité au moment où il se produit.
        return {
            "run_id": self.run.run_id,
            "sim_time_occurred": minute,
            "sim_time_processed": minute,
        }

    async def _publier(self, event: Envelope) -> None:
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
    ) -> None:
        self.bilan.incidents += 1
        par_nature = self.bilan.incidents_par_nature
        par_nature[nature.value] = par_nature.get(nature.value, 0) + 1
        await self._publier(
            Incident(
                **self._dates(self.horloge.maintenant()),
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
