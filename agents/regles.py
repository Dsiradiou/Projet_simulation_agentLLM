"""Règles de décision sans LLM : seuil d'adoption (Granovetter) et repli des agents LLM (A4).

Le repli choisi par le binôme (18 oct.) est « seuil + continuité » : un agent LLM dont le modèle
a échoué deux fois garde son avis de la veille ; s'il n'en a pas encore, il applique la règle de
seuil, la même famille de règles que les agents à règles de l'étape 13.
"""

import dataclasses
import random
from dataclasses import dataclass

from agents.base import Agent, Decision, MessageIntent, Perception
from agents.roles import Role
from bus.events import Disturbance, Moteur, Motif, Perturbation
from sim.config import MondeConfig, ReglesConfig, SeuilConfig
from sim.domaine import Action, Canal, Sens, Zone


def tirer_seuil(rng: random.Random, loi: SeuilConfig) -> float:
    """Seuil d'adoption individuel : loi normale bornée à [0, 1]. Paramètre de l'agent tiré
    avec sa graine, jamais un attribut social (A5)."""
    return min(1.0, max(0.0, rng.gauss(loi.moyenne, loi.ecart_type)))


@dataclass(frozen=True)
class ChoixRegle:
    action: Action
    sens: Sens
    porteur: bool
    justification: str


def regle_seuil(perception: Perception, seuil: float) -> ChoixRegle:
    """Adopte si la part de porteurs dans sa zone (état de la veille) atteint son seuil.
    Adopter par imitation donne le sens « tendance » ; ne pas adopter, « banal »."""
    part = perception.part_porteurs_zone or 0.0  # témoin sans diffusion : rien de visible
    if part >= seuil:
        return ChoixRegle(
            Action.ACHETER,
            Sens.TENDANCE,
            True,
            f"Règle de seuil : {part:.0%} de porteurs autour de lui, seuil {seuil:.0%} atteint.",
        )
    return ChoixRegle(
        Action.IGNORER,
        Sens.BANAL,
        False,
        f"Règle de seuil : {part:.0%} de porteurs autour de lui, sous son seuil de {seuil:.0%}.",
    )


def repli_seuil_continuite(
    precedente: Decision | None, perception: Perception, seuil: float
) -> ChoixRegle:
    if precedente is None:
        return regle_seuil(perception, seuil)
    return ChoixRegle(
        Action.APPLIQUER if precedente.porteur else Action.IGNORER,
        precedente.sens,
        precedente.porteur,
        f"Repli : garde son avis de la veille (« {precedente.sens.value} »).",
    )


class AgentSeuil(Agent):
    """Agent à règles provisoire : applique la règle de seuil chaque fois qu'il est déclenché.
    Il tient les rôles à règles (fidèles, vendeurs, concurrent) jusqu'à l'étape 13."""

    def __init__(
        self, agent_id: str, role: Role, zone: Zone, rng: random.Random, seuil: float
    ) -> None:
        super().__init__(agent_id, role, zone, rng)
        self.seuil = seuil

    async def decider(self, perception: Perception) -> Decision:
        choix = regle_seuil(perception, self.seuil)
        return Decision(
            action=choix.action,
            sens=choix.sens,
            justification=choix.justification,
            porteur=choix.porteur,
            moteur=Moteur.REGLE,
        )


# --- agents à règles du monde complet (étape 13) ------------------------------------------------

_NATURES_CAMPAGNE = (Perturbation.PUBLICATION_VIRALE, Perturbation.ARTICLE_PRESSE)
_JOURS_DE_CHALEUR = 3  # jours de forte chaleur avec le produit pour le juger « efficace »


def _natures(perception: Perception) -> set[Perturbation]:
    return {e.payload.nature for e in perception.evenements if isinstance(e, Disturbance)}


