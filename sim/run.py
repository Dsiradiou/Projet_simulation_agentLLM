"""Identité d'un run : run_id, graine, manifeste (E2, E3).

Le manifeste `runs/<run_id>/manifest.json` recopie tout ce qu'il faut pour savoir d'où vient
un run et le relancer : configuration complète, graine, versions, commit git.
"""

import json
import platform
import random
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from sim.config import Config

if TYPE_CHECKING:  # imports de typage seulement : sim.run est importé par les agents
    from collections.abc import Awaitable, Callable

    from agents.base import Agent, WorldActor
    from agents.roles import Palier
    from rag.recuperation import Recuperateur
    from sim.commerce import Commerce
    from sim.orchestrateur import ResumeJour
    from sim.settings import Settings

RUNS_DIR = Path("runs")
_PAQUETS = ("pydantic", "aiokafka", "redis", "ollama")


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    seed: int
    config_name: str
    started_at: datetime


def new_run(config: Config, config_path: str | Path, now: datetime | None = None) -> RunInfo:
    now = now or datetime.now(UTC)
    name = re.sub(r"[^a-z0-9]+", "-", Path(config_path).stem.lower()).strip("-") or "config"
    run_id = f"{now:%Y%m%d-%H%M%S}-{name[:24]}-s{config.run.seed}"
    return RunInfo(run_id=run_id, seed=config.run.seed, config_name=name, started_at=now)


def agent_rng(seed: int, agent_id: str) -> random.Random:
    """Générateur propre à un agent. Il ne dépend que de la graine du run et de l'identifiant :
    l'ordre dans lequel les agents sont appelés ne change pas leurs tirages."""
    return random.Random(f"{seed}:{agent_id}")


def _git_state() -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "modifie": None}
    return {"commit": commit, "modifie": dirty}


def write_manifest(run: RunInfo, config: Config, runs_dir: Path = RUNS_DIR) -> Path:
    run_dir = runs_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run.run_id,
        "seed": run.seed,
        "config_name": run.config_name,
        "started_at": run.started_at.isoformat(),
        "config": config.model_dump(mode="json"),
        "git": _git_state(),
        "python": platform.python_version(),
        "paquets": {name: _version(name) for name in _PAQUETS},
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


# --- Lancement en ligne de commande ---------------------------------------------------------


