"""Notoriété A et lassitude L d'un agent (début de l'étape 16, décidé par le binôme le 4 nov.).

Deux chemins vers le déclin d'une mode :
- **l'oubli** (notoriété A) : l'agent ne pense plus au produit, un effacement passif.
  A(j) = A(j−1)·2^(−1/h) + renforts du jour, plafonnée à 1. Le renfort des porteurs vus est
  plafonné (`porteurs_vus_max`, décidé le 4 nov.) : sans plafond, dans une zone de 8 porteurs,
  A remontait de 0,8 par jour et personne n'oubliait une mode visible. La demi-vie h est un
  paramètre individuel tiré avec la graine (générateur à part), jamais d'après un attribut
  social (A5). Un porteur ne l'oublie pas : A reste à 1. Sous le seuil s, l'agent n'est plus
  relancé et ne coûte aucun appel ; une exposition qui le fait repasser au-dessus déclenche une
  décision.
- **la lassitude** (L) : il le connaît mais en a assez, un rejet actif.
  L(j) = L(j−1)·(1 − récupération) + taux·exposition, l'exposition du jour valant entre 0 et 1.

Au jour 1, seule une part des agents connaît le produit (`oubli.connus_jour_1`) : les autres le
découvrent par exposition, comme l'innovation externe du modèle de Bass. L'oubli imite la
différence entre le rappel libre (l'agent pense seul au produit) et la reconnaissance (il s'en
souvient quand on le lui remontre).

Les mêmes règles s'appliquent en C0 et en C1, avec la même graine (E1) : seule la décision change.
L'orchestrateur met l'état à jour une fois par jour, au passage de l'agent, avant de savoir s'il
décide ; chaque passage du seuil est publié (`agent.notoriete`) et compté dans le bilan.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from bus.events import Disturbance, Notoriete, Perturbation
from sim.config import LassitudeConfig, OubliConfig
from sim.domaine import Canal
from sim.run import agent_rng

if TYPE_CHECKING:
    from agents.base import Perception

# Perturbations qui parlent du produit : elles le remettent en tête de ceux qui les perçoivent.
PERTURBATIONS_DU_PRODUIT = frozenset(
    {
        Perturbation.ARTICLE_PRESSE,
        Perturbation.PUBLICATION_VIRALE,
        Perturbation.POLEMIQUE,
        Perturbation.RUPTURE_STOCK,
    }
)
DECIMALES = 6  # valeurs publiées et gardées arrondies : mêmes chiffres en direct et au rejeu
MESSAGES_PLEINE_EXPOSITION = 3  # au-delà, des messages de plus n'ajoutent rien à la lassitude


@dataclass
class EtatProduit:
    """Ce que l'agent a en tête du produit. `connu` : A au-dessus du seuil après le dernier jour."""

    notoriete: float
    connu: bool
    deja_connu: bool  # il y a déjà pensé : une remontée est une redécouverte
    demi_vie: float = 10.0
    lassitude: float = 0.0
    transition: Notoriete | None = None  # passage du seuil lors de la dernière mise à jour
    causes: tuple[UUID, ...] = ()  # expositions qui ont provoqué une (re)découverte
    # Habitude des expositions ambiantes (rayon, porteurs vus), entre 0 et 1 : moyenne glissante
    # des jours où elles étaient là. Une exposition familière ne rappelle plus grand-chose.
    familiarite_rayon: float = 0.0
    familiarite_porteurs: float = 0.0
    _vues: set[UUID] = field(default_factory=set)  # expositions déjà comptées

    @property
    def decouvre(self) -> bool:
        return self.transition in (Notoriete.DECOUVERTE, Notoriete.REDECOUVERTE)


