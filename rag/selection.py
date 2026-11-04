"""Mémoire sélective : ce qui entre dans le prompt d'un agent (début de l'étape 16, formule
décidée par le binôme le 4 nov.).

Le choix se fait en quatre temps, pour chacune des trois sources (corpus, mémoire personnelle,
mémoire collective) :

a) **filtres durs**, dans la requête ChromaDB : date (C6), rôle, auteur, portée sociale ; puis ce
   qui est déjà dans la perception du jour ; on garde `memoire.candidats` candidats par source ;
b) **un score**, chaque terme étant déjà entre 0 et 1 (pas de mise à l'échelle entre candidats :
   un score garde le même sens d'une décision à l'autre) :

       score = [α·pertinence + β·rétention + γ·importance + δ·nouveauté + ε·proximité
                − λ·L·redondance] × accès

   - pertinence : 1 − distance cosinus avec la question du jour ;
   - rétention : 0,5^(âge / demi-vie effective), demi-vie effective = demi-vie × (1 + κ·importance),
     l'âge comptant depuis le dernier rappel (un souvenir montré au modèle repart à 1) ; sous
     `seuil_retention`, le souvenir n'est plus candidat mais reste en base (F6) ;
   - importance : importance du souvenir ÷ la plus forte de l'agent ;
   - nouveauté : 1, ou `nouveaute_deja_vu` si l'élément a été montré depuis moins de
     `nouveaute_jours` ;
   - proximité (mémoire collective) : l'auteur est un proche, un compte suivi, ou le fait vient
     de sa zone ;
   - redondance : ressemblance la plus forte avec ce qui lui a été montré récemment, pénalisée
     par sa lassitude L ;
   - accès : sa notoriété A. Un agent qui a oublié le produit ne décide pas : quand il décide,
     A est au-dessus du seuil et ce facteur, commun à tous les candidats, ne change pas
     l'ordre. À 0, rien ne remonte ;
c) **sans doublons** : un candidat trop semblable à un élément déjà choisi est écarté ;
d) **un quota par source** (`rag.passages`, `rag.souvenirs`, `rag.collectif`).

Les égalités sont départagées par le jour (le plus récent d'abord) puis la source : un ordre
reproductible, jamais celui de ChromaDB.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from sim.config import MemoireConfig

if TYPE_CHECKING:  # rag.base importe ce module
    from rag.base import Passage


@dataclass(frozen=True)
class Candidat:
    """Un élément retrouvé, avec son vecteur et les termes du score propres à sa source."""

    passage: "Passage"
    vecteur: tuple[float, ...]
    retention: float = 1.0
    importance: float = 0.0
    proximite: float = 0.0


def cosinus(a: Sequence[float], b: Sequence[float]) -> float:
    norme = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norme if norme else 0.0


def retention(age: float, importance: float, reglage: MemoireConfig) -> float:
    """Ce qui reste d'un élément après `age` jours sans rappel ; `importance` entre 0 et 1."""
    demi_vie = reglage.demi_vie_jours * (1 + reglage.kappa * importance)
    return 0.5 ** (max(0.0, age) / demi_vie)


@dataclass
class Historique:
    """Ce qui a été montré à un agent : jour du dernier affichage de chaque source, et vecteurs
    des éléments montrés (redondance)."""

    vus: dict[str, int] = field(default_factory=dict)
    vecteurs: dict[str, tuple[int, tuple[float, ...]]] = field(default_factory=dict)

    def noter(self, jour: int, montres: Iterable[Candidat]) -> None:
        for c in montres:
            self.vus[c.passage.source] = jour
            self.vecteurs[c.passage.source] = (jour, c.vecteur)

    def recents(self, jour: int, fenetre: int) -> list[tuple[float, ...]]:
        return [v for source, (j, v) in sorted(self.vecteurs.items()) if jour - j < fenetre]


def choisir(
    candidats: Sequence[Candidat],
    k: int,
    jour: int,
    reglage: MemoireConfig,
    historique: Historique,
    lassitude: float = 0.0,
    acces: float = 1.0,
) -> list[Candidat]:
    """Les `k` candidats les mieux notés, sans doublons, du plus au moins utile. Le score et la
    rétention sont recopiés dans le `Passage` (journal de récupération, réduction du prompt).

    `acces` est la notoriété A de l'agent (gardée dans la formule, décision M5 du 4 nov.). Elle
    multiplie tous les scores par la même valeur : elle ne change pas l'ordre et agit comme une
    porte. À 0, rien ne revient ; au-dessus, tout revient comme sans elle. Un agent qui a oublié
    le produit ne décide pas : l'oubli agit d'abord par le déclencheur (`Agent._motif`)."""
    if k <= 0 or acces <= 0 or not candidats:
        return []
    p = reglage.poids
    recents = historique.recents(jour, reglage.nouveaute_jours) if lassitude else []
    notes: list[Candidat] = []
    for c in candidats:
        vu = historique.vus.get(c.passage.source)
        deja_vu = vu is not None and jour - vu < reglage.nouveaute_jours
        nouveaute = reglage.nouveaute_deja_vu if deja_vu else 1.0
        redondance = max((cosinus(c.vecteur, v) for v in recents), default=0.0)
        score = (
            p.pertinence * min(1.0, max(0.0, 1.0 - c.passage.distance))
            + p.retention * c.retention
            + p.importance * c.importance
            + p.nouveaute * nouveaute
            + p.proximite * c.proximite
            - p.redondance * lassitude * redondance
        ) * acces
        passage = replace(c.passage, score=round(score, 9), retention=round(c.retention, 6))
        notes.append(replace(c, passage=passage))
    notes.sort(key=lambda c: (-c.passage.score, -c.passage.jour, c.passage.source))
    choisis: list[Candidat] = []
    for c in notes:
        if len(choisis) == k:
            break
        if all(cosinus(c.vecteur, d.vecteur) < reglage.doublon for d in choisis):
            choisis.append(c)
    return choisis
