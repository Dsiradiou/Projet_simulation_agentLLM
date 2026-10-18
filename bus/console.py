"""Client console : affiche en direct les événements d'un run lus sur Kafka (étape 7).

C'est un observateur : il lit tous les topics `sim.*` et n'écrit jamais sur le bus. Un message
non conforme est signalé à l'écran mais pas renvoyé dans `sim.dlq`, sinon chaque console ouverte
dupliquerait les rejets.

Usage, dans un second terminal, avant ou pendant un run :
    python -m bus.console                 # suit le prochain run qui démarre
    python -m bus.console --run <run_id>  # un run précis, relu depuis le début de la rétention
"""

import argparse
import asyncio
import sys

from aiokafka import AIOKafkaConsumer

from bus import events as ev
from bus.producer import ordre_record
from bus.topics import Topic
from sim.domaine import format_sim
from sim.settings import load_settings

_ETIQUETTES = {
    ev.Moteur.LLM: "",
    ev.Moteur.REGLE: " [règle]",
    ev.Moteur.REPLI: " [REPLI]",
}


def formater(event: ev.Envelope) -> str | None:
    """Une ligne lisible par événement, ou None pour ceux qu'on n'affiche pas."""
    quand = format_sim(event.sim_time_occurred)
    qui = event.source_agent_id
    match event:
        case ev.ClockStep():
            return f"\n──────── Jour {event.payload.jour} ────────"
        case ev.Weather():
            m = event.payload
            return f"{quand}  météo       {m.conditions}, {m.temperature_c:g} °C, UV {m.indice_uv}"
        case ev.AgentDecision():
            d = event.payload
            cout = ""
            if d.appel_llm:
                a = d.appel_llm
                essais = f", {a.tentatives} essais" if a.tentatives > 1 else ""
                cout = f" ({a.modele}, {a.duree_s:.1f} s, {a.tokens_sortie} tok{essais})"
            porteur = "porteur" if d.porteur else "non porteur"
            return (
                f"{quand}  décision    {qui}{_ETIQUETTES[d.moteur]}{cout}\n"
                f"{'':19}{d.sens.value} · {d.action.value} · {porteur} — « {d.justification} »"
            )
        case ev.AgentMessage():
            m = event.payload
            vers = f" → {', '.join(m.destinataires)}" if m.destinataires else ""
            return (
                f"{quand}  message     {qui} [{m.canal.value}{vers}] « {m.contenu} » "
                f"(lisible {format_sim(m.livraison)})"
            )
        case ev.AgentMove():
            return f"{quand}  déplacement {qui} : {event.payload.depuis} → {event.payload.vers}"
        case ev.Incident():
            i = event.payload
            essai = f" (essai {i.tentative})" if i.tentative else ""
            return f"{quand}  INCIDENT    {i.nature.value}{essai} {i.agent_id or ''} : {i.detail}"
        case ev.Disturbance():
            p = event.payload
            zone = f" [{event.zone}]" if event.zone else ""
            return f"{quand}  événement   {p.nature.value}{zone} : {p.description}"
        case ev.Injection():
            p = event.payload
            zone = f" [{event.zone}]" if event.zone else ""
            return f"{quand}  INJECTION   {p.nature.value}{zone} : {p.description} (jour suivant)"
        case ev.Command():
            return f"{quand}  COMMANDE    {event.payload.commande}"
        case ev.Indicator():
            p = event.payload
            groupe = f" [{p.groupe}]" if p.groupe else ""
            return f"{quand}  indicateur  {p.nom}{groupe} = {p.valeur:.3f}"
        case ev.Rejection():
            return f"{quand}  REJET       {event.payload.topic_origine} : {event.payload.erreur}"
    return f"{quand}  {event.type:<11} {qui}"


async def suivre(run_id: str | None, bootstrap: str) -> None:
    consumer = AIOKafkaConsumer(
        *(t.value for t in Topic),
        bootstrap_servers=bootstrap,
        group_id=None,  # sans groupe : lecteur indépendant, qui ne gêne aucun autre consommateur
        auto_offset_reset="earliest" if run_id else "latest",
        enable_auto_commit=False,
    )
    await consumer.start()
    suivi = run_id
    print(f"En écoute sur {bootstrap} : " + (f"run {run_id}" if run_id else "prochain run"))
    try:
        while True:
            lots = await consumer.getmany(timeout_ms=500)
            # Chaque topic a sa propre partition : on remet le lot dans l'ordre de publication,
            # le même que celui du rejeu.
            records = sorted((r for rs in lots.values() for r in rs), key=ordre_record)
            events: list[ev.Envelope] = []
            for record in records:
                try:
                    events.append(ev.decode(record.value))
                except ev.EventValidationError as exc:
                    print(f"(message non conforme ignoré : {exc})")
            for event in events:
                if suivi is None and isinstance(event, ev.ClockStep):
                    suivi = event.run_id
                    print(f"Run {suivi}")
                if event.run_id != suivi:
                    continue
                ligne = formater(event)
                if ligne:
                    print(ligne, flush=True)
    finally:
        await consumer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Affiche en direct les événements d'un run.")
    parser.add_argument("--run", help="run_id à suivre (défaut : le prochain run qui démarre)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows : accents et symboles
    try:
        asyncio.run(suivre(args.run, load_settings().kafka_bootstrap_servers))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
