"""Horloge simulée explicite (C3), horloge de phase du mode asynchrone (C4) et ordre de passage
des paliers."""

import time

from agents.roles import Palier
from sim.domaine import HEURE_DECISION, debut_du_jour


class SimClock:
    """Temps simulé courant, en minutes entières. Il n'avance que sur ordre de l'orchestrateur,
    jamais au rythme de l'horloge réelle."""

    def __init__(self, duree_jours: int) -> None:
        self.duree_jours = duree_jours
        self.jour = 0
        self.minute = 0

    def commencer_jour(self, jour: int) -> int:
        if not 1 <= jour <= self.duree_jours:
            raise ValueError(f"jour {jour} hors de la durée simulée (1 à {self.duree_jours})")
        if jour <= self.jour:
            raise ValueError("le temps simulé ne recule pas")
        self.jour = jour
        self.minute = debut_du_jour(jour)
        return self.minute

    def heure_decision(self) -> int:
        self.minute = debut_du_jour(self.jour) + HEURE_DECISION
        return self.minute

    def maintenant(self) -> int:
        return self.minute


class HorlogePhase:
    """Heure simulée d'une phase de la journée asynchrone (C4) : elle part de `debut` et avance
    avec le temps réel de calcul, à raison de `minutes_par_seconde`. Le temps passé à charger un
    modèle en est retiré (`decaler`) : c'est la carte graphique, pas l'agent, qui le perd.
    Elle ne recule jamais et s'arrête à `limite`, avant les événements du soir."""

    def __init__(self, debut: int, minutes_par_seconde: float, limite: int) -> None:
        self.debut = debut
        self.minutes_par_seconde = minutes_par_seconde
        self.limite = limite
        self._t0 = time.perf_counter()
        self._exclu_s = 0.0
        self._derniere = debut

    def decaler(self, secondes: float) -> None:
        self._exclu_s += secondes

    def maintenant(self) -> int:
        ecoule = time.perf_counter() - self._t0 - self._exclu_s
        minute = self.debut + int(ecoule * self.minutes_par_seconde)
        self._derniere = min(self.limite, max(self._derniere, minute))
        return self._derniere


def ordre_paliers(jour: int) -> list[Palier]:
    """Ordre de passage des paliers LLM dans la journée.

    Le grand modèle ne tient pas en VRAM avec les deux autres (étape 3 : un aller-retour coûte
    environ 13 s). En alternant l'ordre d'un jour à l'autre, deux jours consécutifs se touchent
    par le même palier : le modèle chargé en fin de journée sert au début de la suivante, ce qui
    divise par deux le nombre de bascules. La barrière synchrone rend cet ordre sans effet sur
    les décisions : chaque agent ne voit que l'état de la veille.
    """
    if jour % 2 == 1:
        return [Palier.PETIT, Palier.MOYEN, Palier.GRAND]
    return [Palier.GRAND, Palier.MOYEN, Palier.PETIT]
