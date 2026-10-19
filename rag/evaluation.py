"""Évaluation de la récupération (F2) : jeu de référence, comparatif des modèles d'embeddings,
export de décisions réelles à annoter.

- Jeu de référence (`corpus/evaluation/reference.yaml`) : pour chaque question, les documents qui
  devraient figurer dans les 4 premiers passages, et ceux qui ne doivent jamais y figurer
  (document pas encore publié, note réservée à un autre rôle). Mesure automatique, reproductible.
- Annotation : pour un échantillon de décisions d'un run, ce que l'agent avait sous les yeux, dans
  un fichier que le binôme annote (pertinent ou non).

Usage :
    python -m rag.evaluation --modeles qwen3-embedding:0.6b bge-m3 nomic-embed-text
    python -m rag.evaluation --annoter <run_id> [--nombre 30]   # export CSV + contrôle C6
    python -m rag.evaluation --noter docs/mesures/annotation_<run_id>.csv
"""

import argparse
import asyncio
import csv
import json
import random
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from agents.roles import Role
from rag.base import BaseCorpus, client_memoire_vive
from rag.corpus import lire_corpus, remplacer
from rag.embeddings import EmbeddeurOllama
from sim.config import MondeConfig, load_config
from sim.settings import load_settings

REFERENCE = Path("corpus/evaluation/reference.yaml")
OUT_DIR = Path("docs/mesures")
K = 4


@dataclass(frozen=True)
class QuestionReference:
    question: str
    jour: int
    role: Role
    attendus: tuple[str, ...]
    interdits: tuple[str, ...] = ()


@dataclass
class Resultat:
    modele: str
    reussite: float = 0.0  # part des questions dont un document attendu est dans le top 4
    mrr: float = 0.0  # rang réciproque moyen du premier document attendu
    violations: int = 0  # documents interdits retrouvés (futur ou autre rôle) : doit valoir 0
    duree_index_s: float = 0.0
    duree_question_ms: float = 0.0
    vram_mo: float | None = None
    details: list[dict] = field(default_factory=list)


def charger_reference(monde: MondeConfig, chemin: Path = REFERENCE) -> list[QuestionReference]:
    brut = yaml.safe_load(chemin.read_text(encoding="utf-8"))["questions"]
    return [
        QuestionReference(
            question=remplacer(q["question"], monde),
            jour=int(q["jour"]),
            role=Role(q["role"]),
            attendus=tuple(q.get("attendus") or ()),
            interdits=tuple(q.get("interdits") or ()),
        )
        for q in brut
    ]


async def evaluer(base: BaseCorpus, reference: list[QuestionReference]) -> Resultat:
    resultat = Resultat(base.embeddeur.modele)
    rangs: list[float] = []
    durees = []
    for q in reference:
        debut = time.perf_counter()
        passages = await base.rechercher(q.question, q.jour, q.role, K)
        durees.append(time.perf_counter() - debut)
        trouves = [p.source.removeprefix("corpus:") for p in passages]
        violations = [t for t in trouves if t in q.interdits]
        resultat.violations += len(violations)
        rang = next((i + 1 for i, t in enumerate(trouves) if t in q.attendus), None)
        if q.attendus:
            rangs.append(1 / rang if rang else 0.0)
        resultat.details.append(
            {
                "question": q.question,
                "jour": q.jour,
                "role": q.role.value,
                "trouves": trouves,
                "attendus": list(q.attendus),
                "rang": rang,
                "violations": violations,
            }
        )
    avec_attendus = [d for d in resultat.details if d["attendus"]]
    resultat.reussite = round(
        sum(d["rang"] is not None for d in avec_attendus) / len(avec_attendus), 3
    )
    resultat.mrr = round(statistics.mean(rangs), 3)
    resultat.duree_question_ms = round(1000 * statistics.median(durees), 1)
    return resultat


