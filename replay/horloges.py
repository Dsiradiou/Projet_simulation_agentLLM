"""Les deux horloges d'un run, depuis son seul historique (étape 15, C3 à C5).

Pour chaque run : à quelle heure simulée décide chaque palier, combien de temps simulé dure sa
réflexion (de `sim_time_occurred` à `sim_time_processed`), combien de messages sont lus le jour
même de leur émission et par qui, le délai entre l'émission d'un message et sa lecture, et les
informations manquées du mode asynchrone, par rôle de l'émetteur. Sert à comparer les modes
synchrone et asynchrone sur un même scénario (C5).

Usage :
    python -m replay.horloges <run_id> [<run_id> ...] [--titre "C1 synchrone contre asynchrone"]
Écrit un résumé Markdown dans docs/mesures/.
"""

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bus import events as ev
from replay.rejeu import charger
from sim.domaine import MINUTES_PAR_JOUR

OUT_DIR = Path("docs/mesures")
REGLE = "regle"
PALIERS = (REGLE, "petit", "moyen", "grand")


@dataclass
class Horloges:
    run_id: str
    heures: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    reflexions: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    messages_emis: Counter = field(default_factory=Counter)  # par palier de l'émetteur
    lus: Counter = field(default_factory=Counter)  # par palier du lecteur
    lus_jour_meme: Counter = field(default_factory=Counter)  # (émetteur, lecteur)
    delais_lecture: list[int] = field(default_factory=list)  # minutes simulées
    manquees: int = 0
    manquees_par_role: Counter = field(default_factory=Counter)


def _role(agent_id: str) -> str:
    return agent_id.rsplit("_", 1)[0]


def mesurer(run_id: str, events: list[ev.Envelope]) -> Horloges:
    h = Horloges(run_id)
    messages: dict = {}
    palier_de: dict[str, str] = {}
    for event in events:
        match event:
            case ev.AgentDecision():
                p = event.payload
                palier = p.palier.value if p.palier else REGLE
                palier_de[event.source_agent_id] = palier
                h.heures[palier].append(event.sim_time_processed % MINUTES_PAR_JOUR)
                h.reflexions[palier].append(event.sim_time_processed - event.sim_time_occurred)
                for cause in event.caused_by:
                    message = messages.get(cause)
                    if message is None:
                        continue
                    h.lus[palier] += 1
                    h.delais_lecture.append(event.sim_time_occurred - message.sim_time_processed)
                    meme_jour = (
                        event.sim_time_occurred // MINUTES_PAR_JOUR
                        == message.sim_time_processed // MINUTES_PAR_JOUR
                    )
                    if meme_jour:
                        emetteur = palier_de.get(message.source_agent_id, "?")
                        h.lus_jour_meme[(emetteur, palier)] += 1
            case ev.AgentMessage():
                messages[event.event_id] = event
                h.messages_emis[palier_de.get(event.source_agent_id, "?")] += 1
            case ev.ClockEnd():
                h.manquees += event.payload.informations_manquees
                for cause in event.caused_by:
                    if cause in messages:
                        h.manquees_par_role[_role(messages[cause].source_agent_id)] += 1
    return h


def _heure(minute: float) -> str:
    m = int(minute)
    return f"{m // 60:02d}:{m % 60:02d}"


def _quantile(valeurs: list[int], q: float) -> int:
    ordonnees = sorted(valeurs)
    return ordonnees[min(len(ordonnees) - 1, int(q * len(ordonnees)))]


def rapport(mesures: list[Horloges], titre: str) -> str:
    lignes = [f"# {titre}", ""]
    for h in mesures:
        lignes += [
            f"## `{h.run_id}`",
            "",
            "| Palier | Décisions | Heure médiane (p10 – p90) | Réflexion médiane (max) | "
            "Messages émis | Messages lus |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for palier in PALIERS:
            heures = h.heures.get(palier)
            if not heures:
                continue
            reflexion = h.reflexions[palier]
            lignes.append(
                f"| {palier} | {len(heures)} | {_heure(statistics.median(heures))} "
                f"({_heure(_quantile(heures, 0.1))} – {_heure(_quantile(heures, 0.9))}) | "
                f"{statistics.median(reflexion):.0f} min ({max(reflexion)}) | "
                f"{h.messages_emis.get(palier, 0)} | {h.lus.get(palier, 0)} |"
            )
        delai = (
            f"{statistics.median(h.delais_lecture) / 60:.1f} h"
            if h.delais_lecture
            else "aucun message lu"
        )
        jour_meme = ", ".join(
            f"{emetteur} → {lecteur} : {n}"
            for (emetteur, lecteur), n in h.lus_jour_meme.most_common()
        )
        par_role = ", ".join(f"{r} : {n}" for r, n in h.manquees_par_role.most_common())
        lignes += [
            "",
            f"- Messages lus le jour même de leur émission : **{sum(h.lus_jour_meme.values())}**"
            + (f" ({jour_meme})" if jour_meme else ""),
            f"- Délai médian entre l'émission d'un message et sa lecture : {delai}",
            f"- Informations manquées : **{h.manquees}**"
            + (f" (messages distincts par rôle de l'émetteur : {par_role})" if par_role else ""),
            "",
        ]
    return "\n".join(lignes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--titre", default="Les deux horloges")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texte = rapport([mesurer(r, charger(r)) for r in args.runs], args.titre)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chemin = OUT_DIR / f"horloges_{datetime.now():%Y-%m-%d_%H%M}.md"
    chemin.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"Écrit dans {chemin}")


if __name__ == "__main__":
    main()
