"""Rôles des acteurs du monde (voir docs/contexte-projet.md, section 5)."""

from enum import StrEnum


class Palier(StrEnum):
    """Taille du modèle LLM attribuée à un rôle (A5). Les agents à règles n'ont pas de palier."""

    GRAND = "grand"
    MOYEN = "moyen"
    PETIT = "petit"


class Role(StrEnum):
    """Rôle d'un agent. Il détermine son palier de modèle, jamais une catégorie sociale (A5)."""

    # Agents LLM
    PHARMACIEN = "pharmacien"
    CREATEUR_CONTENU = "createur_contenu"
    JOURNALISTE = "journaliste"
    RESPONSABLE_MARQUE = "responsable_marque"
    HABITUE_SOIN = "habitue_soin"
    BAIGNEUR_OCCASIONNEL = "baigneur_occasionnel"
    SCEPTIQUE = "sceptique"
    # Agents à règles
    FIDELE_AUTRE_MARQUE = "fidele_autre_marque"
    VENDEUR = "vendeur"
    MARQUE_CONCURRENTE = "marque_concurrente"
    METEO = "meteo"


# Attribution fixée par CLAUDE.md (A5) : selon le rôle et l'expérience, jamais selon une
# catégorie sociale. Un rôle absent de ce tableau est tenu par un agent à règles.
PALIER_PAR_ROLE: dict[Role, Palier] = {
    Role.PHARMACIEN: Palier.GRAND,
    Role.CREATEUR_CONTENU: Palier.MOYEN,
    Role.JOURNALISTE: Palier.MOYEN,
    Role.RESPONSABLE_MARQUE: Palier.MOYEN,
    Role.HABITUE_SOIN: Palier.MOYEN,
    Role.BAIGNEUR_OCCASIONNEL: Palier.PETIT,
    Role.SCEPTIQUE: Palier.PETIT,
}


def palier_du_role(role: Role) -> Palier | None:
    return PALIER_PAR_ROLE.get(role)