class AgentAuSeuil(Agent):
    """Agent à règles avec flacon : adopte au seuil (Granovetter), rachète son flacon tant que la
    mode tient autour de lui, et revient à son habitude quand elle recule.

    - fidèle à une autre marque : seuil relevé (`bonus_fidelite`), et encore plus pendant une
      promotion de la marque concurrente (`bonus_promotion`) ;
    - vendeur : seuil ordinaire.
    """

    def __init__(
        self,
        agent_id: str,
        role: Role,
        zone: Zone,
        rng: random.Random,
        seuil: float,
        regles: ReglesConfig,
        bonus: float = 0.0,
    ) -> None:
        super().__init__(agent_id, role, zone, rng)
        self.seuil = seuil
        self.regles = regles
        self.bonus = bonus
        self._part_adoption = 0.0

    def seuil_du_jour(self, perception: Perception) -> float:
        promo = (
            self.regles.bonus_promotion if perception.promotion_concurrente and self.bonus else 0
        )
        return min(1.0, self.seuil + self.bonus + promo)

    async def decider(self, perception: Perception) -> Decision:
        part, seuil = perception.part_porteurs_zone or 0.0, self.seuil_du_jour(perception)
        etait_porteur = bool(self.derniere_decision and self.derniere_decision.porteur)
        recule = etait_porteur and self._part_adoption - part >= self.regles.recul_abandon
        if etait_porteur and (recule or (perception.flacon_vide and part < seuil)):
            raison = (
                f"Règle : la mode recule autour de lui ({part:.0%}), il revient à son habitude."
            )
            return self._decision(Action.ABANDONNER, Sens.BANAL, False, raison)
        if etait_porteur:
            action = Action.ACHETER if perception.flacon_vide else Action.APPLIQUER
            raison = f"Règle : {part:.0%} de porteurs autour de lui, il continue."
            return self._decision(action, Sens.TENDANCE, True, raison)
        if part >= seuil or perception.achat_rate:
            self._part_adoption = part
            raison = (
                f"Règle de seuil : {part:.0%} de porteurs autour de lui, seuil {seuil:.0%} atteint."
            )
            return self._decision(Action.ACHETER, Sens.TENDANCE, True, raison)
        raison = (
            f"Règle de seuil : {part:.0%} de porteurs autour de lui, sous son seuil de {seuil:.0%}."
        )
        return self._decision(Action.IGNORER, Sens.BANAL, False, raison)

    def _decision(self, action: Action, sens: Sens, porteur: bool, justification: str) -> Decision:
        return Decision(action, sens, justification, porteur, Moteur.REGLE)


class AgentConcurrent(Agent):
    """La marque concurrente : ne s'exprime qu'aux dates de son calendrier, sur les réseaux."""

    def __init__(
        self, agent_id: str, role: Role, zone: Zone, rng: random.Random, monde: MondeConfig
    ) -> None:
        super().__init__(agent_id, role, zone, rng)
        nom = monde.concurrent
        self.annonces = {
            21: f"{nom} à 8 euros le flacon toute la semaine au centre commercial !",
            45: f"Nouvelle formule {nom} : un fini plus léger, en rayon au jour 50.",
        }

    def doit_decider(self, perception: Perception) -> bool:
        premiere = self.derniere_decision is None
        self.motif = Motif.PREMIERE_DECISION if premiere else None
        return premiere or perception.jour in self.annonces

    async def decider(self, perception: Perception) -> Decision:
        annonce = self.annonces.get(perception.jour)
        messages = (MessageIntent(Canal.RESEAUX, annonce),) if annonce else ()
        return Decision(
            Action.CRITIQUER if annonce else Action.IGNORER,
            Sens.BANAL,
            "Calendrier de la marque concurrente." if annonce else "Marque concurrente.",
            False,
            Moteur.REGLE,
            messages=messages,
        )


# Messages types de la baseline : même réseau et mêmes canaux que les agents LLM du même rôle.
_CANAL_DU_ROLE = {
    Role.CREATEUR_CONTENU: Canal.RESEAUX,
    Role.JOURNALISTE: Canal.RESEAUX,
    Role.RESPONSABLE_MARQUE: Canal.RESEAUX,
    Role.PHARMACIEN: Canal.ZONE,
    Role.HABITUE_SOIN: Canal.DIRECT,
    Role.BAIGNEUR_OCCASIONNEL: Canal.ZONE,
    Role.SCEPTIQUE: Canal.DIRECT,
}
_PHRASES = {
    Sens.TENDANCE: "Tout le monde a {marque} en ce moment, je m'y mets aussi.",
    Sens.EFFICACE: "{marque} tient bien par cette chaleur, je la recommande.",
    Sens.DEPASSE: "{marque}, c'est fini pour moi : plus personne n'en met.",
    Sens.BANAL: "Avec ce qu'on lit sur {marque}, je laisse tomber.",
}


