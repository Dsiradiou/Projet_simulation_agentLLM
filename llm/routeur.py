"""Routeur par palier : quel modèle sert quel palier (A5), délai maximal, préchargement.

L'orchestrateur regroupe déjà les décisions par palier. Avant chaque groupe, il demande au
routeur de `preparer` le palier : le modèle est chargé une fois, et le temps de chargement est
mesuré à part au lieu d'alourdir la latence du premier agent du groupe.
"""

import asyncio
import time
import zlib

from agents.roles import Palier
from llm.client import ClientLLM, Options, Reponse
from sim.config import LlmConfig


class DelaiDepasse(TimeoutError):
    """Le modèle n'a pas répondu dans le délai configuré (`llm.delai_max_s`)."""


def options_de(config: LlmConfig) -> Options:
    return Options(
        tokens_sortie_max=config.tokens_sortie_max,
        contexte_tokens=config.contexte_tokens,
        temperature=config.temperature,
    )


def graine_appel(seed: int, agent_id: str, jour: int, tentative: int) -> int:
    """Graine d'échantillonnage d'un appel, stable d'un processus à l'autre (pas de `hash()`).

    Elle rend chaque appel reproductible pris isolément ; le traitement en lot d'Ollama peut
    encore faire varier une réponse à quelques tokens près. Le rejeu exact passe donc par le
    journal du run (étape 8), pas par un nouvel appel au modèle.
    """
    return zlib.crc32(f"{seed}:{agent_id}:{jour}:{tentative}".encode())


class Routeur:
    def __init__(self, client: ClientLLM, config: LlmConfig) -> None:
        self.client = client
        self.config = config
        self.chargements_s: list[tuple[str, float]] = []

    def modele(self, palier: Palier) -> str:
        return self.config.modeles[palier]

    async def generer(
        self, palier: Palier, systeme: str, prompt: str, schema: dict, graine: int
    ) -> Reponse:
        try:
            async with asyncio.timeout(self.config.delai_max_s):
                return await self.client.generer(
                    self.modele(palier), systeme, prompt, schema, graine
                )
        except TimeoutError as exc:
            raise DelaiDepasse(
                f"{self.modele(palier)} sans réponse après {self.config.delai_max_s:g} s"
            ) from exc

    async def preparer(self, palier: Palier) -> float:
        """Charge le modèle du palier et renvoie la durée (quasi nulle s'il est déjà chargé)."""
        debut = time.perf_counter()
        await self.client.charger(self.modele(palier))
        duree = time.perf_counter() - debut
        self.chargements_s.append((self.modele(palier), duree))
        return duree
