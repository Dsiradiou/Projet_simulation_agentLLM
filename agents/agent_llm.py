"""Agent piloté par un LLM : prompt, appel au palier, une relance, puis repli sur règle (A4).

Déroulé d'une décision :
1. appel au modèle du palier, réponse JSON contrainte par le schéma de l'agent ;
2. réponse invalide ou délai dépassé : incident, puis une relance qui rappelle la raison du refus ;
3. second échec : incident `repli`, et la décision vient de la règle « seuil + continuité ».

Chaque tentative est comptée dans le coût (E4), y compris celles qui échouent. Les incidents sont
rendus avec la décision ; l'orchestrateur les publie (un agent ne parle jamais à Kafka).
"""

import random
import time

from agents.base import Agent, Decision, IncidentAgent, MessageIntent, Perception, porteur_apres
from agents.prompts import prompt_jour, prompt_systeme
from agents.regles import ChoixRegle, repli_seuil_continuite
from agents.roles import Role
from bus.events import LlmCall, Moteur, NatureIncident
from llm.client import Reponse
from llm.routeur import DelaiDepasse, Routeur, graine_appel
from llm.sortie import SortieAgent, SortieInvalide, lire_sortie, schema_sortie
from sim.config import MondeConfig
from sim.domaine import Canal, Zone

TENTATIVES = 2  # un appel et une relance (règle 4 de CLAUDE.md)
_EXTRAIT = 300  # caractères de la réponse brute recopiés dans l'incident


class AgentLLM(Agent):
    def __init__(
        self,
        agent_id: str,
        role: Role,
        zone: Zone,
        rng: random.Random,
        routeur: Routeur,
        monde: MondeConfig,
        seed: int,
        contacts: list[str],
        seuil: float,
    ) -> None:
        super().__init__(agent_id, role, zone, rng)
        if self.palier is None:
            raise ValueError(f"le rôle {role} n'est pas tenu par un agent LLM")
        self.routeur = routeur
        self.monde = monde
        self.seed = seed
        self.contacts = contacts
        self.seuil = seuil
        self.systeme = prompt_systeme(role, monde)
        self.schema = schema_sortie(contacts)

    async def decider(self, perception: Perception) -> Decision:
        assert self.palier is not None
        prompt, montres = prompt_jour(perception, self.derniere_decision, self.contacts, self.monde)
        caused_by = tuple(m.event_id for m in montres) + tuple(
            e.event_id for e in perception.evenements
        )
        incidents: list[IncidentAgent] = []
        reponses: list[Reponse] = []
        duree_s = 0.0
        relance = ""
        for tentative in range(1, TENTATIVES + 1):
            graine = graine_appel(self.seed, self.agent_id, perception.jour, tentative)
            debut = time.perf_counter()
            try:
                reponse = await self.routeur.generer(
                    self.palier, self.systeme, prompt + relance, self.schema, graine
                )
            except Exception as exc:  # délai dépassé, Ollama injoignable... (A4)
                duree_s += time.perf_counter() - debut
                detail = (
                    str(exc) if isinstance(exc, DelaiDepasse) else f"{type(exc).__name__}: {exc}"
                )
                incidents.append(IncidentAgent(NatureIncident.SANS_REPONSE, detail, tentative))
                relance = ""
                continue
            duree_s += reponse.duree_s
            reponses.append(reponse)
            try:
                sortie = lire_sortie(reponse.texte, self.contacts)
            except SortieInvalide as exc:
                incidents.append(
                    IncidentAgent(
                        NatureIncident.SORTIE_INVALIDE,
                        f"{exc} | réponse : {reponse.texte[:_EXTRAIT]}",
                        tentative,
                    )
                )
                relance = (
                    f"\n\nTa réponse précédente a été refusée ({exc}). "
                    "Réponds de nouveau, uniquement avec le JSON demandé."
                )
                continue
            return self._depuis_sortie(
                sortie, self._cout(reponses, duree_s, tentative), caused_by, incidents
            )

        incidents.append(
            IncidentAgent(
                NatureIncident.REPLI, f"{TENTATIVES} échecs : décision par règle", TENTATIVES
            )
        )
        choix = repli_seuil_continuite(self.derniere_decision, perception, self.seuil)
        return self._depuis_regle(choix, self._cout(reponses, duree_s, TENTATIVES), incidents)

    def _cout(self, reponses: list[Reponse], duree_s: float, tentatives: int) -> LlmCall:
        assert self.palier is not None
        return LlmCall(
            modele=self.routeur.modele(self.palier),
            tokens_entree=sum(r.tokens_entree for r in reponses),
            tokens_sortie=sum(r.tokens_sortie for r in reponses),
            duree_s=round(duree_s, 3),
            tentatives=tentatives,
        )

    def _depuis_sortie(
        self,
        sortie: SortieAgent,
        cout: LlmCall,
        caused_by: tuple,
        incidents: list[IncidentAgent],
    ) -> Decision:
        messages: tuple[MessageIntent, ...] = ()
        if sortie.message:
            m = sortie.message
            destinataires = (m.destinataire,) if m.canal is Canal.DIRECT and m.destinataire else ()
            messages = (MessageIntent(m.canal, m.contenu, destinataires),)
        return Decision(
            action=sortie.action,
            sens=sortie.sens,
            justification=sortie.justification,
            porteur=porteur_apres(sortie.action, self.porteur),
            moteur=Moteur.LLM,
            palier=self.palier,
            appel_llm=cout,
            messages=messages,
            caused_by=caused_by,
            incidents=tuple(incidents),
        )

    def _depuis_regle(
        self, choix: ChoixRegle, cout: LlmCall, incidents: list[IncidentAgent]
    ) -> Decision:
        # Une règle ne lit pas les messages : la décision ne cite donc aucun événement.
        return Decision(
            action=choix.action,
            sens=choix.sens,
            justification=choix.justification,
            porteur=choix.porteur,
            moteur=Moteur.REPLI,
            palier=self.palier,
            appel_llm=cout,
            incidents=tuple(incidents),
        )
