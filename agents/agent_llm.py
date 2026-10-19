"""Agent piloté par un LLM : prompt, appel au palier, une relance, puis repli sur règle (A4).

Déroulé d'une décision :
1. appel au modèle du palier, réponse JSON contrainte par le schéma de l'agent ;
2. réponse invalide ou délai dépassé : incident, puis une relance qui rappelle la raison du refus ;
3. second échec : incident `repli`, et la décision vient de la règle « seuil + continuité ».

Chaque tentative est comptée dans le coût (E4), y compris celles qui échouent. Les incidents sont
rendus avec la décision ; l'orchestrateur les publie (un agent ne parle jamais à Kafka).
"""

import dataclasses
import random
import time
from typing import TYPE_CHECKING

from agents.base import Agent, Decision, IncidentAgent, MessageIntent, Perception, porteur_apres
from agents.prompts import MESSAGES_MAX, prompt_jour, prompt_systeme
from agents.regles import ChoixRegle, repli_seuil_continuite
from agents.roles import Role
from bus.events import LlmCall, Moteur, NatureIncident
from llm.client import Reponse
from llm.routeur import DelaiDepasse, Routeur, graine_appel
from llm.sortie import SortieAgent, SortieInvalide, lire_sortie, schema_sortie
from sim.config import MondeConfig
from sim.domaine import Canal, Zone

if TYPE_CHECKING:
    from rag.recuperation import Recuperateur

