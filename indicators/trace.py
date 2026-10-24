"""Traçabilité d'un indicateur (F6) et explication d'une variation marquée (B4).

Une valeur d'indicateur cite dans `caused_by` les décisions qu'elle compte. D'un jour au suivant,
les décisions nouvelles sont celles qui ont fait bouger la valeur ; chacune cite à son tour les
messages et les perturbations que l'agent avait sous les yeux. On remonte ainsi de la valeur
affichée aux échanges qui l'ont précédée, jusqu'au texte des messages.

Usage :
    python -m indicators.trace <run_id> --jour 25 [--nom sens_partage] [--groupe role:sceptique]
    python -m indicators.trace <run_id> --variation      # le jour où la valeur bouge le plus
Écrit aussi le résultat dans docs/mesures/.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import UUID

from bus import events as ev
from indicators.calcul import SENS_PARTAGE
from indicators.rapport import _recalcul
from replay.rejeu import charger
from sim.domaine import jour
from sim.run import RUNS_DIR

OUT_DIR = Path("docs/mesures")
TEXTE_MAX = 200


def indicateurs_du_run(run_id: str, events: list[ev.Envelope], runs_dir: Path) -> list:
    enregistres = [e for e in events if isinstance(e, ev.Indicator)]
    if enregistres:
        return enregistres
    manifeste = json.loads((runs_dir / run_id / "manifest.json").read_text(encoding="utf-8"))
    return _recalcul(run_id, manifeste["seed"], events)


def serie_de(indicateurs: list, nom: str, groupe: str | None) -> dict[int, ev.Indicator]:
    return {
        jour(i.sim_time_occurred): i
        for i in indicateurs
        if i.payload.nom == nom and i.payload.groupe == groupe
    }


def jour_de_variation(serie: dict[int, ev.Indicator]) -> int:
    """Jour où la valeur change le plus par rapport à la veille."""
    jours = sorted(serie)
    if len(jours) < 2:
        return jours[0]
    return max(
        jours[1:],
        key=lambda j: (
            (abs(serie[j].payload.valeur - serie[j - 1].payload.valeur), -j)
            if j - 1 in serie
            else (0.0, -j)
        ),
    )


def _court(texte: str) -> str:
    texte = texte.replace("\n", " ").replace("|", "/")
    return texte if len(texte) <= TEXTE_MAX else texte[: TEXTE_MAX - 1] + "…"


def tracer(
    events: list[ev.Envelope], indicateurs: list, nom: str, groupe: str | None, j: int
) -> str:
    par_id: dict[UUID, ev.Envelope] = {e.event_id: e for e in events}
    serie = serie_de(indicateurs, nom, groupe)
    if j not in serie:
        raise ValueError(f"pas de valeur de {nom} ({groupe or 'global'}) au jour {j}")
    courant, veille = serie[j], serie.get(j - 1)
    comptees = set(courant.caused_by)
    avant = set(veille.caused_by) if veille else set()
    nouvelles = [par_id[c] for c in courant.caused_by if c not in avant and c in par_id]
    # Sens de chaque agent la veille, pour lire le basculement.
    sens_veille = {
        par_id[c].source_agent_id: par_id[c].payload.sens.value for c in avant if c in par_id
    }
    lignes = [
        f"# Trace de `{nom}` ({groupe or 'global'}), jour {j}",
        "",
        f"- Valeur : **{courant.payload.valeur:.3f}**"
        + (f" (veille : {veille.payload.valeur:.3f})" if veille else ""),
        f"- Événement : `{courant.event_id}` ; décisions comptées : {len(comptees)}",
    ]
    if courant.payload.repartition is not None:
        lignes.append(
            f"- Répartition : {courant.payload.repartition}"
            + (f" (veille : {veille.payload.repartition})" if veille else "")
        )
    lignes += ["", f"## Décisions nouvelles depuis la veille ({len(nouvelles)})", ""]
    lignes += [
        "| Agent | Sens | Action | Motif | Justification |",
        "| --- | --- | --- | --- | --- |",
    ]
    expediteurs: Counter[str] = Counter()
    messages_cites: dict[UUID, ev.AgentMessage] = {}
    perturbations: dict[UUID, ev.Disturbance] = {}
    for d in sorted(nouvelles, key=lambda e: e.source_agent_id):
        p = d.payload
        ancien = sens_veille.get(d.source_agent_id)
        sens = f"{ancien} → {p.sens.value}" if ancien and ancien != p.sens.value else p.sens.value
        motif = p.motif.value if p.motif else "—"
        lignes.append(
            f"| {d.source_agent_id} | {sens} | {p.action.value} | {motif} "
            f"| {_court(p.justification)} |"
        )
        for c in d.caused_by:
            cause = par_id.get(c)
            if isinstance(cause, ev.AgentMessage):
                messages_cites[c] = cause
                expediteurs[cause.source_agent_id] += 1
            elif isinstance(cause, ev.Disturbance):
                perturbations[c] = cause
    lignes += [
        "",
        f"## Échanges qui ont précédé ces décisions ({len(messages_cites)} messages)",
        "",
    ]
    if expediteurs:
        lignes.append(
            "Voix les plus entendues : "
            + ", ".join(f"{a} ({n})" for a, n in expediteurs.most_common(6))
        )
        lignes += ["", "| Jour | De | Canal | Message |", "| --- | --- | --- | --- |"]
        for m in sorted(messages_cites.values(), key=lambda m: (m.sim_time_occurred, m.event_id)):
            lignes.append(
                f"| {jour(m.sim_time_occurred)} | {m.source_agent_id} | {m.payload.canal.value} "
                f"| {_court(m.payload.contenu)} |"
            )
    else:
        lignes.append("Aucun message : ces décisions viennent d'autres déclencheurs.")
    if perturbations:
        lignes += ["", "## Perturbations perçues", ""]
        for p in perturbations.values():
            lignes.append(f"- Jour {jour(p.sim_time_occurred)}, {p.payload.nature.value} : "
                          f"{_court(p.payload.description)}")  # fmt: skip
    return "\n".join(lignes) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--nom", default=SENS_PARTAGE)
    parser.add_argument("--groupe", default=None, help="role:<rôle> ou zone:<zone> ; global sinon")
    quand = parser.add_mutually_exclusive_group(required=True)
    quand.add_argument("--jour", type=int)
    quand.add_argument("--variation", action="store_true", help="jour de la plus forte variation")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    events = charger(args.run_id, RUNS_DIR)
    indicateurs = indicateurs_du_run(args.run_id, events, RUNS_DIR)
    j = args.jour or jour_de_variation(serie_de(indicateurs, args.nom, args.groupe))
    texte = tracer(events, indicateurs, args.nom, args.groupe, j)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"trace_{args.run_id}_{args.nom}_j{j}_{datetime.now():%H%M}.md"
    chemin.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"Écrit dans {chemin}")


if __name__ == "__main__":
    main()
