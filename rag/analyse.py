"""Ce que les agents LLM ont vu de leur base de connaissances, run par run (étape 15 ter).

Lu dans le seul historique : chaque décision publie ses `sources` (F2, F6), c'est-à-dire les
passages du corpus et les souvenirs restés dans le prompt après sa réduction. On compare avec ce
qui était demandé (`rag.passages`, `rag.souvenirs`) et disponible (un souvenir par décision
précédente de l'agent) : part des souvenirs montrés, décisions privées de mémoire (A1), passages
retirés (F1), par palier et par tranche de dix jours. Marche aussi pour les runs anciens.

Usage :
    python -m rag.analyse <run_id> [<run_id> ...] [--titre "Avant et après les souvenirs compacts"]
Écrit un résumé Markdown dans docs/mesures/.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bus import events as ev
from replay.rejeu import charger
from sim.domaine import jour
from sim.run import RUNS_DIR

OUT_DIR = Path("docs/mesures")
PALIERS = ("petit", "moyen", "grand")


@dataclass
class VueRag:
    run_id: str
    passages_demandes: int
    souvenirs_demandes: int
    decisions: int = 0
    souvenirs_disponibles: int = 0
    souvenirs_montres: int = 0
    passages_montres: int = 0
    sans_un_souvenir: int = 0  # au moins un souvenir disponible retiré du prompt
    sans_aucun_souvenir: int = 0  # des souvenirs disponibles, aucun montré
    passage_retire: int = 0
    ages_souvenirs: list[int] = field(default_factory=list)  # jours écoulés depuis chaque souvenir
    par_palier: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    par_tranche: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))


def mesurer(run_id: str, events: list[ev.Envelope], passages: int, souvenirs: int) -> VueRag:
    vue = VueRag(run_id, passages, souvenirs)
    decisions_avant: Counter[str] = Counter()
    decisions = sorted(
        (e for e in events if isinstance(e, ev.AgentDecision) and e.payload.palier is not None),
        key=lambda e: (e.sim_time_occurred, e.source_agent_id),
    )
    for e in decisions:
        sources = e.payload.sources
        montres_p = sum(s.startswith("corpus:") for s in sources)
        montres_s = sum(s.startswith("memoire:") for s in sources)
        jour_decision = jour(e.sim_time_occurred)
        disponibles = min(souvenirs, decisions_avant[e.source_agent_id])
        decisions_avant[e.source_agent_id] += 1
        if not sources and e.payload.moteur is ev.Moteur.REPLI:
            continue  # repli sans prompt exploitable : rien à compter
        manque_s = disponibles > montres_s
        vue.decisions += 1
        vue.souvenirs_disponibles += disponibles
        vue.souvenirs_montres += montres_s
        vue.passages_montres += montres_p
        vue.sans_un_souvenir += manque_s
        vue.sans_aucun_souvenir += disponibles > 0 and montres_s == 0
        vue.passage_retire += montres_p < passages
        vue.ages_souvenirs += [
            jour_decision - int(s.rsplit(":j", 1)[1]) for s in sources if s.startswith("memoire:")
        ]
        palier = e.payload.palier.value
        vue.par_palier[palier]["decisions"] += 1
        vue.par_palier[palier]["sans_un_souvenir"] += manque_s
        vue.par_palier[palier]["passage_retire"] += montres_p < passages
        tranche = (jour(e.sim_time_occurred) - 1) // 10
        vue.par_tranche[tranche]["decisions"] += 1
        vue.par_tranche[tranche]["sans_un_souvenir"] += manque_s
        vue.par_tranche[tranche]["sans_aucun_souvenir"] += disponibles > 0 and montres_s == 0
    return vue


def _pc(n: int, total: int) -> str:
    return f"{100 * n / total:.0f} %" if total else "—"


def _age(ages: list[int]) -> str:
    return f"{statistics.median(ages):g} j" if ages else "—"


def rapport(vues: list[VueRag], titre: str) -> str:
    lignes = [f"# {titre}", "", f"Généré le {datetime.now():%Y-%m-%d %H:%M}.", ""]
    lignes += [
        "| Run | Décisions LLM | Souvenirs montrés / disponibles | Privées d'un souvenir | "
        "Privées de tout souvenir | Âge médian d'un souvenir montré | Passages montrés | "
        "Privées d'un passage |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in vues:
        lignes.append(
            f"| `{v.run_id}` | {v.decisions} | {v.souvenirs_montres} / {v.souvenirs_disponibles} "
            f"({_pc(v.souvenirs_montres, v.souvenirs_disponibles)}) | "
            f"{_pc(v.sans_un_souvenir, v.decisions)} | {_pc(v.sans_aucun_souvenir, v.decisions)} | "
            f"{_age(v.ages_souvenirs)} | "
            f"{_pc(v.passages_montres, v.passages_demandes * v.decisions)} | "
            f"{_pc(v.passage_retire, v.decisions)} |"
        )
    for v in vues:
        lignes += ["", f"## `{v.run_id}`", ""]
        lignes += [
            f"Demandés par décision : {v.passages_demandes} passages, "
            f"{v.souvenirs_demandes} souvenirs.",
            "",
            "| Palier | Décisions | Privées d'un souvenir | Privées d'un passage |",
            "| --- | --- | --- | --- |",
        ]
        for palier in PALIERS:
            c = v.par_palier.get(palier)
            if c:
                lignes.append(
                    f"| {palier} | {c['decisions']} | {_pc(c['sans_un_souvenir'], c['decisions'])}"
                    f" | {_pc(c['passage_retire'], c['decisions'])} |"
                )
        lignes += [
            "",
            "| Jours | Décisions | Privées d'un souvenir | Privées de tout souvenir |",
            "| --- | --- | --- | --- |",
        ]
        for t, c in sorted(v.par_tranche.items()):
            lignes.append(
                f"| {10 * t + 1}–{10 * t + 10} | {c['decisions']} | "
                f"{_pc(c['sans_un_souvenir'], c['decisions'])} | "
                f"{_pc(c['sans_aucun_souvenir'], c['decisions'])} |"
            )
        bilan = RUNS_DIR / v.run_id / "bilan.json"
        if bilan.exists():
            b = json.loads(bilan.read_text(encoding="utf-8"))
            if "retraits_prompt" in b:  # compté par l'agent depuis l'étape 15 ter
                retraits = ", ".join(f"{k} {n}" for k, n in b["retraits_prompt"].items()) or "aucun"
                lignes += ["", f"Retraits faits par la réduction du prompt : {retraits}."]
    return "\n".join(lignes) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--titre", default="Ce que les agents ont vu de leur base de connaissances")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vues = []
    for run_id in args.runs:
        config = json.loads((RUNS_DIR / run_id / "manifest.json").read_text(encoding="utf-8"))
        rag = config["config"]["rag"]
        vues.append(mesurer(run_id, charger(run_id), rag["passages"], rag["souvenirs"]))
    texte = rapport(vues, args.titre)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"rag_{datetime.now():%Y-%m-%d_%H%M}.md"
    chemin.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"Écrit dans {chemin}")


if __name__ == "__main__":
    main()
