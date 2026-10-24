"""Rejeu d'un run passé à partir de son seul historique Parquet, sans aucun appel LLM (E3).

Les événements sont relus tels qu'ils ont circulé (colonne `brut`), revalidés par le contrat,
et rendus dans l'ordre exact de publication, à la vitesse choisie. Le rejeu ne repasse pas par
Kafka : il alimente directement un afficheur (la console ici, le pont vers Godot à l'étape 9),
et ne mélange jamais un run rejoué avec un run en direct.

Usage :
    python -m replay.rejeu <run_id>                # tout, sans attente
    python -m replay.rejeu <run_id> --vitesse 2    # 2 jours simulés par seconde
    python -m replay.rejeu <run_id> --verifier     # compare le bilan recalculé à bilan.json
"""

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pyarrow.parquet as pq

from bus import events as ev
from bus.producer import ordre_publication
from replay.bilan import COMPARABLES, bilan_depuis, population_agents
from replay.ecrivain import dossier_evenements
from sim.orchestrateur import Bilan
from sim.run import RUNS_DIR


class HistoriqueAbsent(FileNotFoundError):
    """Aucun historique Parquet pour ce run."""


def charger(run_id: str, runs_dir: Path = RUNS_DIR) -> list[ev.Envelope]:
    """Événements du run, dans l'ordre de publication (`bus.producer.ordre_publication`)."""
    dossier = dossier_evenements(run_id, runs_dir)
    fichiers = sorted(dossier.glob("part-*.parquet"))
    if not fichiers:
        raise HistoriqueAbsent(f"aucun historique pour {run_id} dans {dossier}")
    lignes = [
        ligne
        for f in fichiers
        for ligne in pq.read_table(f, columns=["kafka_ts", "producteur", "seq", "brut"]).to_pylist()
    ]
    lignes.sort(key=lambda r: ordre_publication(r["kafka_ts"], r["producteur"], r["seq"]))
    return [ev.decode(r["brut"].encode("utf-8")) for r in lignes]


async def rejouer(
    events: list[ev.Envelope], vitesse: float | None = None
) -> AsyncIterator[ev.Envelope]:
    """Rend les événements un à un. Avec `vitesse` (jours simulés par seconde), attend au début
    de chaque jour simulé ; sans, rend tout immédiatement."""
    for event in events:
        if vitesse and isinstance(event, ev.ClockStep) and event.payload.jour > 1:
            await asyncio.sleep(1 / vitesse)
        yield event


def verifier(run_id: str, events: list[ev.Envelope], runs_dir: Path = RUNS_DIR) -> dict:
    """Écarts entre le bilan enregistré à la fin du run et celui recalculé depuis l'historique.
    Un dictionnaire vide signifie un rejeu fidèle."""
    manifeste = json.loads((runs_dir / run_id / "manifest.json").read_text(encoding="utf-8"))
    enregistre = json.loads((runs_dir / run_id / "bilan.json").read_text(encoding="utf-8"))
    recalcule = bilan_depuis(events, population_agents(manifeste))
    # Un compteur ajouté après le run (étape 15 : informations manquées) vaut sa valeur initiale.
    defaut = Bilan()
    ecarts = {}
    for k in COMPARABLES:
        valeur = enregistre.get(k, getattr(defaut, k))
        if valeur != recalcule[k]:
            ecarts[k] = {"enregistre": valeur, "rejeu": recalcule[k]}
    return ecarts


async def _afficher(run_id: str, vitesse: float | None, sortie: Callable[[str], None]) -> int:
    from bus.console import formater

    events = charger(run_id)
    sortie(f"Rejeu de {run_id} : {len(events)} événements, aucun appel LLM")
    async for event in rejouer(events, vitesse):
        ligne = formater(event)
        if ligne:
            sortie(ligne)
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rejoue un run depuis son historique.")
    parser.add_argument("run_id")
    parser.add_argument("--vitesse", type=float, help="jours simulés par seconde")
    parser.add_argument("--verifier", action="store_true", help="compare le bilan à bilan.json")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.verifier:
            ecarts = verifier(args.run_id, charger(args.run_id))
            print("Rejeu fidèle : bilan identique." if not ecarts else f"Écarts : {ecarts}")
            sys.exit(1 if ecarts else 0)
        asyncio.run(_afficher(args.run_id, args.vitesse, lambda s: print(s, flush=True)))
    except HistoriqueAbsent as exc:
        parser.exit(1, f"Erreur : {exc}\n")


if __name__ == "__main__":
    main()
