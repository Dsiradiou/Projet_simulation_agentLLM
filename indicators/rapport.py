"""Rapport des indicateurs d'un ou plusieurs runs, depuis leur seul historique (étape 14).

Pour chaque run : sens partagé, information mutuelle rôle × sens (et sa p-valeur), adoption et
taux de dérive, jour par jour ; sens par rôle en fin de run ; adoption au premier contact ;
dérives détectées, avec des exemples à vérifier à la main. Plusieurs runs (un run avec diffusion
et son témoin sans diffusion, B3) sont comparés jour par jour.

Les indicateurs viennent des événements `indicateur.valeur` enregistrés pendant le run ; un run
antérieur à l'étape 14 est recalculé avec le même code (`indicators.service.CalculateurDuRun`).

Usage :
    python -m indicators.rapport <run_id> [<run_id> ...] [--titre "C1 contre témoin"]
Écrit un résumé Markdown dans docs/mesures/.
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bus import events as ev
from indicators.calcul import ADOPTION, INFORMATION_MUTUELLE, INFORMATION_MUTUELLE_P, SENS_PARTAGE
from indicators.derives import Detecteur
from indicators.service import CalculateurDuRun
from replay.rejeu import charger
from sim.domaine import jour
from sim.run import RUNS_DIR

OUT_DIR = Path("docs/mesures")
EXEMPLES_MAX = 12


@dataclass
class Serie:
    run_id: str
    recalcule: bool
    par_jour: dict[int, dict[tuple[str, str | None], ev.Indicator]] = field(default_factory=dict)
    premieres: Counter[str] = field(default_factory=Counter)  # premières décisions LLM
    exemples: list[tuple[int, str, list[str], str]] = field(default_factory=list)
    derives: Counter[str] = field(default_factory=Counter)
    examinees: int = 0

    def valeur(self, j: int, nom: str, groupe: str | None = None) -> float | None:
        i = self.par_jour.get(j, {}).get((nom, groupe))
        return i.payload.valeur if i else None

    @property
    def jours(self) -> list[int]:
        return sorted(self.par_jour)


def serie(run_id: str, runs_dir: Path = RUNS_DIR) -> Serie:
    events = charger(run_id, runs_dir)
    indicateurs = [e for e in events if isinstance(e, ev.Indicator)]
    manifeste = json.loads((runs_dir / run_id / "manifest.json").read_text(encoding="utf-8"))
    recalcule = not indicateurs
    if recalcule:  # run antérieur à l'étape 14 : même calcul, après coup
        indicateurs = _recalcul(run_id, manifeste["seed"], events)
    s = Serie(run_id, recalcule)
    for i in indicateurs:
        s.par_jour.setdefault(jour(i.sim_time_occurred), {})[(i.payload.nom, i.payload.groupe)] = i
    # Premières décisions LLM, et dérives détaillées (exemples).
    vus: set[str] = set()
    detecteur = Detecteur()
    for e in avec_fins_de_jour(run_id, events):
        detecteur.appliquer(e)
        if isinstance(e, ev.AgentDecision) and e.payload.moteur is ev.Moteur.LLM:
            if e.source_agent_id not in vus:
                vus.add(e.source_agent_id)
                s.premieres["porteur" if e.payload.porteur else "non_porteur"] += 1
        if isinstance(e, ev.ClockEnd):
            _examiner(s, detecteur, e.payload.jour)
    return s


def _examiner(s: Serie, detecteur: Detecteur, j: int) -> None:
    n, signalements = detecteur.fin_du_jour(j)
    s.examinees += n
    for sig in signalements:
        s.derives.update(d.value for d in sig.derives)
        if len(s.exemples) < EXEMPLES_MAX:
            s.exemples.append((sig.jour, sig.agent_id, [d.value for d in sig.derives], sig.texte))


def avec_fins_de_jour(run_id: str, events: list[ev.Envelope]) -> list[ev.Envelope]:
    """Runs antérieurs à l'étape 14 (sans `horloge.fin_jour`) : la fin d'un jour est placée
    juste avant le début du suivant, et après le dernier événement."""
    if any(isinstance(e, ev.ClockEnd) for e in events):
        return events
    resultat: list[ev.Envelope] = []
    dernier = 0
    for e in events:
        if isinstance(e, ev.ClockStep):
            if dernier:
                resultat.append(_fin(run_id, dernier))
            dernier = e.payload.jour
        resultat.append(e)
    return resultat + ([_fin(run_id, dernier)] if dernier else [])


def _recalcul(run_id: str, graine: int, events: list[ev.Envelope]) -> list[ev.Indicator]:
    calcul = CalculateurDuRun(run_id, graine)
    return [i for e in avec_fins_de_jour(run_id, events) for i in calcul.appliquer(e)]


def _fin(run_id: str, j: int) -> ev.ClockEnd:
    minute = j * 24 * 60 - 1
    return ev.ClockEnd(
        run_id=run_id,
        sim_time_occurred=minute,
        sim_time_processed=minute,
        source_agent_id="horloge",
        payload=ev.ClockEndPayload(jour=j),
    )


def _f(v: float | None, chiffres: int = 2) -> str:
    return "—" if v is None else f"{v:.{chiffres}f}"


def rapport(series: list[Serie], titre: str) -> str:
    lignes = [f"# {titre}", "", "## Synthèse", ""]
    lignes += [
        "| Run | Jours | Sens partagé J1 → fin | Information mutuelle J1 → fin (p fin) "
        "| Adoption fin | Porteurs à la 1re décision LLM | Dérives (décisions LLM) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in series:
        d, f = s.jours[0], s.jours[-1]
        premiers = s.premieres["porteur"], sum(s.premieres.values())
        signalees = sum(s.derives.values())
        lignes.append(
            f"| `{s.run_id}`{' (recalculé)' if s.recalcule else ''} | {f} "
            f"| {_f(s.valeur(d, SENS_PARTAGE))} → {_f(s.valeur(f, SENS_PARTAGE))} "
            f"| {_f(s.valeur(d, INFORMATION_MUTUELLE))} → {_f(s.valeur(f, INFORMATION_MUTUELLE))} "
            f"({_f(s.valeur(f, INFORMATION_MUTUELLE_P), 3)}) "
            f"| {_f(s.valeur(f, ADOPTION))} | {premiers[0]} / {premiers[1]} "
            f"| {signalees} signes sur {s.examinees} |"
        )
    lignes += ["", "## Jour par jour", ""]
    entetes = [f"{n} ({s.run_id[-12:]})" for s in series for n in ("C", "IM", "p", "adoption")]
    lignes.append("| Jour | " + " | ".join(entetes) + " |")
    lignes.append("| --- | " + " | ".join("---" for _ in entetes) + " |")
    for j in sorted(set().union(*(s.jours for s in series))):
        cellules = []
        for s in series:
            cellules += [
                _f(s.valeur(j, SENS_PARTAGE)),
                _f(s.valeur(j, INFORMATION_MUTUELLE)),
                _f(s.valeur(j, INFORMATION_MUTUELLE_P), 3),
                _f(s.valeur(j, ADOPTION)),
            ]
        lignes.append(f"| {j} | " + " | ".join(cellules) + " |")
    for s in series:
        lignes += ["", f"## `{s.run_id}`", "", "Sens par rôle (premier et dernier jour) :", ""]
        lignes += ["| Rôle | Jour 1 | Fin |", "| --- | --- | --- |"]
        d, f = s.jours[0], s.jours[-1]
        roles = sorted({g for (n, g) in s.par_jour[f] if n == SENS_PARTAGE and g and
                        g.startswith("role:")})  # fmt: skip
        for g in roles:
            debut = s.par_jour[d].get((SENS_PARTAGE, g))
            fin = s.par_jour[f][(SENS_PARTAGE, g)]
            lignes.append(
                f"| {g.removeprefix('role:')} | {debut.payload.repartition if debut else '—'} "
                f"| {fin.payload.repartition} |"
            )
        if s.derives:
            lignes += ["", f"Dérives : {dict(s.derives)} sur {s.examinees} décisions LLM.", ""]
            lignes += ["| Jour | Agent | Signes | Texte |", "| --- | --- | --- | --- |"]
            for j, agent, signes, texte in s.exemples:
                propre = texte.replace("|", "/").replace("\n", " ")[:220]
                lignes.append(f"| {j} | {agent} | {', '.join(signes)} | {propre} |")
    return "\n".join(lignes) + "\n"


def sens_par_role(s: Serie, j: int) -> dict[str, dict[str, int]]:
    return {
        g.removeprefix("role:"): dict(i.payload.repartition or {})
        for (n, g), i in s.par_jour.get(j, {}).items()
        if n == SENS_PARTAGE and g and g.startswith("role:")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--titre", default="Indicateurs d'émergence")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texte = rapport([serie(r) for r in args.runs], args.titre)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"indicateurs_{datetime.now():%Y-%m-%d_%H%M}.md"
    chemin.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"Écrit dans {chemin}")


if __name__ == "__main__":
    main()
