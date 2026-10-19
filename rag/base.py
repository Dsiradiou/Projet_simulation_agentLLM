"""Base de connaissances ChromaDB (étape 12) : corpus du monde et mémoires des agents (F1, A1).

Deux collections, deux usages :
- **corpus** : les documents fictifs du monde, indexés une fois par modèle d'embeddings (l'index
  est réutilisé tant que le corpus et la configuration du monde ne changent pas) ;
- **mémoires** : les épisodes vécus par chaque agent LLM pendant un run, ajoutés après chaque
  décision.

Toute recherche filtre sur la date simulée (C6) : un document n'est retrouvable qu'à partir de
son jour de publication, un souvenir qu'à partir du lendemain. Le corpus filtre aussi sur le rôle
(une note interne aux vendeurs n'est lue que par eux). ChromaDB tourne dans le processus Python :
aucun service de plus à lancer.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from agents.roles import Role
from rag.corpus import Document
from rag.embeddings import Embeddeur

DOSSIER_INDEX = Path("data/chroma")


def client_persistant(dossier: Path = DOSSIER_INDEX) -> chromadb.ClientAPI:
    dossier.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(dossier), settings=ChromaSettings(anonymized_telemetry=False)
    )


def client_memoire_vive() -> chromadb.ClientAPI:
    return chromadb.EphemeralClient(settings=ChromaSettings(anonymized_telemetry=False))


def _nom_collection(prefixe: str, modele: str) -> str:
    return f"{prefixe}__{re.sub(r'[^a-zA-Z0-9]+', '-', modele).strip('-')}"[:63]


@dataclass(frozen=True)
class Passage:
    """Un élément retrouvé : document du corpus ou souvenir, avec sa distance à la question."""

    source: str  # « corpus:fiche-produit » ou « memoire:habitue_soin_03:j5 »
    texte: str
    jour: int
    type: str
    distance: float


class BaseCorpus:
    def __init__(self, client: chromadb.ClientAPI, embeddeur: Embeddeur) -> None:
        self.client = client
        self.embeddeur = embeddeur
        self.collection: chromadb.Collection | None = None

    @staticmethod
    def empreinte(documents: list[Document]) -> str:
        """Empreinte du corpus indexé : un texte ou un nom modifié impose de réindexer."""
        h = hashlib.sha256()
        for d in documents:
            h.update(f"{d.id}|{d.jour}|{d.visibilite}|{d.passage()}".encode())
        return h.hexdigest()[:16]

    async def indexer(self, documents: list[Document]) -> bool:
        """Indexe le corpus, sauf s'il l'est déjà tel quel pour ce modèle (l'empreinte du corpus
        fait partie du nom de la collection). Renvoie True si l'index a été construit."""
        nom = _nom_collection(f"corpus-{self.empreinte(documents)}", self.embeddeur.modele)
        suffixe = nom.split("__", 1)[1]
        for ancienne in self.client.list_collections():  # index périmés de ce modèle
            nom_ancien = getattr(ancienne, "name", ancienne)
            if (
                nom_ancien.startswith("corpus-")
                and nom_ancien.endswith(suffixe)
                and nom_ancien != nom
            ):
                self.client.delete_collection(nom_ancien)
        self.collection = self.client.get_or_create_collection(
            nom, metadata={"hnsw:space": "cosine"}
        )
        if self.collection.count() == len(documents):
            return False
        vecteurs = await self.embeddeur.documents([d.passage() for d in documents])
        self.collection.add(
            ids=[d.id for d in documents],
            embeddings=vecteurs,
            documents=[d.passage() for d in documents],
            metadatas=[
                {
                    "jour": d.jour,
                    "type": d.type,
                    "tous": d.visibilite is None,
                    "role": d.visibilite.value if d.visibilite else "",
                    "zone": d.zone.value if d.zone else "",
                }
                for d in documents
            ],
        )
        return True

    async def rechercher(self, question: str, jour: int, role: Role, k: int = 4) -> list[Passage]:
        (vecteur,) = await self.embeddeur.questions([question])
        return self.rechercher_vecteur(vecteur, jour, role, k)

    def rechercher_vecteur(
        self, vecteur: list[float], jour: int, role: Role, k: int = 4
    ) -> list[Passage]:
        filtre = {
            "$and": [
                {"jour": {"$lte": jour}},  # rien du futur (C6)
                {"$or": [{"tous": True}, {"role": role.value}]},  # rien hors de son rôle
            ]
        }
        if self.collection is None:
            raise RuntimeError("corpus non indexé : appeler indexer() d'abord")
        r = self.collection.query(query_embeddings=[vecteur], n_results=k, where=filtre)
        return [
            Passage(f"corpus:{i}", texte, int(meta["jour"]), str(meta["type"]), float(dist))
            for i, texte, meta, dist in zip(
                r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0], strict=True
            )
        ]


class Memoires:
    """Épisodes vécus par les agents d'un run. Chaque agent ne retrouve que les siens (A1)."""

    def __init__(self, client: chromadb.ClientAPI, embeddeur: Embeddeur, run_id: str) -> None:
        self.embeddeur = embeddeur
        self.collection = client.get_or_create_collection(
            _nom_collection(f"memoires-{run_id}", embeddeur.modele),
            metadata={"hnsw:space": "cosine"},
        )

    async def ajouter(self, agent_id: str, jour: int, texte: str, importance: int) -> str:
        source = f"memoire:{agent_id}:j{jour}"
        (vecteur,) = await self.embeddeur.documents([texte])
        self.collection.upsert(
            ids=[source],
            embeddings=[vecteur],
            documents=[texte],
            metadatas=[{"agent_id": agent_id, "jour": jour, "importance": importance}],
        )
        return source

    def rechercher_vecteur(
        self, vecteur: list[float], agent_id: str, jour: int, k: int = 3
    ) -> list[Passage]:
        filtre = {"$and": [{"agent_id": agent_id}, {"jour": {"$lt": jour}}]}  # jours passés
        disponibles = self.collection.count()
        if not disponibles:
            return []
        r = self.collection.query(
            query_embeddings=[vecteur], n_results=min(k, disponibles), where=filtre
        )
        return [
            Passage(i, texte, int(meta["jour"]), "souvenir", float(dist))
            for i, texte, meta, dist in zip(
                r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0], strict=True
            )
        ]
