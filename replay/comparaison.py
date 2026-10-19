"""Comparaison de runs à partir de leur seul historique (étape 13, base de E1 et E2).

Pour chaque run : la courbe des porteurs jour par jour (dernière décision de chaque agent), le
pic (jour et hauteur), le déclin depuis le pic, la répartition des sens en fin de run, et le coût.
Plusieurs runs d'une même configuration (graines différentes) sont résumés par leur moyenne et
leur écart-type jour par jour (E2 : dispersion entre runs).

Usage :
    python -m replay.comparaison <run_id> [<run_id> ...] [--jours 30] [--titre "C0 contre C1"]
Écrit un résumé Markdown dans docs/mesures/.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from replay.historique import connexion
from sim.run import RUNS_DIR

OUT_DIR = Path("docs/mesures")
SENS = ("efficace", "tendance", "banal", "depasse")


@dataclass
class Courbe:
    run_id: str
    configuration: str
    agents: int
    porteurs: list[int] = field(default_factory=list)  # indice : jour - 1
    sens_fin: dict[str, int] = field(default_factory=dict)
    appels_llm: int = 0
    duree_s: float = 0.0
    messages: int = 0

    @property
    def pic(self) -> tuple[int, int]:
        hauteur = max(self.porteurs)
        return self.porteurs.index(hauteur) + 1, hauteur

    @property
    def declin(self) -> int:
        """Porteurs perdus entre le pic et le dernier jour : un déclin en cours de saison."""
        return self.pic[1] - self.porteurs[-1]


def courbes(
    run_ids: list[str], jours: int | None = None, runs_dir: Path = RUNS_DIR
) -> list[Courbe]:
    con = connexion(runs_dir)
    from replay.historique import REQUETES

    lignes = con.execute(
        f"SELECT * FROM ({REQUETES['adoption_par_jour']}) WHERE list_contains(?, run_id)",
        [run_ids],
    ).fetchall()
    par_run: dict[str, list[tuple]] = defaultdict(list)
    for ligne in lignes:
        par_run[ligne[0]].append(ligne)
    resultat = []
    for run_id in run_ids:
        if run_id not in par_run:
            raise ValueError(f"aucun historique pour {run_id}")
        jours_run = sorted(par_run[run_id], key=lambda r: r[1])
        if jours:
            jours_run = jours_run[:jours]
        dernier = jours_run[-1]
        bilan = json.loads((runs_dir / run_id / "bilan.json").read_text(encoding="utf-8"))
        configuration = run_id.split("-", 2)[2].rsplit("-s", 1)[0]
        resultat.append(
            Courbe(
                run_id=run_id,
                configuration=configuration,
                agents=dernier[3],
                porteurs=[r[2] for r in jours_run],
                sens_fin=dict(zip(SENS, dernier[4:8], strict=True)),
                appels_llm=bilan.get("appels_llm", 0),
                duree_s=bilan.get("duree_reelle_s", 0.0),
                messages=bilan.get("messages", 0),
            )
        )
    return resultat


def _moyenne(courbes_config: list[Courbe]) -> tuple[list[float], list[float]]:
    longueur = min(len(c.porteurs) for c in courbes_config)
    moyennes, ecarts = [], []
    for j in range(longueur):
        valeurs = [c.porteurs[j] for c in courbes_config]
        moyennes.append(statistics.mean(valeurs))
        ecarts.append(statistics.stdev(valeurs) if len(valeurs) > 1 else 0.0)
    return moyennes, ecarts


def rapport(liste: list[Courbe], titre: str) -> str:
    lignes = [f"# {titre}", "", "## Runs", ""]
    lignes += [
        "| Run | Configuration | Agents | Pic (jour, porteurs) | Déclin depuis le pic | Fin : "
        "efficace / tendance / banal / dépassé | Messages | Appels LLM | Durée (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in liste:
        jour, hauteur = c.pic
        sens = " / ".join(str(c.sens_fin[s]) for s in SENS)
        lignes.append(
            f"| `{c.run_id}` | {c.configuration} | {c.agents} | J{jour}, {hauteur} | "
            f"{c.declin} | {sens} | {c.messages} | {c.appels_llm} | {c.duree_s:.0f} |"
        )
    groupes: dict[str, list[Courbe]] = defaultdict(list)
    for c in liste:
        groupes[c.configuration].append(c)
    lignes += ["", "## Porteurs jour par jour (moyenne ± écart-type entre graines)", ""]
    noms = list(groupes)
    lignes.append("| Jour | " + " | ".join(f"{n} ({len(groupes[n])} run(s))" for n in noms) + " |")
    lignes.append("| --- | " + " | ".join("---" for _ in noms) + " |")
    series = {n: _moyenne(groupes[n]) for n in noms}
    longueur = min(len(series[n][0]) for n in noms)
    for j in range(longueur):
        cellules = []
        for n in noms:
            m, e = series[n][0][j], series[n][1][j]
            cellules.append(f"{m:.1f} ± {e:.1f}" if len(groupes[n]) > 1 else f"{m:.0f}")
        lignes.append(f"| {j + 1} | " + " | ".join(cellules) + " |")
    return "\n".join(lignes) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--jours", type=int, help="ne comparer que les premiers jours")
    parser.add_argument("--titre", default="Comparaison de runs")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texte = rapport(courbes(args.runs, args.jours), args.titre)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"comparaison_{datetime.now():%Y-%m-%d_%H%M}.md"
    chemin.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"Écrit dans {chemin}")


if __name__ == "__main__":
    main()
