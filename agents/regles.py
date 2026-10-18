"""Règles de décision sans LLM : seuil d'adoption (Granovetter) et repli des agents LLM (A4).

Le repli choisi par le binôme (18 oct.) est « seuil + continuité » : un agent LLM dont le modèle
a échoué deux fois garde son avis de la veille ; s'il n'en a pas encore, il applique la règle de
seuil, la même famille de règles que les agents à règles de l'étape 13.
"""

import random
from dataclasses import dataclass

from agents.base import Decision, Perception
from sim.config import SeuilConfig
from sim.domaine import Action, Sens


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
    part = perception.part_porteurs_zone
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