def etat_initial(oubli: OubliConfig, seed: int, agent_id: str) -> EtatProduit:
    """État de départ. Tiré avec un générateur propre (`<agent>:notoriete`) : les autres tirages
    de l'agent (zone, contacts, seuil, décisions de C0) restent ceux d'un run sans oubli."""
    if not oubli.actif:
        return EtatProduit(1.0, connu=True, deja_connu=True)
    rng = agent_rng(seed, f"{agent_id}:notoriete")
    connu = rng.random() < oubli.connus_jour_1
    demi_vie = max(1.0, rng.gauss(oubli.demi_vie_jours, oubli.demi_vie_ecart))
    return EtatProduit(1.0 if connu else 0.0, connu, connu, demi_vie=demi_vie)


def vivre_jour(
    etat: EtatProduit,
    perception: "Perception",
    *,
    porteur: bool,
    contacts: list[str],
    oubli: OubliConfig,
    lassitude: LassitudeConfig,
) -> None:
    """Met A et L à jour avec les expositions du jour. Un message ou une perturbation déjà
    comptés (relus parce que l'agent n'a pas décidé) ne comptent pas deux fois."""
    messages = [m for m in perception.messages if m.event_id not in etat._vues]
    articles = [
        e
        for e in perception.evenements
        if isinstance(e, Disturbance)
        and e.payload.nature in PERTURBATIONS_DU_PRODUIT
        and e.event_id not in etat._vues
    ]
    etat._vues.update(x.event_id for x in (*messages, *articles))
    etat.transition, etat.causes = None, ()

    if oubli.actif:
        r = oubli.renforts
        renforts = sum(
            r.proche
            if m.payload.canal is Canal.DIRECT or m.source_agent_id in contacts
            else r.publication
            for m in messages
        )
        renforts += r.article * len(articles)
        # Expositions ambiantes : leur effet baisse quand elles se répètent (habituation).
        vus = perception.porteurs_vus > 0
        renforts += min(r.porteur_vu * perception.porteurs_vus, r.porteurs_vus_max) * (
            1 - etat.familiarite_porteurs
        )
        renforts += r.rayon * (1 - etat.familiarite_rayon) if perception.rayon else 0.0
        h = oubli.habituation
        etat.familiarite_porteurs = round(
            etat.familiarite_porteurs + h * (vus - etat.familiarite_porteurs), DECIMALES
        )
        etat.familiarite_rayon = round(
            etat.familiarite_rayon + h * (perception.rayon - etat.familiarite_rayon), DECIMALES
        )
        if porteur:
            a = 1.0
        else:
            a = min(1.0, etat.notoriete * 2 ** (-1 / etat.demi_vie) + renforts)
        a = round(a, DECIMALES)
        # Hystérésis : on oublie sous `seuil_oubli`, on (re)découvre au-dessus de `seuil`.
        bas = oubli.seuil if oubli.seuil_oubli is None else oubli.seuil_oubli
        connu = a >= (bas if etat.connu else oubli.seuil)
        if connu and not etat.connu:
            etat.transition = Notoriete.REDECOUVERTE if etat.deja_connu else Notoriete.DECOUVERTE
            etat.causes = tuple(x.event_id for x in (*messages, *articles))
        elif etat.connu and not connu:
            etat.transition = Notoriete.OUBLI
        etat.notoriete, etat.connu = a, connu
        etat.deja_connu = etat.deja_connu or connu

    if lassitude.actif:
        exposition = (
            min(len(messages), MESSAGES_PLEINE_EXPOSITION) / MESSAGES_PLEINE_EXPOSITION
            + (perception.part_porteurs_zone or 0.0)
            + float(porteur)
        ) / 3
        valeur = etat.lassitude * (1 - lassitude.recuperation) + lassitude.taux * exposition
        etat.lassitude = round(min(1.0, valeur), DECIMALES)


def niveau_de_lassitude(valeur: float, niveaux: tuple[float, float, float]) -> int:
    """0 (pas de lassitude sensible) à 3 (il en a assez) : choisit la phrase du prompt."""
    return sum(valeur >= seuil for seuil in niveaux)
