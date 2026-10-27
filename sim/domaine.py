"""Vocabulaire fermé du monde : zones, sens, et représentation du temps simulé."""

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import Field


class Zone(StrEnum):
    """Type de zone. Le monde en compte un ou plusieurs exemplaires de chaque type (`zones`)."""

    PHARMACIE = "pharmacie"
    PLAGE = "plage"
    PISCINE = "piscine"
    RESEAUX_SOCIAUX = "reseaux_sociaux"
    CENTRE_COMMERCIAL = "centre_commercial"
    TERRAIN_SPORT = "terrain_sport"
    DOMICILE = "domicile"


# Un lieu est une zone précise du monde. Un type présent une seule fois garde son nom (« plage ») :
# les runs à sept zones sont inchangés. Présent plusieurs fois, il est numéroté (« plage_1 »,
# « plage_2 »…) et son nom seul désigne alors tous ses exemplaires (perturbation sur « plage »).
ZONES_PAR_TYPE_MAX = 4
LIEU_MOTIF = rf"^({'|'.join(z.value for z in Zone)})(_[1-{ZONES_PAR_TYPE_MAX}])?$"
_NUMERO = re.compile(r"_[0-9]+$")

Lieu = Annotated[
    str,
    Field(pattern=LIEU_MOTIF, description="Zone du monde : « plage », ou « plage_2 » s'il y en a "
          "plusieurs ; le nom du type seul désigne tous ses exemplaires."),
]  # fmt: skip


def type_de_lieu(lieu: str) -> Zone:
    """Type d'un lieu : « plage_2 » -> plage."""
    return Zone(_NUMERO.sub("", lieu))


def lieux_du_monde(nombres: Mapping[Zone, int] | None = None) -> tuple[str, ...]:
    """Lieux du monde, dans l'ordre des types : un type absent de `nombres` compte une zone."""
    lieux: list[str] = []
    for zone in Zone:
        n = (nombres or {}).get(zone, 1)
        lieux += [zone.value] if n == 1 else [f"{zone.value}_{i}" for i in range(1, n + 1)]
    return tuple(lieux)


def concerne(cible: str | None, lieu: str) -> bool:
    """Un fait daté dans `cible` touche-t-il quelqu'un qui se trouve dans `lieu` ? Sans cible,
    tout le monde ; un nom de type, tous ses exemplaires ; un lieu numéroté, lui seul."""
    return cible is None or cible == lieu or cible == type_de_lieu(lieu).value


class Sens(StrEnum):
    """Sens que l'agent attribue au produit : catégories fermées, jamais de texte libre (B1)."""

    EFFICACE = "efficace"
    TENDANCE = "tendance"
    BANAL = "banal"
    DEPASSE = "depasse"


class Canal(StrEnum):
    """Canal d'un message entre agents ; chacun a son délai de livraison (configuration)."""

    DIRECT = "direct"  # à des destinataires nommés
    ZONE = "zone"  # à toutes les personnes présentes dans la zone
    RESEAUX = "reseaux"  # publication sur les réseaux sociaux


class Produit(StrEnum):
    MARQUE = "marque"  # la crème de la marque fictive étudiée
    CONCURRENT = "concurrent"  # celle de la marque concurrente


class Action(StrEnum):
    """Action concrète décidée par un agent pour la journée."""

    ACHETER = "acheter"
    APPLIQUER = "appliquer"
    RECOMMANDER = "recommander"
    CRITIQUER = "critiquer"
    EN_PARLER = "en_parler"
    IGNORER = "ignorer"
    ABANDONNER = "abandonner"


# Le temps simulé se compte en minutes entières depuis le début de l'été (jour 1 à 00:00).
# Un entier est exact et se compare sans ambiguïté, ce qui compte pour le rejeu (E3) et la
# détection des violations temporelles (C6). Un pas de simulation dure un jour.
MINUTES_PAR_JOUR = 24 * 60

SimMinute = Annotated[int, Field(ge=0, description="Minutes simulées depuis le jour 1 à 00:00.")]

# En mode synchrone, les agents décident tous au même moment de la journée, après les
# événements du monde (publiés à 00:00). Un message émis un jour est donc lu au plus tôt le
# lendemain à cette heure, dès lors que son délai de livraison est inférieur à 24 h.
HEURE_DECISION = 10 * 60


def jour(minute: int) -> int:
    """Numéro du jour simulé (à partir de 1)."""
    return minute // MINUTES_PAR_JOUR + 1


def debut_du_jour(numero: int) -> int:
    """Minute simulée de 00:00 au jour `numero`."""
    return (numero - 1) * MINUTES_PAR_JOUR


def format_sim(minute: int) -> str:
    """Forme lisible, par exemple « J12 · 14:30 »."""
    reste = minute % MINUTES_PAR_JOUR
    return f"J{jour(minute)} · {reste // 60:02d}:{reste % 60:02d}"
