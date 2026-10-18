"""Adresses des services locaux, lues dans `.env` (jamais dans le code ni dans `configs/`).

Les paramètres d'un run sont dans `sim.config` ; ceux-ci décrivent la machine sur laquelle il
tourne. Sans fichier `.env`, les valeurs par défaut sont celles de `.env.example`.
"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    ollama_host: str = "http://localhost:11434"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Lit `.env` puis les variables d'environnement, qui gardent la priorité.

    Le fichier est lu sans être chargé dans l'environnement du processus : lire une
    configuration ne doit jamais modifier celle des autres composants.
    """
    fichier = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    sources = {**fichier, **os.environ}
    values = {
        name: sources[name.upper()] for name in Settings.model_fields if name.upper() in sources
    }
    return Settings(**values)
