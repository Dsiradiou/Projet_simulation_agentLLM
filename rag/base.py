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
from rag.selection import Candidat, retention
from sim.config import MemoireConfig

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
    importance: int = 0  # souvenir : voir `recuperation.souvenir`
    score: float | None = None  # noté par `selection.choisir` : le plus utile a le plus haut score
    retention: float | None = None  # souvenir ou fait public : ce qu'il en reste (0 à 1)


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
        r = self.collection.query(
            query_embeddings=[vecteur],
            n_results=k,
            where=filtre,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        self._vecteurs = {
            f"corpus:{i}": tuple(float(x) for x in v)
            for i, v in zip(r["ids"][0], r["embeddings"][0], strict=True)
        }
        return [
            Passage(f"corpus:{i}", texte, int(meta["jour"]), str(meta["type"]), float(dist))
            for i, texte, meta, dist in zip(
                r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0], strict=True
            )
        ]

    def candidats(
        self, vecteur: list[float], jour: int, role: Role, reglage: MemoireConfig
    ) -> list[Candidat]:
        """Documents candidats (`selection.choisir`) : publiés avant le jour de l'agent et lisibles
        par son rôle. Un document ne s'efface pas (rétention 1, importance 0)."""
        passages = self.rechercher_vecteur(vecteur, jour, role, reglage.candidats)
        return [Candidat(p, self._vecteurs[p.source]) for p in passages]


class Memoires:
    """Épisodes vécus par les agents d'un run. Chaque agent ne retrouve que les siens (A1).

    Chaque souvenir garde son jour, son importance et le jour de son dernier rappel (`rappel`,
    mis à jour quand il est montré au modèle) : sa rétention se compte depuis ce rappel."""

    def __init__(self, client: chromadb.ClientAPI, embeddeur: Embeddeur, run_id: str) -> None:
        self.embeddeur = embeddeur
        self.collection = client.get_or_create_collection(
            _nom_collection(f"memoires-{run_id}", embeddeur.modele),
            metadata={"hnsw:space": "cosine"},
        )
        self._importance_max: dict[str, int] = {}  # par agent : l'importance se lit relative

    async def ajouter(
        self, agent_id: str, jour: int, texte: str, importance: int, prefixe: str = "memoire"
    ) -> str:
        """Enregistre un souvenir. `prefixe` : `memoire` (épisode) ou `bilan` (bilan d'une
        semaine) ; les deux se cherchent et s'oublient de la même façon."""
        source = f"{prefixe}:{agent_id}:j{jour}"
        (vecteur,) = await self.embeddeur.documents([texte])
        self.collection.upsert(
            ids=[source],
            embeddings=[vecteur],
            documents=[texte],
            metadatas=[
                {"agent_id": agent_id, "jour": jour, "importance": importance, "rappel": jour}
            ],
        )
        self._importance_max[agent_id] = max(importance, self._importance_max.get(agent_id, 0))
        return source

    def rappeler(self, sources: list[str], jour: int) -> None:
        """Souvenirs montrés au modèle ce jour-là : leur rétention repart à 1."""
        if not sources:
            return
        r = self.collection.get(ids=sorted(sources), include=["metadatas"])
        metas = [dict(meta) | {"rappel": jour} for meta in r["metadatas"]]
        self.collection.update(ids=r["ids"], metadatas=metas)

    def _requete(self, vecteur: list[float], agent_id: str, jour: int, n: int) -> dict | None:
        if not self.collection.count():
            return None
        return self.collection.query(
            query_embeddings=[vecteur],
            n_results=min(n, self.collection.count()),
            where={"$and": [{"agent_id": agent_id}, {"jour": {"$lt": jour}}]},  # jours passés (C6)
            include=["documents", "metadatas", "distances", "embeddings"],
        )

    def rechercher_vecteur(
        self, vecteur: list[float], agent_id: str, jour: int, k: int = 3
    ) -> list[Passage]:
        """Les `k` souvenirs de l'agent les plus proches de la question, des jours passés
        seulement (C6), sans score."""
        return [p for p, _, _ in self._lire(self._requete(vecteur, agent_id, jour, k))]

    def candidats(
        self, vecteur: list[float], agent_id: str, jour: int, reglage: MemoireConfig
    ) -> list[Candidat]:
        """Souvenirs candidats (`selection.choisir`) : les `candidats` plus proches, sans ceux
        dont la rétention est passée sous le seuil (ils restent en base pour la trace, F6).
        L'importance se lit par rapport à la plus forte de l'agent."""
        lus = self._lire(self._requete(vecteur, agent_id, jour, reglage.candidats))
        plus_forte = self._importance_max.get(agent_id) or max(
            (p.importance for p, _, _ in lus), default=0
        )
        resultat = []
        for passage, vecteur_souvenir, rappel in lus:
            relative = min(1.0, passage.importance / plus_forte) if plus_forte else 0.0
            reste = retention(jour - rappel, relative, reglage)
            if reste >= reglage.seuil_retention:
                resultat.append(Candidat(passage, vecteur_souvenir, reste, relative))
        return resultat

    @staticmethod
    def _lire(r: dict | None) -> list[tuple[Passage, tuple[float, ...], int]]:
        """Résultat brut de ChromaDB : chaque souvenir, son vecteur et son dernier rappel."""
        if r is None:
            return []
        return [
            (
                Passage(
                    i, texte, int(meta["jour"]), "souvenir", float(dist), int(meta["importance"])
                ),
                tuple(float(x) for x in vec),
                int(meta.get("rappel", meta["jour"])),
            )
            for i, texte, meta, dist, vec in zip(
                r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0],
                r["embeddings"][0], strict=True,
            )
        ]  # fmt: skip
