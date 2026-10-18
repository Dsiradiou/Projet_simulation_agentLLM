"""Réponse imposée à un agent LLM (règle 5) : schéma JSON passé à Ollama et lecture validée.

Le schéma est transmis au paramètre `format` d'Ollama, qui contraint la génération : le modèle
ne peut écrire qu'un JSON de cette forme, avec des valeurs prises dans les catégories fermées.
La lecture revalide tout avec Pydantic, car la contrainte ne couvre pas tout (texte vide,
destinataire direct manquant si le schéma est modifié...).

Ordre des champs : la justification vient en premier. Ollama génère les champs dans l'ordre
du schéma ; le modèle écrit donc sa raison avant de choisir son sens et son action, au lieu de
justifier après coup un choix déjà fait.
"""

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sim.domaine import Action, Canal, Sens

# Garde-fous de longueur. La contrainte de longueur d'Ollama coupe le texte net, en plein mot :
# c'est le prompt qui demande des réponses courtes, ces plafonds ne servent qu'en dernier recours.
LONGUEUR_JUSTIFICATION = 300
LONGUEUR_MESSAGE = 280


class SortieInvalide(ValueError):
    """La réponse du modèle ne respecte pas le schéma imposé."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MessageSortie(_Strict):
    canal: Canal
    contenu: str = Field(min_length=1, max_length=LONGUEUR_MESSAGE)
    destinataire: str | None = None

    @model_validator(mode="after")
    def _destinataire_si_direct(self) -> "MessageSortie":
        if (self.canal is Canal.DIRECT) != (self.destinataire is not None):
            raise ValueError("un destinataire est requis pour un message direct, et seulement là")
        return self


class SortieAgent(_Strict):
    justification: str = Field(min_length=1, max_length=LONGUEUR_JUSTIFICATION)
    sens: Sens
    action: Action
    message: MessageSortie | None = None


def schema_sortie(contacts: Sequence[str], message_possible: bool = True) -> dict:
    """Schéma JSON propre à un agent : un message direct ne peut viser qu'un de ses contacts.

    `message` vaut null (rien dit), une diffusion (zone ou réseaux) ou un message direct. Les
    deux formes de message sont distinctes pour que le destinataire soit imposé en direct. Quand
    l'agent a épuisé son budget de messages de la semaine, seul null est possible.
    """
    diffusion = {
        "type": "object",
        "properties": {
            "canal": {"type": "string", "enum": [Canal.ZONE.value, Canal.RESEAUX.value]},
            "contenu": {"type": "string", "maxLength": LONGUEUR_MESSAGE},
        },
        "required": ["canal", "contenu"],
    }
    formes: list[dict] = [{"type": "null"}]
    if message_possible:
        formes.append(diffusion)
    if contacts and message_possible:
        formes.append(
            {
                "type": "object",
                "properties": {
                    "canal": {"type": "string", "enum": [Canal.DIRECT.value]},
                    "destinataire": {"type": "string", "enum": list(contacts)},
                    "contenu": {"type": "string", "maxLength": LONGUEUR_MESSAGE},
                },
                "required": ["canal", "destinataire", "contenu"],
            }
        )
    return {
        "type": "object",
        "properties": {
            "justification": {"type": "string", "maxLength": LONGUEUR_JUSTIFICATION},
            "sens": {"type": "string", "enum": [s.value for s in Sens]},
            "action": {"type": "string", "enum": [a.value for a in Action]},
            "message": {"anyOf": formes},
        },
        "required": ["justification", "sens", "action", "message"],
    }


def lire_sortie(texte: str, contacts: Sequence[str], message_possible: bool = True) -> SortieAgent:
    """Valide la réponse brute du modèle. Lève `SortieInvalide` avec la raison, réutilisée
    dans le prompt de relance et dans l'incident publié."""
    try:
        sortie = SortieAgent.model_validate(json.loads(texte))
    except json.JSONDecodeError as exc:
        raise SortieInvalide(f"JSON illisible : {exc.msg}") from exc
    except ValidationError as exc:
        erreurs = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'réponse'} : {e['msg']}" for e in exc.errors()
        )
        raise SortieInvalide(erreurs) from exc
    if sortie.message and not message_possible:
        raise SortieInvalide("budget de messages épuisé : message doit valoir null")
    if sortie.message and sortie.message.destinataire not in (None, *contacts):
        raise SortieInvalide(f"destinataire inconnu : {sortie.message.destinataire}")
    return sortie