def main() -> None:
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(description="Lance un run de simulation.")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--jours", type=int, help="nombre de jours (défaut : durée configurée)")
    parser.add_argument("--graine", type=int, help="graine du run (défaut : celle de la config)")
    parser.add_argument(
        "--factice", action="store_true", help="agents factices sans LLM (tests du moteur)"
    )
    parser.add_argument(
        "--latence",
        type=float,
        default=0.0,
        help="facteur appliqué aux latences mesurées des modèles (agents factices)",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(_lancer(args.config, args.jours, args.factice, args.latence, args.graine))
    except OllamaIndisponible as exc:
        parser.exit(1, f"Erreur : {exc}\n")


class OllamaIndisponible(RuntimeError):
    """Ollama ne répond pas, ou un modèle de la configuration n'est pas téléchargé."""


async def _recuperateur(config: Config, host: str, run: RunInfo) -> "Recuperateur":
    """Base de connaissances du run : corpus indexé (réutilisé s'il n'a pas changé) et mémoires
    propres au run, gardées dans son dossier pour l'analyse (étape 12)."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    from rag.base import BaseCorpus, Memoires, client_persistant
    from rag.corpus import lire_corpus
    from rag.embeddings import EmbeddeurOllama
    from rag.recuperation import Recuperateur

    embeddeur = EmbeddeurOllama(host, config.rag.modele_embeddings)
    corpus = BaseCorpus(client_persistant(), embeddeur)
    if await corpus.indexer(lire_corpus(config.monde)):
        print(f"Corpus indexé avec {embeddeur.modele}")
    dossier = RUNS_DIR / run.run_id / "memoires"
    dossier.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(dossier), settings=ChromaSettings(anonymized_telemetry=False)
    )
    return Recuperateur(corpus, Memoires(client, embeddeur, run.run_id), config.rag, config.monde)


async def _verifier_ollama(host: str, modeles: set[str]) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            reponse = await client.get(f"{host}/api/tags")
        presents = {m["name"] for m in reponse.json()["models"]}
    except httpx.HTTPError as exc:
        raise OllamaIndisponible(f"Ollama injoignable sur {host} ({exc})") from exc
    # Un modèle sans étiquette (« bge-m3 ») est listé par Ollama avec « :latest ».
    manquants = {m for m in modeles if m not in presents and f"{m}:latest" not in presents}
    if manquants:
        raise OllamaIndisponible(
            f"modèles absents : {', '.join(sorted(manquants))} (ollama pull <modèle>)"
        )


async def _lancer(
    config_path: str,
    jours: int | None,
    factice: bool,
    facteur_latence: float,
    graine: int | None = None,
) -> None:
    from agents.factices import population_factice
    from agents.population import construire_population
    from agents.roles import palier_du_role
    from llm.client import ClientOllama
    from llm.routeur import Routeur, options_de
    from sim.config import MoteurAgents, load_config
    from sim.orchestrateur import ResumeJour
    from sim.settings import load_settings

    config = load_config(config_path)
    if graine is not None:  # runs répétés d'une même configuration (E2)
        config = config.model_copy(update={"run": config.run.model_copy(update={"seed": graine})})
    settings = load_settings()
    run = new_run(config, config_path)
    preparer = None
    if factice:
        agents, acteurs = population_factice(config, facteur_latence)
    elif config.agents.moteur is MoteurAgents.REGLES:  # baseline C0 : ni Ollama ni RAG
        agents, acteurs = construire_population(config, routeur=None)
    else:
        paliers = {palier_du_role(r) for r, n in config.population.items() if n} - {None}
        modeles = {config.llm.modeles[p] for p in paliers}
        if config.rag.actif:
            modeles.add(config.rag.modele_embeddings)
        await _verifier_ollama(settings.ollama_host, modeles)
        client = ClientOllama(settings.ollama_host, options_de(config.llm))
        routeur = Routeur(client, config.llm)
        recuperateur = (
            await _recuperateur(config, settings.ollama_host, run) if config.rag.actif else None
        )
        agents, acteurs = construire_population(config, routeur, recuperateur)
        preparer = routeur.preparer
    write_manifest(run, config)
    nb_llm = sum(a.palier is not None for a in agents)
    mode = "factices" if factice else f"dont {nb_llm} LLM" if nb_llm else "à règles, baseline C0"
    print(f"Run {run.run_id} : {len(agents)} agents ({mode}), graine {run.seed}")

    def afficher(r: ResumeJour) -> None:
        sens = ", ".join(f"{k} {v}" for k, v in sorted(r.sens.items()))
        print(
            f"Jour {r.jour:2d}  décisions {r.decisions:3d}  "
            f"conservées {r.conservees:3d}  porteurs {r.porteurs:3d}/{r.population}  [{sens}]",
            flush=True,
        )

    from agents.population import construire_commerce

    resume = await executer_run(
        config, run, agents, acteurs, settings, jours, afficher=afficher, preparer=preparer,
        commerce=construire_commerce(config, agents),
    )  # fmt: skip
    if resume["evenements_enregistres"] != resume["evenements_publies"]:
        print(
            f"ATTENTION : {resume['evenements_enregistres']} événements enregistrés "
            f"sur {resume['evenements_publies']} publiés"
        )
    print("Bilan :")
    for cle, valeur in resume.items():
        if isinstance(valeur, float):
            valeur = round(valeur, 2)
        print(f"  {cle} : {valeur}")


async def executer_run(
    config: Config,
    run: RunInfo,
    agents: list["Agent"],
    acteurs: list["WorldActor"],
    settings: "Settings",
    jours: int | None = None,
    *,
    afficher: "Callable[[ResumeJour], None] | None" = None,
    preparer: "Callable[[Palier], Awaitable[float]] | None" = None,
    runs_dir: Path = RUNS_DIR,
    commerce: "Commerce | None" = None,
) -> dict:
    """Exécute un run de bout en bout : Kafka, Redis, historique Parquet, `bilan.json`.
    Renvoie le bilan écrit. Utilisé par la commande et par les tests d'intégration."""
    import asyncio

    from redis.asyncio import Redis

    from bus.producer import EventProducer
    from bus.topics import ensure_topics
    from indicators.service import PRODUCTEUR, ServiceIndicateurs
    from replay.ecrivain import EcrivainHistorique
    from sim.controle import LecteurControle
    from sim.etat import EtatMonde
    from sim.orchestrateur import Orchestrateur
    from sim.routines import Routines

    await ensure_topics(settings.kafka_bootstrap_servers)
    # L'écrivain d'historique se place en fin de topic avant le premier événement du run (E3).
    ecrivain = EcrivainHistorique(settings.kafka_bootstrap_servers, run.run_id, runs_dir)
    await ecrivain.demarrer()
    ecriture = asyncio.create_task(ecrivain.boucle())
    # Les indicateurs (B2) sont calculés par un consommateur à part, lancé avec le run.
    indicateurs = ServiceIndicateurs(settings.kafka_bootstrap_servers, run.run_id, config.run.seed)
    await indicateurs.demarrer()
    calcul = asyncio.create_task(indicateurs.boucle())
    redis = Redis.from_url(settings.redis_url)
    etat = EtatMonde(redis, run.run_id)
    await etat.reinitialiser()
    try:
        async with EventProducer(settings.kafka_bootstrap_servers) as producer:
            # Commandes et injections du pont (D3), lues à partir de maintenant.
            controle = LecteurControle(settings.kafka_bootstrap_servers, run.run_id, producer)
            await controle.start()
            try:
                orchestrateur = Orchestrateur(
                    config, run, agents, acteurs, producer, etat, afficher, preparer, controle,
                    Routines(config.routines, config.run.seed), commerce,
                )  # fmt: skip
                bilan = await orchestrateur.executer(jours)
            finally:
                await controle.stop()
        publies_indicateurs = await indicateurs.terminer(bilan.jours)
        await calcul
        enregistres = await ecrivain.terminer(
            producer.published, autres={PRODUCTEUR: publies_indicateurs}
        )
        await ecriture
    finally:
        await redis.aclose()
        for tache in (ecriture, calcul):
            if not tache.done():
                tache.cancel()

    resume = {k: v for k, v in vars(bilan).items() if k != "sequence_paliers"}
    resume["evenements_publies"] = producer.published
    resume["evenements_enregistres"] = enregistres  # publiés par l'orchestrateur et relus
    resume["indicateurs_publies"] = publies_indicateurs
    resume["indicateurs_enregistres"] = ecrivain.recus_de[PRODUCTEUR]
    # Injections et commandes du pont.
    resume["evenements_externes"] = ecrivain.recus - enregistres - ecrivain.recus_de[PRODUCTEUR]
    # Prompts ramenés sous le budget du contexte (étape 12) : état des agents, pas des événements.
    resume["prompts_reduits"] = sum(getattr(a, "prompts_reduits", 0) for a in agents)
    recuperateurs = {
        id(a.recuperateur): a.recuperateur for a in agents if getattr(a, "recuperateur", None)
    }
    if recuperateurs:  # embeddings du RAG : appels et relances après un arrêt du serveur
        (recuperateur,) = recuperateurs.values()
        resume["embeddings_appels"] = recuperateur.corpus.embeddeur.appels
        resume["embeddings_relances"] = recuperateur.corpus.embeddeur.relances
    (runs_dir / run.run_id / "bilan.json").write_text(
        json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resume


if __name__ == "__main__":
    main()
