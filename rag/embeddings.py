"""Embeddings des textes par un modèle Ollama local (étape 12).

Certains modèles attendent un préfixe différent pour une question et pour un document : sans lui,
la recherche se dégrade nettement. Les préfixes sont ceux recommandés par chaque modèle.
"""

import asyncio
import time
from typing import Protocol

from ollama import AsyncClient, ResponseError

# Préfixes (question, document) selon la famille du modèle. Inconnu : pas de préfixe.
PREFIXES = {
    "qwen3-embedding": (
        "Instruct: Retrouver les passages qui répondent à la question\nQuery: ",
        "",
    ),
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "bge-m3": ("", ""),
}


class Embeddeur(Protocol):
    modele: str

    async def questions(self, textes: list[str]) -> list[list[float]]: ...
    async def documents(self, textes: list[str]) -> list[list[float]]: ...


class EmbeddeurOllama:
    def __init__(self, host: str, modele: str, keep_alive: str = "30m") -> None:
        self._client = AsyncClient(host=host)
        self.modele = modele
        self.keep_alive = keep_alive
        famille = modele.split(":")[0]
        self._prefixe_question, self._prefixe_document = PREFIXES.get(famille, ("", ""))
        self.appels = 0
        self.relances = 0
        self.duree_s = 0.0

    async def _embed(self, textes: list[str]) -> list[list[float]]:
        """Une relance après une seconde : sous charge, le serveur d'Ollama s'est arrêté une fois
        par run de 15 jours (« CUDA error », mesures du 18 oct.) puis a redémarré seul."""
        debut = time.perf_counter()
        try:
            reponse = await self._client.embed(
                model=self.modele, input=textes, keep_alive=self.keep_alive
            )
        except ResponseError:
            self.relances += 1
            await asyncio.sleep(1.0)
            reponse = await self._client.embed(
                model=self.modele, input=textes, keep_alive=self.keep_alive
            )
        self.appels += 1
        self.duree_s += time.perf_counter() - debut
        return [list(v) for v in reponse.embeddings]

    async def questions(self, textes: list[str]) -> list[list[float]]:
        return await self._embed([self._prefixe_question + t for t in textes])

    async def documents(self, textes: list[str]) -> list[list[float]]:
        return await self._embed([self._prefixe_document + t for t in textes])
