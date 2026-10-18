"""Client de test du pont, en attendant Godot : affiche le flux WebSocket comme la console.

python -m bridge.client                        # run en cours
python -m bridge.client --rejeu <run_id> --vitesse 2
"""

import argparse
import asyncio
import json
import sys
from urllib.parse import urlencode

import websockets

from bus import events as ev
from bus.console import formater


def ligne(message: str) -> str | None:
    """Une ligne lisible par message du pont : événement du contrat ou message `pont.*`."""
    donnees = json.loads(message)
    if str(donnees.get("type", "")).startswith("pont."):
        if donnees["type"] == "pont.instantane":
            run = donnees.get("run") or {}
            return (
                f"[pont] instantané : run {run.get('run_id')} ({run.get('statut')}), "
                f"jour {donnees['jour']}, {len(donnees['agents'])} agents"
            )
        details = {k: v for k, v in donnees.items() if k != "type"}
        return f"[pont] {donnees['type'].removeprefix('pont.')} {details}"
    return formater(ev.decode(message.encode("utf-8")))


async def ecouter(url: str) -> None:
    async with websockets.connect(url, max_size=None) as ws:
        async for message in ws:
            texte = ligne(message)
            if texte:
                print(texte, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Affiche le flux WebSocket du pont.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--rejeu", help="run_id à rejouer depuis l'historique")
    parser.add_argument("--vitesse", type=float, help="jours simulés par seconde (rejeu)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    params = {k: v for k, v in {"rejeu": args.rejeu, "vitesse": args.vitesse}.items() if v}
    url = f"{args.url}?{urlencode(params)}" if params else args.url
    try:
        asyncio.run(ecouter(url))
    except (KeyboardInterrupt, websockets.ConnectionClosed):
        pass


if __name__ == "__main__":
    main()
