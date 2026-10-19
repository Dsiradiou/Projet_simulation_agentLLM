"""Lecture du corpus fictif (`corpus/documents/*.md`) : métadonnées et texte de chaque document.

Chaque document commence par un en-tête YAML : `id`, `type`, `titre`, `jour` (jour simulé de
publication, 0 = avant l'été), `visibilite` (`tous` ou un rôle), `zone` (facultatif), `auteur`.
Les noms du monde sont des gabarits (`{marque}`, `{ingredient}`, `{concurrent}`) remplacés à la
lecture par la configuration : aucun nom n'est écrit en dur dans les textes.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from agents.roles import Role
from sim.config import MondeConfig
from sim.domaine import Zone

DOSSIER = Path("corpus/documents")
TYPES = {
    "fiche_produit": "fiche produit",
    "communique": "communiqué",
    "avis_client": "avis client",
    "article": "article",
    "conseil": "conseil",
    "note_interne": "note interne",
    "concurrent": "concurrent",
}


class CorpusInvalide(ValueError):
    """Un document du corpus est mal formé."""


@dataclass(frozen=True)
class Document:
    id: str
    type: str
    titre: str
    jour: int
    visibilite: Role | None  # None : lisible par tous
    zone: Zone | None
    auteur: str
    texte: str

    def lisible_par(self, role: Role) -> bool:
        return self.visibilite is None or self.visibilite is role

    def passage(self) -> str:
        """Texte indexé et montré à l'agent : titre et contenu."""
        return f"{self.titre}. {self.texte}"


def remplacer(texte: str, monde: MondeConfig) -> str:
    return (
        texte.replace("{marque}", monde.brand_name)
        .replace("{ingredient}", monde.ingredient)
        .replace("{concurrent}", monde.concurrent)
    )


def lire_document(chemin: Path, monde: MondeConfig) -> Document:
    brut = chemin.read_text(encoding="utf-8")
    try:
        _, entete, texte = brut.split("---", 2)
        meta = yaml.safe_load(entete)
        visibilite = meta["visibilite"]
        if meta["type"] not in TYPES:
            raise ValueError(f"type inconnu : {meta['type']}")
        return Document(
            id=meta["id"],
            type=meta["type"],
            titre=remplacer(meta["titre"], monde),
            jour=int(meta["jour"]),
            visibilite=None if visibilite == "tous" else Role(visibilite),
            zone=Zone(meta["zone"]) if meta.get("zone") else None,
            auteur=remplacer(meta["auteur"], monde),
            texte=remplacer(texte.strip(), monde),
        )
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise CorpusInvalide(f"{chemin.name} : {exc}") from exc


def lire_corpus(monde: MondeConfig, dossier: Path = DOSSIER) -> list[Document]:
    documents = [lire_document(f, monde) for f in sorted(dossier.glob("*.md"))]
    ids = [d.id for d in documents]
    if len(set(ids)) != len(ids):
        raise CorpusInvalide("deux documents portent le même identifiant")
    return documents