async def _vram(host: str, modele: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            modeles = (await client.get(f"{host}/api/ps")).json().get("models", [])
    except httpx.HTTPError:
        return None
    nom = modele if ":" in modele else f"{modele}:latest"
    for m in modeles:
        if m.get("name") == nom:
            return round(m.get("size_vram", 0) / 2**20)
    return None


async def comparer(modeles: list[str], config_path: str = "configs/default.yaml") -> list[Resultat]:
    config = load_config(config_path)
    host = load_settings().ollama_host
    documents = lire_corpus(config.monde)
    reference = charger_reference(config.monde)
    resultats = []
    for modele in modeles:
        base = BaseCorpus(client_memoire_vive(), EmbeddeurOllama(host, modele))
        debut = time.perf_counter()
        await base.indexer(documents)
        duree_index = time.perf_counter() - debut
        resultat = await evaluer(base, reference)
        resultat.duree_index_s = round(duree_index, 2)
        resultat.vram_mo = await _vram(host, modele)
        resultats.append(resultat)
    return resultats


def ecrire_comparatif(resultats: list[Resultat]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"embeddings_{datetime.now():%Y-%m-%d_%H%M}"
    base.with_suffix(".json").write_text(
        json.dumps([r.__dict__ for r in resultats], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lignes = [
        "# Comparatif des modèles d'embeddings (étape 12)",
        "",
        f"Jeu de référence : {len(resultats[0].details)} questions en français "
        "(`corpus/evaluation/reference.yaml`), 4 passages retrouvés par question, filtres de date "
        "et de rôle appliqués. Réussite : un document attendu figure dans les 4 passages. MRR : "
        "rang réciproque moyen du premier document attendu. Violations : documents interdits "
        "retrouvés (doit valoir 0).",
        "",
        "| Modèle | Réussite@4 | MRR | Violations | Indexation (s) | Question (ms) | VRAM (Mo) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in resultats:
        lignes.append(
            f"| {r.modele} | {r.reussite:.0%} | {r.mrr:.2f} | {r.violations} | "
            f"{r.duree_index_s} | {r.duree_question_ms} | {r.vram_mo or '—'} |"
        )
    lignes += ["", "## Questions manquées", ""]
    for r in resultats:
        manquees = [d for d in r.details if d["attendus"] and d["rang"] is None]
        textes = [
            f"« {d['question']} » (J{d['jour']}) → {', '.join(d['trouves'])}" for d in manquees
        ]
        lignes.append(f"- **{r.modele}** : " + ("; ".join(textes) or "aucune"))
    base.with_suffix(".md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return base.with_suffix(".md")


# --- décisions réelles : contrôle et annotation ------------------------------------------------


@dataclass(frozen=True)
class DecisionLue:
    event_id: str
    agent_id: str
    jour: int
    motif: str | None
    justification: str
    sources: tuple[str, ...]


def decisions_avec_sources(run_id: str, runs_dir: Path | None = None) -> list[DecisionLue]:
    from bus import events as ev
    from replay.rejeu import charger

    events = charger(run_id, runs_dir) if runs_dir else charger(run_id)
    return [
        DecisionLue(
            str(e.event_id),
            e.source_agent_id,
            e.sim_time_occurred // 1440 + 1,
            e.payload.motif.value if e.payload.motif else None,
            e.payload.justification,
            e.payload.sources,
        )  # fmt: skip
        for e in events
        if isinstance(e, ev.AgentDecision) and e.payload.sources
    ]


def controler(decisions: list[DecisionLue], monde: MondeConfig) -> dict[str, int]:
    """Contrôle C6 sur tout un run : aucune source lue avant sa date, ni hors de son rôle, aucun
    souvenir du jour même ou d'un autre agent."""
    documents = {d.id: d for d in lire_corpus(monde)}
    ecarts = {"futur": 0, "hors_role": 0, "souvenir_invalide": 0, "sources": 0}
    for d in decisions:
        role = Role(d.agent_id.rsplit("_", 1)[0])
        for source in d.sources:
            ecarts["sources"] += 1
            if source.startswith("corpus:"):
                doc = documents[source.removeprefix("corpus:")]
                ecarts["futur"] += doc.jour > d.jour
                ecarts["hors_role"] += not doc.lisible_par(role)
            else:
                _, agent, jour = source.split(":")
                ecarts["souvenir_invalide"] += agent != d.agent_id or int(jour[1:]) >= d.jour
    return ecarts


def textes_des_sources(run_id: str, monde: MondeConfig, runs_dir: Path) -> dict[str, str]:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    textes = {f"corpus:{d.id}": d.passage() for d in lire_corpus(monde)}
    dossier = runs_dir / run_id / "memoires"
    if dossier.exists():
        client = chromadb.PersistentClient(
            path=str(dossier), settings=ChromaSettings(anonymized_telemetry=False)
        )
        for collection in client.list_collections():
            c = client.get_collection(getattr(collection, "name", collection))
            contenu = c.get()
            textes |= dict(zip(contenu["ids"], contenu["documents"], strict=True))
    return textes


def exporter_annotation(
    run_id: str, nombre: int, config_path: str = "configs/default.yaml"
) -> Path:
    from sim.run import RUNS_DIR

    monde = load_config(config_path).monde
    decisions = decisions_avec_sources(run_id)
    echantillon = sorted(
        random.Random(f"annotation:{run_id}").sample(decisions, min(nombre, len(decisions))),
        key=lambda d: (d.jour, d.agent_id),
    )
    textes = textes_des_sources(run_id, monde, RUNS_DIR)
    ecarts = controler(decisions, monde)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"annotation_{run_id}.csv"
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f, delimiter=";")
        ecrivain.writerow(
            ["decision", "agent", "jour", "motif", "justification", "rang", "source", "texte",
             "pertinent (0/1)"]
        )  # fmt: skip
        for d in echantillon:
            for rang, source in enumerate(d.sources, 1):
                ecrivain.writerow(
                    [d.event_id[:8], d.agent_id, d.jour, d.motif, d.justification, rang, source,
                     textes.get(source, "(texte introuvable)"), ""]
                )  # fmt: skip
    lignes = [
        f"# Récupération du run {run_id}",
        "",
        f"- Décisions LLM avec sources : {len(decisions)} ; échantillon à annoter : "
        f"{len(echantillon)} (`{chemin.name}`, colonne « pertinent » à remplir).",
        f"- Contrôle sur tout le run ({ecarts['sources']} sources) : documents lus avant "
        f"leur date : {ecarts['futur']} ; hors du rôle : {ecarts['hors_role']} ; souvenirs "
        f"invalides : {ecarts['souvenir_invalide']}.",
    ]
    chemin.with_suffix(".md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def noter(chemin: Path) -> dict[str, float]:
    """Précision des sources annotées (part jugée pertinente), au total et par type."""
    with chemin.open(encoding="utf-8") as f:
        lignes = [r for r in csv.DictReader(f, delimiter=";") if r["pertinent (0/1)"].strip()]
    if not lignes:
        raise ValueError("aucune ligne annotée")
    resultat = {"annotees": len(lignes)}
    for nom, filtre in (("toutes", ""), ("corpus", "corpus:"), ("souvenirs", "memoire:")):
        choix = [int(r["pertinent (0/1)"]) for r in lignes if r["source"].startswith(filtre)]
        if choix:
            resultat[f"precision_{nom}"] = round(sum(choix) / len(choix), 3)
    return resultat


def main() -> None:
    parser = argparse.ArgumentParser(description="Évaluation de la récupération (F2).")
    parser.add_argument("--modeles", nargs="+", help="comparer ces modèles d'embeddings")
    parser.add_argument("--annoter", help="run_id : exporter un échantillon à annoter")
    parser.add_argument("--nombre", type=int, default=30, help="décisions à annoter")
    parser.add_argument("--noter", type=Path, help="fichier d'annotation rempli")
    args = parser.parse_args()
    if args.modeles:
        chemin = ecrire_comparatif(asyncio.run(comparer(args.modeles)))
        print(chemin.read_text(encoding="utf-8"))
    if args.annoter:
        chemin = exporter_annotation(args.annoter, args.nombre)
        print(chemin.with_suffix(".md").read_text(encoding="utf-8"))
    if args.noter:
        print(json.dumps(noter(args.noter), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