class AgentRegleC0(AgentAuSeuil):
    """Baseline C0 (E1) pour les rôles LLM : seuil + Bass + déclin, sans aucun appel de modèle.

    - adoption par imitation (Granovetter) : la part de porteurs autour de lui, augmentée de
      `q_message` par message reçu (au plus 3, Bass q), atteint son seuil ;
    - adoption par influence externe (Bass p) : probabilité `p_externe` chaque jour, `p_campagne`
      les jours de communiqué de la marque, de publication virale ou d'article de presse ;
    - déclin (Berger, Le Mens) : quand la mode recule autour de lui, il abandonne (« dépassé »),
      sûrement si son flacon est fini, sinon avec `p_abandon_recul` ; une polémique le fait
      abandonner avec `p_abandon_polemique` ; un flacon fini sans pression sociale, une fois sur
      deux ;
    - sens : « tendance » s'il a imité, « efficace » s'il a été convaincu par la marque ou après
      plusieurs jours de forte chaleur avec le produit, « dépassé » s'il a abandonné une mode ;
    - messages types sur le canal de son rôle quand il adopte ou abandonne, dans le même budget
      hebdomadaire que les agents LLM.
    """

    def __init__(
        self,
        agent_id: str,
        role: Role,
        zone: Zone,
        rng: random.Random,
        seuil: float,
        regles: ReglesConfig,
        monde: MondeConfig,
        contacts: list[str],
        jours_campagne: set[int],
        messages_par_semaine: int,
    ) -> None:
        super().__init__(agent_id, role, zone, rng, seuil, regles)
        self.palier = None  # agent à règles : aucun modèle à charger, aucun palier
        self.monde = monde
        self.contacts = contacts
        self.jours_campagne = jours_campagne
        self.messages_par_semaine = messages_par_semaine
        self._jours_messages: list[int] = []
        self._jours_chauds = 0

    async def decider(self, p: Perception) -> Decision:
        r, rng = self.regles, self.rng
        natures = _natures(p)
        if p.meteo and p.meteo.conditions == "canicule" and self.porteur:
            self._jours_chauds += 1
        part = p.part_porteurs_zone or 0.0
        pression = part + r.q_message * min(3, len(p.messages))
        etait_porteur = bool(self.derniere_decision and self.derniere_decision.porteur)
        if etait_porteur:
            recule = self._part_adoption - part >= r.recul_mode
            if Perturbation.POLEMIQUE in natures and rng.random() < r.p_abandon_polemique:
                return self._avec_message(p, Action.ABANDONNER, Sens.BANAL, False, "polémique")
            if recule and (p.flacon_vide or rng.random() < r.p_abandon_recul):
                return self._avec_message(
                    p, Action.ABANDONNER, Sens.DEPASSE, False, "mode qui recule"
                )
            if p.flacon_vide and pression < self.seuil and rng.random() < 0.5:
                return self._avec_message(p, Action.ABANDONNER, Sens.BANAL, False, "flacon fini")
            chaleur = self._jours_chauds >= _JOURS_DE_CHALEUR
            sens = Sens.EFFICACE if chaleur else self.sens or Sens.TENDANCE
            action = Action.ACHETER if p.flacon_vide else Action.APPLIQUER
            return self._decision(
                action, sens, True, f"Règle C0 : il continue ({part:.0%} autour de lui)."
            )
        if pression >= self.seuil or p.achat_rate:
            self._part_adoption, self._jours_chauds = part, 0
            return self._avec_message(p, Action.ACHETER, Sens.TENDANCE, True, "imitation")
        campagne = p.jour in self.jours_campagne or bool(natures & set(_NATURES_CAMPAGNE))
        if rng.random() < (r.p_campagne if campagne else r.p_externe):
            self._part_adoption, self._jours_chauds = part, 0
            return self._avec_message(p, Action.ACHETER, Sens.EFFICACE, True, "influence externe")
        sens = Sens.DEPASSE if self.sens is Sens.DEPASSE else Sens.BANAL
        raison = f"Règle C0 : {pression:.0%} de pression sociale, sous son seuil."
        return self._decision(Action.IGNORER, sens, False, raison)

    def _avec_message(
        self, p: Perception, action: Action, sens: Sens, porteur: bool, raison: str
    ) -> Decision:
        decision = self._decision(action, sens, porteur, f"Règle C0 : {raison}.")
        recents = sum(1 for j in self._jours_messages if p.jour - j < 7)
        canal = _CANAL_DU_ROLE.get(self.role)
        if canal is None or recents >= self.messages_par_semaine:
            return decision
        if canal is Canal.DIRECT and not self.contacts:
            canal = Canal.ZONE
        destinataires = (self.rng.choice(self.contacts),) if canal is Canal.DIRECT else ()
        texte = _PHRASES[sens].format(marque=self.monde.brand_name)
        self._jours_messages.append(p.jour)
        return dataclasses.replace(decision, messages=(MessageIntent(canal, texte, destinataires),))
