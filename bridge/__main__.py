"""Lance le pont : python -m bridge [--port 8000]. Écoute sur la machine locale uniquement."""

import argparse

import uvicorn

from bridge.app import creer_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Pont FastAPI entre la simulation et Godot.")
    parser.add_argument("--hote", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(creer_app(), host=args.hote, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