TENTATIVES = 2  # un appel et une relance (règle 4 de CLAUDE.md)
# Budget du prompt : 3,4 à 3,6 caractères par token mesurés en français sur Qwen3 (étape 12) ;
# 3,2 garde une marge. Le gabarit de conversation coûte une quarantaine de tokens.
CARACTERES_PAR_TOKEN = 3.2
SURCOUT_TOKENS = 40
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
        messages_par_semaine: int | None = None,
        recuperateur: "Recuperateur | None" = None,
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
        # Budget de messages par semaine simulée (None : sans limite, pour les tests).
        self.messages_par_semaine = messages_par_semaine
        self._jours_messages: list[int] = []  # jours où l'agent a envoyé un message
        self.recuperateur = recuperateur  # corpus et mémoire (RAG, étape 12) ; None : sans
        self.prompts_reduits = 0  # prompts ramenés sous le budget du contexte
        self.retraits_prompt = 0

    def messages_restants(self, jour: int) -> int | None:
        if self.messages_par_semaine is None:
            return None
        recents = sum(1 for j in self._jours_messages if jour - j < 7)
        return max(0, self.messages_par_semaine - recents)

    async def decider(self, perception: Perception) -> Decision:
        assert self.palier is not None
        restants = self.messages_restants(perception.jour)
        peut_parler = restants is None or restants > 0
        schema = schema_sortie(self.contacts, peut_parler)
        incidents: list[IncidentAgent] = []
        contexte = None
        if self.recuperateur:
            try:
                contexte = await self.recuperateur.contexte(self.agent_id, self.role, perception)
            except Exception as exc:  # sans base de connaissances, l'agent décide quand même
                incidents.append(
                    IncidentAgent(
                        NatureIncident.SANS_REPONSE, f"récupération : {type(exc).__name__}: {exc}"
                    )
                )
        prompt, montres, contexte, retraits = self._prompt_dans_le_budget(
            perception, restants, contexte
        )
        sources = contexte.sources if contexte else ()  # seulement ce que le modèle a vu
        caused_by = tuple(m.event_id for m in montres) + tuple(
            e.event_id for e in perception.evenements
        )
        reponses: list[Reponse] = []
        duree_s = 0.0
        relance = ""
        for tentative in range(1, TENTATIVES + 1):
            graine = graine_appel(self.seed, self.agent_id, perception.jour, tentative)
            debut = time.perf_counter()
            try:
                reponse = await self.routeur.generer(
                    self.palier, self.systeme, prompt + relance, schema, graine
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
                sortie = lire_sortie(reponse.texte, self.contacts, peut_parler)
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
            if sortie.message:
                self._jours_messages.append(perception.jour)
            decision = self._depuis_sortie(
                sortie, self._cout(reponses, duree_s, tentative), caused_by, incidents, sources
            )
            if await self._retenir(perception, decision, incidents):
                return decision
            return dataclasses.replace(decision, incidents=tuple(incidents))

        incidents.append(
            IncidentAgent(
                NatureIncident.REPLI, f"{TENTATIVES} échecs : décision par règle", TENTATIVES
            )
        )
        choix = repli_seuil_continuite(self.derniere_decision, perception, self.seuil)
        return self._depuis_regle(choix, self._cout(reponses, duree_s, TENTATIVES), incidents)

    def budget_caracteres(self) -> int:
        """Place laissée au prompt du jour dans le contexte du modèle, réponse comprise."""
        c = self.routeur.config
        tokens = c.contexte_tokens - c.tokens_sortie_max - SURCOUT_TOKENS
        return int(tokens * CARACTERES_PAR_TOKEN) - len(self.systeme)

    def _prompt_dans_le_budget(self, perception: Perception, restants: int | None, contexte):
        """Construit le prompt du jour sous le budget (décision du 18 oct.) : s'il déborde, on
        retire les messages anciens au-delà de 3, puis le souvenir le moins proche, puis le
        passage le moins proche. Au-delà du contexte, Ollama tronquerait le début du prompt,
        c'est-à-dire la consigne de rôle."""
        budget = self.budget_caracteres()
        messages_max = MESSAGES_MAX
        retraits = 0
        while True:
            prompt, montres = prompt_jour(
                perception, self.derniere_decision, self.contacts, self.monde, self.motif,
                restants, contexte, messages_max,
            )  # fmt: skip
            if len(prompt) <= budget:
                break
            if messages_max > 3 and len(perception.messages) > 3:
                messages_max = min(messages_max, len(perception.messages)) - 1
            elif contexte and contexte.souvenirs:
                loin = max(contexte.souvenirs, key=lambda p: p.distance)
                souvenirs = tuple(p for p in contexte.souvenirs if p is not loin)
                contexte = dataclasses.replace(contexte, souvenirs=souvenirs)
            elif contexte and len(contexte.passages) > 1:
                contexte = dataclasses.replace(contexte, passages=contexte.passages[:-1])
            elif messages_max > 1 and len(perception.messages) > 1:
                messages_max = min(messages_max, len(perception.messages)) - 1
            else:
                break  # rien de plus à retirer : on envoie tel quel
            retraits += 1
        self.retraits_prompt += retraits
        self.prompts_reduits += bool(retraits)
        return prompt, montres, contexte, retraits

    def _cout(self, reponses: list[Reponse], duree_s: float, tentatives: int) -> LlmCall:
        assert self.palier is not None
        return LlmCall(
            modele=self.routeur.modele(self.palier),
            tokens_entree=sum(r.tokens_entree for r in reponses),
            tokens_sortie=sum(r.tokens_sortie for r in reponses),
            duree_s=round(duree_s, 3),
            tentatives=tentatives,
        )

    async def _retenir(
        self, perception: Perception, decision: Decision, incidents: list[IncidentAgent]
    ) -> bool:
        """Enregistre le souvenir de la décision (A1). Un échec est un incident, pas une panne :
        il est ajouté à `incidents` et la fonction renvoie False."""
        if self.recuperateur is None:
            return True
        try:
            await self.recuperateur.retenir(self.agent_id, perception, decision)
        except Exception as exc:
            incidents.append(
                IncidentAgent(NatureIncident.SANS_REPONSE, f"mémoire : {type(exc).__name__}: {exc}")
            )
            return False
        return True

    def _depuis_sortie(
        self,
        sortie: SortieAgent,
        cout: LlmCall,
        caused_by: tuple,
        incidents: list[IncidentAgent],
        sources: tuple[str, ...] = (),
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
            sources=sources,
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
