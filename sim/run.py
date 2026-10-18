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

from sim.config import Config

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

    parser = argparse.ArgumentParser(description="Lance un run de simulation.")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--jours", type=int, help="nombre de jours (défaut : durée configurée)")
    parser.add_argument(
        "--factice",
        action="store_true",
        help="agents factices sans LLM (seul mode disponible avant l'étape 6)",
    )
    parser.add_argument(
        "--latence",
        type=float,
        default=0.0,
        help="facteur appliqué aux latences mesurées des modèles (agents factices)",
    )
    args = parser.parse_args()
    if not args.factice:
        parser.error("les agents LLM arrivent à l'étape 6 : utiliser --factice")
    asyncio.run(_lancer(args.config, args.jours, args.latence))


async def _lancer(config_path: str, jours: int | None, facteur_latence: float) -> None:
    from redis.asyncio import Redis

    from agents.factices import population_factice
    from bus.producer import EventProducer
    from bus.topics import ensure_topics
    from sim.config import load_config
    from sim.etat import EtatMonde
    from sim.orchestrateur import Orchestrateur, ResumeJour
    from sim.settings import load_settings

    config = load_config(config_path)
    settings = load_settings()
    run = new_run(config, config_path)
    write_manifest(run, config)
    agents, acteurs = population_factice(config, facteur_latence)
    print(f"Run {run.run_id} : {len(agents)} agents, graine {run.seed}")

    def afficher(r: ResumeJour) -> None:
        sens = ", ".join(f"{k} {v}" for k, v in sorted(r.sens.items()))
        print(
            f"Jour {r.jour:2d}  décisions {r.decisions:3d}  "
            f"conservées {r.conservees:3d}  porteurs {r.porteurs:3d}/{r.population}  [{sens}]"
        )

    await ensure_topics(settings.kafka_bootstrap_servers)
    redis = Redis.from_url(settings.redis_url)
    etat = EtatMonde(redis, run.run_id)
    await etat.reinitialiser()
    try:
        async with EventProducer(settings.kafka_bootstrap_servers) as producer:
            orchestrateur = Orchestrateur(config, run, agents, acteurs, producer, etat, afficher)
            bilan = await orchestrateur.executer(jours)
    finally:
        await redis.aclose()

    resume = {k: v for k, v in vars(bilan).items() if k != "sequence_paliers"}
    (RUNS_DIR / run.run_id / "bilan.json").write_text(
        json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Bilan :", ", ".join(f"{k}={v}" for k, v in resume.items()))


if __name__ == "__main__":
    main()
