"""Benchmark des modèles Ollama sur la machine du binôme (étape 3, base de l'exigence E4).

Mesure, pour chaque modèle : temps de chargement, VRAM occupée, latence d'une décision d'agent en
séquentiel puis en parallèle, débit en tokens, validité du JSON imposé, puissance du GPU. Mesure
aussi le coût d'une bascule de palier (charger le 8b quand le 1.7b et le 4b sont en mémoire).

Le prompt imite une décision d'agent : environ 600 tokens en entrée, réponse JSON courte.

Usage : python -m llm.benchmark [--models qwen3:1.7b qwen3:4b qwen3:8b] [--calls 12]
Écrit les mesures brutes (JSON) et un résumé (Markdown) dans docs/mesures/.
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from ollama import AsyncClient

from sim.config import load_config
from sim.settings import load_settings

SENS = ["efficace", "tendance", "banal", "depasse"]

# Schéma simplifié, aligné sur la règle 5 de CLAUDE.md, gardé tel quel pour que les mesures restent
# comparables d'une machine à l'autre. Le schéma des agents est dans llm/sortie.py (étape 6).
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "sens": {"type": "string", "enum": SENS},
        "message": {"type": "string"},
        "justification": {"type": "string"},
    },
    "required": ["action", "sens", "justification"],
}

NUM_CTX = 2048
NUM_PREDICT = 160
KEEP_ALIVE = "30m"

ZONES = ["pharmacie", "plage", "piscine", "reseaux_sociaux", "centre_commercial", "terrain_sport"]
METEO = ["ensoleillé, indice UV 8", "nuageux, indice UV 4", "canicule, indice UV 10"]
PROFILS = [
    "pharmacienne expérimentée, prudente, attachée aux fiches produit",
    "créateur de contenu beauté, à l'affût de ce qui devient tendance",
    "baigneur occasionnel, achète ce qu'il voit autour de lui",
    "sceptique pragmatique, se méfie des effets de mode",
]
MESSAGES = [
    "Une amie dit que la texture ne colle pas et tient après la baignade.",
    "Un commentaire en ligne trouve le flacon trop cher pour ce qu'il apporte.",
    "Le vendeur annonce une promotion sur la marque concurrente ce week-end.",
    "Un voisin affirme que tout le monde en a déjà à la plage, que c'est banal.",
    "Une vidéo montre l'application avant un match, très partagée hier soir.",
    "Le pharmacien recommande de réappliquer toutes les deux heures, quelle que soit la marque.",
]
PASSAGES = [
    "Fiche produit : crème solaire {marque}, ingrédient {ingredient}, indice {indice}, "
    "texture fluide, flacon de 100 ml, vendue en pharmacie et en centre commercial.",
    "Communiqué de {marque} : lancement national au début de l'été, échantillons distribués "
    "dans les pharmacies partenaires pendant deux semaines.",
    "Article beauté : les crèmes solaires « tendance » de l'été misent sur la texture et le "
    "parfum plus que sur la nouveauté de la formule.",
    "Avis client : « agréable, mais je ne vois pas la différence avec mon ancienne crème ».",
    "Note interne vendeurs : réassort prévu le vendredi, rupture possible en cas de canicule.",
]

SYSTEM_PROMPT = (
    "Tu incarnes un habitant d'une ville balnéaire fictive pendant un été simulé. Tu n'es pas un "
    "assistant : tu réagis comme la personne décrite, avec ses habitudes et ses préjugés, sans "
    "chercher à être prudent ou consensuel. Le monde est entièrement fictif : n'utilise aucune "
    "connaissance sur des marques ou des produits réels, seulement les informations fournies. "
    "Le produit est un cosmétique ; ne fais aucune affirmation médicale.\n"
    "À chaque sollicitation, tu décides d'une action concrète pour la journée (acheter, appliquer, "
    "recommander, critiquer, ignorer, abandonner, en parler) et tu indiques le sens que le produit "
    "a pour toi, parmi exactement quatre valeurs : efficace (il remplit sa fonction), tendance (on "
    "le porte pour être dans le coup), banal (un produit comme un autre), depasse (sa mode est "
    "passée). Tu peux écrire un message court destiné à tes proches. Ta justification tient en "
    "une ou deux phrases et s'appuie sur ce que tu as vu, entendu ou lu.\n"
    "Réponds uniquement en JSON avec les champs action, sens, message, justification."
)


def build_prompt(index: int, marque: str, ingredient: str, indice: str) -> str:
    """Construit un contexte d'agent varié mais déterministe pour l'appel numéro `index`."""
    profil = PROFILS[index % len(PROFILS)]
    zone = ZONES[index % len(ZONES)]
    messages = [MESSAGES[(index + k) % len(MESSAGES)] for k in range(3)]
    passages = [
        PASSAGES[(index + k) % len(PASSAGES)].format(
            marque=marque, ingredient=ingredient, indice=indice
        )
        for k in range(4)
    ]
    lignes = [
        f"Profil : {profil}. Agent numéro {index:03d}.",
        f"Jour simulé : {10 + index % 60}. Zone actuelle : {zone}. Météo : {METEO[index % 3]}.",
        f"Situation : environ {20 + (index * 7) % 50} % des personnes autour de toi utilisent "
        f"{marque} aujourd'hui. Ta décision d'hier : sens « {SENS[index % 4]} ».",
        "Ce que tu te rappelles des jours précédents : tu as essayé un échantillon il y a "
        f"{2 + index % 5} jours et tu en as parlé avec deux proches.",
        "Messages reçus depuis hier :",
        *[f"- {m}" for m in messages],
        "Informations retrouvées dans tes sources :",
        *[f"- {p}" for p in passages],
        "Que fais-tu aujourd'hui, et quel sens le produit a-t-il pour toi ?",
    ]
    return "\n".join(lignes)


@dataclass
class CallResult:
    model: str
    scenario: str
    concurrency: int
    wall_s: float
    load_s: float
    prompt_tokens: int
    prompt_s: float
    output_tokens: int
    output_s: float
    valid: bool
    sens: str | None
    error: str | None = None


@dataclass
class GpuStats:
    mean_power_w: float
    max_power_w: float
    max_memory_mb: float
    max_temperature_c: float
    duration_s: float

    @property
    def energy_j(self) -> float:
        return self.mean_power_w * self.duration_s


class GpuSampler:
    """Échantillonne puissance, mémoire et température du GPU via nvidia-smi pendant un bloc."""

    def __init__(self, interval_ms: int = 250) -> None:
        self.interval_ms = interval_ms
        self.samples: list[tuple[float, float, float]] = []
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._start = 0.0
        self.stats: GpuStats | None = None

    def __enter__(self) -> "GpuSampler":
        self._start = time.perf_counter()
        try:
            self._process = subprocess.Popen(
                [
                    "nvidia-smi",
                    "--query-gpu=power.draw,memory.used,temperature.gpu",
                    "--format=csv,noheader,nounits",
                    f"-lms={self.interval_ms}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            return self
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def _read(self) -> None:
        assert self._process and self._process.stdout
        for line in self._process.stdout:
            try:
                power, memory, temperature = (float(v) for v in line.split(","))
            except ValueError:
                continue
            self.samples.append((power, memory, temperature))

    def __exit__(self, *exc: object) -> None:
        duration = time.perf_counter() - self._start
        if self._process:
            self._process.terminate()
            self._process.wait()
        if self._thread:
            self._thread.join(timeout=2)
        if self.samples:
            powers = [s[0] for s in self.samples]
            self.stats = GpuStats(
                mean_power_w=statistics.mean(powers),
                max_power_w=max(powers),
                max_memory_mb=max(s[1] for s in self.samples),
                max_temperature_c=max(s[2] for s in self.samples),
                duration_s=duration,
            )


class Benchmark:
    def __init__(self, host: str, marque: str, ingredient: str, indice: str) -> None:
        self.client = AsyncClient(host=host)
        self.host = host
        self.monde = (marque, ingredient, indice)
        self.results: list[CallResult] = []
        self.scenarios: list[dict] = []
        self.vram: dict[str, dict] = {}
        self._index = 0

    def _next_index(self) -> int:
        self._index += 1
        return self._index

    async def call(self, model: str, scenario: str, concurrency: int) -> CallResult:
        index = self._next_index()
        start = time.perf_counter()
        try:
            response = await self.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(index, *self.monde)},
                ],
                format=DECISION_SCHEMA,
                think=False,
                keep_alive=KEEP_ALIVE,
                options={
                    "num_ctx": NUM_CTX,
                    "num_predict": NUM_PREDICT,
                    "temperature": 0.7,
                    "seed": index,
                },
            )
        except Exception as exc:  # une erreur est une mesure, pas un arrêt du benchmark
            result = CallResult(
                model=model,
                scenario=scenario,
                concurrency=concurrency,
                wall_s=time.perf_counter() - start,
                load_s=0,
                prompt_tokens=0,
                prompt_s=0,
                output_tokens=0,
                output_s=0,
                valid=False,
                sens=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.results.append(result)
            return result

        valid, sens = False, None
        try:
            payload = json.loads(response.message.content)
            sens = payload.get("sens")
            valid = sens in SENS and bool(payload.get("action"))
        except (json.JSONDecodeError, AttributeError):
            pass
        ns = 1e9
        result = CallResult(
            model=model,
            scenario=scenario,
            concurrency=concurrency,
            wall_s=time.perf_counter() - start,
            load_s=(response.load_duration or 0) / ns,
            prompt_tokens=response.prompt_eval_count or 0,
            prompt_s=(response.prompt_eval_duration or 0) / ns,
            output_tokens=response.eval_count or 0,
            output_s=(response.eval_duration or 0) / ns,
            valid=valid,
            sens=sens,
        )
        self.results.append(result)
        return result

    async def unload_all(self) -> None:
        for loaded in (await self.client.ps()).models:
            await self.client.generate(model=loaded.model, keep_alive=0)
        await asyncio.sleep(1)

    async def loaded_models(self) -> list[dict]:
        return [
            {
                "model": m.model,
                "size_mb": round(m.size / 2**20),
                "size_vram_mb": round(m.size_vram / 2**20),
                "gpu_share": round(m.size_vram / m.size, 3) if m.size else None,
                "context_length": getattr(m, "context_length", None),
            }
            for m in (await self.client.ps()).models
        ]

    async def run_block(self, model: str, scenario: str, concurrency: int, calls: int) -> None:
        semaphore = asyncio.Semaphore(concurrency)

        async def limited() -> CallResult:
            async with semaphore:
                return await self.call(model, scenario, concurrency)

        with GpuSampler() as gpu:
            start = time.perf_counter()
            await asyncio.gather(*(limited() for _ in range(calls)))
            duration = time.perf_counter() - start
        self.scenarios.append(
            {
                "model": model,
                "scenario": scenario,
                "concurrency": concurrency,
                "calls": calls,
                "duration_s": duration,
                "gpu": asdict(gpu.stats) if gpu.stats else None,
            }
        )

    async def model_suite(self, model: str, calls: int, concurrencies: list[int]) -> None:
        print(f"[{model}] chargement à froid")
        await self.unload_all()
        await self.call(model, "chargement", 1)
        self.vram[model] = next((m for m in await self.loaded_models() if m["model"] == model), {})
        print(f"[{model}] {calls} appels en séquentiel")
        await self.run_block(model, "sequentiel", 1, calls)
        for repeat, concurrency in enumerate(concurrencies):
            # Un nom unique par bloc, même si un niveau de parallélisme est mesuré deux fois.
            name = f"parallele_{concurrency}"
            if concurrencies[:repeat].count(concurrency):
                name += f"_{concurrencies[:repeat].count(concurrency) + 1}"
            print(f"[{model}] {calls} appels, {concurrency} en parallèle")
            await self.run_block(model, name, concurrency, calls)

    async def swap_suite(self, small: str, medium: str, large: str) -> dict:
        """Phase A (petit + moyen) puis bascule vers la phase B (grand)."""
        print("[bascule] phase A puis chargement du grand modèle")
        await self.unload_all()
        await self.call(small, "phase_a", 1)
        await self.call(medium, "phase_a", 1)
        phase_a = await self.loaded_models()
        swap = await self.call(large, "bascule", 1)
        phase_b = await self.loaded_models()
        back = await self.call(small, "retour", 1)
        retour = await self.loaded_models()
        return {
            "phase_a": phase_a,
            "phase_b": phase_b,
            "charge_grand_s": swap.load_s,
            "retour_petit_load_s": back.load_s,
            "apres_retour": retour,
        }


def ollama_env() -> dict[str, str]:
    """Réglages OLLAMA_* : environnement courant, complété sous Windows par les variables
    utilisateur enregistrées (celles que lit Ollama au démarrage)."""
    env = {k: v for k, v in os.environ.items() if k.startswith("OLLAMA_")}
    if platform.system() == "Windows":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                if name.startswith("OLLAMA_"):
                    env.setdefault(name, str(value))
                index += 1
    return dict(sorted(env.items()))


def machine_info(host: str) -> dict:
    info: dict = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "python": platform.python_version(),
        "ollama_env": ollama_env(),
    }
    try:
        info["ollama_version"] = httpx.get(f"{host}/api/version", timeout=5).json()["version"]
    except httpx.HTTPError:
        info["ollama_version"] = None
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        info["gpu"] = gpu
    except (FileNotFoundError, subprocess.CalledProcessError):
        info["gpu"] = None
    return info


def _q(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(quantile * (len(ordered) - 1)))]


@dataclass
class Row:
    model: str
    scenario: str
    concurrency: int
    calls: int
    errors: int
    valid_rate: float
    latency_median_s: float
    latency_p90_s: float
    prompt_tokens: float
    output_tokens: float
    output_tok_s: float
    calls_per_min: float
    mean_power_w: float | None
    energy_per_call_j: float | None
    extra: dict = field(default_factory=dict)


def summarize(bench: Benchmark) -> list[Row]:
    rows = []
    for block in bench.scenarios:
        calls = [
            r
            for r in bench.results
            if r.model == block["model"] and r.scenario == block["scenario"]
        ]
        ok = [r for r in calls if r.error is None]
        gpu = block["gpu"]
        rows.append(
            Row(
                model=block["model"],
                scenario=block["scenario"],
                concurrency=block["concurrency"],
                calls=len(calls),
                errors=len(calls) - len(ok),
                valid_rate=sum(r.valid for r in ok) / len(calls) if calls else 0,
                latency_median_s=statistics.median(r.wall_s for r in ok) if ok else 0,
                latency_p90_s=_q([r.wall_s for r in ok], 0.9) if ok else 0,
                prompt_tokens=statistics.mean(r.prompt_tokens for r in ok) if ok else 0,
                output_tokens=statistics.mean(r.output_tokens for r in ok) if ok else 0,
                output_tok_s=(
                    statistics.mean(r.output_tokens / r.output_s for r in ok if r.output_s)
                    if ok
                    else 0
                ),
                calls_per_min=60 * len(ok) / block["duration_s"],
                mean_power_w=gpu["mean_power_w"] if gpu else None,
                energy_per_call_j=(
                    gpu["mean_power_w"] * gpu["duration_s"] / len(ok) if gpu and ok else None
                ),
            )
        )
    return rows


# Appels par run estimés dans le document d'architecture (section 7), à 90 jours et 120 agents.
APPELS_PAR_RUN = {"qwen3:8b": 270, "qwen3:4b": 715, "qwen3:1.7b": 1170}


def write_markdown(path: Path, info: dict, bench: Benchmark, rows: list[Row], swap: dict) -> None:
    lines = [
        f"# Benchmark Ollama — {info['date'][:10]}",
        "",
        f"- GPU : {info['gpu']}",
        f"- Ollama : {info['ollama_version']} · Python {info['python']} · {info['os']}",
        f"- Réglages : {', '.join(f'{k}={v}' for k, v in info['ollama_env'].items())}",
        f"- Requête : contexte {NUM_CTX} tokens, sortie plafonnée à {NUM_PREDICT} tokens, "
        "JSON imposé, réflexion désactivée",
        "",
        "## Modèles chargés",
        "",
        "| Modèle | Chargement à froid (s) | Taille (Mo) | En VRAM (Mo) | Part sur GPU |",
        "| --- | --- | --- | --- | --- |",
    ]
    for model, vram in bench.vram.items():
        load = next(
            (r.load_s for r in bench.results if r.model == model and r.scenario == "chargement"),
            0,
        )
        lines.append(
            f"| {model} | {load:.1f} | {vram.get('size_mb', '?')} | "
            f"{vram.get('size_vram_mb', '?')} | {vram.get('gpu_share', '?')} |"
        )
    lines += [
        "",
        "## Latence et débit",
        "",
        "| Modèle | Mode | Appels | JSON valide | Latence médiane (s) | p90 (s) | Tokens entrée "
        "| Tokens sortie | Tokens/s sortie | Appels/min | Puissance (W) | Énergie/appel (J) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        power = f"{r.mean_power_w:.0f}" if r.mean_power_w is not None else "?"
        energy = f"{r.energy_per_call_j:.0f}" if r.energy_per_call_j is not None else "?"
        lines.append(
            f"| {r.model} | {r.scenario} | {r.calls} | {r.valid_rate:.0%} | "
            f"{r.latency_median_s:.2f} | {r.latency_p90_s:.2f} | {r.prompt_tokens:.0f} | "
            f"{r.output_tokens:.0f} | {r.output_tok_s:.0f} | {r.calls_per_min:.1f} | "
            f"{power} | {energy} |"
        )
    if swap:
        lines += [
            "",
            "## Bascule de palier",
            "",
            f"- Phase A (petit + moyen) : {swap['phase_a']}",
            f"- Phase B après appel du grand modèle : {swap['phase_b']}",
            f"- Chargement du grand modèle pendant la bascule : {swap['charge_grand_s']:.1f} s",
            f"- Rechargement du petit modèle au retour : {swap['retour_petit_load_s']:.1f} s",
            f"- Modèles chargés après le retour : {swap['apres_retour']}",
        ]
    lines += [
        "",
        "## Durée estimée d'un run (90 jours, 120 agents, appels du document d'architecture)",
        "",
        "| Modèle | Appels/run | Séquentiel (min) | Meilleur parallèle (min) |",
        "| --- | --- | --- | --- |",
    ]
    for model, n in APPELS_PAR_RUN.items():
        model_rows = [r for r in rows if r.model == model and r.calls_per_min > 0]
        if not model_rows:
            continue
        seq = next((r for r in model_rows if r.concurrency == 1), model_rows[0])
        best = max(model_rows, key=lambda r: r.calls_per_min)
        lines.append(
            f"| {model} | {n} | {n / seq.calls_per_min:.1f} | "
            f"{n / best.calls_per_min:.1f} ({best.scenario}) |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(models: list[str], calls: int, concurrencies: list[int], out_dir: Path) -> Path:
    settings = load_settings()
    monde = load_config("configs/default.yaml").monde
    bench = Benchmark(
        settings.ollama_host, monde.brand_name, monde.ingredient, monde.indice_protection
    )
    info = machine_info(settings.ollama_host)
    for model in models:
        await bench.model_suite(model, calls, concurrencies)
    swap = await bench.swap_suite(*models[:3]) if len(models) >= 3 else {}
    rows = summarize(bench)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    raw = out_dir / f"benchmark_{stamp}.json"
    raw.write_text(
        json.dumps(
            {
                "machine": info,
                "vram": bench.vram,
                "swap": swap,
                "scenarios": bench.scenarios,
                "calls": [asdict(r) for r in bench.results],
                "summary": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = out_dir / f"benchmark_{stamp}.md"
    write_markdown(summary, info, bench, rows, swap)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=["qwen3:1.7b", "qwen3:4b", "qwen3:8b"])
    parser.add_argument("--calls", type=int, default=12, help="appels par scénario")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[2, 4])
    parser.add_argument("--out", type=Path, default=Path("docs/mesures"))
    args = parser.parse_args()
    summary = asyncio.run(run(args.models, args.calls, args.concurrency, args.out))
    print(f"Résumé : {summary}")


if __name__ == "__main__":
    main()
