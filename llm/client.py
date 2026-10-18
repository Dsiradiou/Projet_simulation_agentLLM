"""Client Ollama : un appel = un prompt, une réponse JSON contrainte, et sa mesure de coût (E4).

La réflexion de Qwen3 est désactivée (`think=False`) : elle multiplierait les tokens de sortie
sans changer la forme de la réponse, déjà imposée par le schéma.
"""

import time
from dataclasses import dataclass
from typing import Protocol

from ollama import AsyncClient


@dataclass(frozen=True)
class Reponse:
    modele: str
    texte: str
    tokens_entree: int
    tokens_sortie: int
    duree_s: float  # temps réel de l'appel, vu du client


@dataclass(frozen=True)
class Options:
    tokens_sortie_max: int
    contexte_tokens: int
    temperature: float


class ClientLLM(Protocol):
    """Ce dont la couche LLM a besoin. Les tests le remplacent par un client scripté."""

    async def generer(
        self, modele: str, systeme: str, prompt: str, schema: dict, graine: int
    ) -> Reponse: ...

    async def charger(self, modele: str) -> None: ...


class ClientOllama:
    def __init__(self, host: str, options: Options, keep_alive: str = "30m") -> None:
        self._client = AsyncClient(host=host)
        self.options = options
        self.keep_alive = keep_alive

    async def generer(
        self, modele: str, systeme: str, prompt: str, schema: dict, graine: int
    ) -> Reponse:
        debut = time.perf_counter()
        reponse = await self._client.chat(
            model=modele,
            messages=[
                {"role": "system", "content": systeme},
                {"role": "user", "content": prompt},
            ],
            format=schema,
            think=False,
            keep_alive=self.keep_alive,
            options={
                "num_predict": self.options.tokens_sortie_max,
                "num_ctx": self.options.contexte_tokens,
                "temperature": self.options.temperature,
                "seed": graine,
            },
        )
        return Reponse(
            modele=modele,
            texte=reponse.message.content or "",
            tokens_entree=reponse.prompt_eval_count or 0,
            tokens_sortie=reponse.eval_count or 0,
            duree_s=time.perf_counter() - debut,
        )

    async def charger(self, modele: str) -> None:
        """Charge le modèle en mémoire sans rien générer (requête vide)."""
        await self._client.generate(
            model=modele,
            prompt="",
            keep_alive=self.keep_alive,
            options={"num_ctx": self.options.contexte_tokens},
        )
