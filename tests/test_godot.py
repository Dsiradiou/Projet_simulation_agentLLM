"""Scène Godot (étape 10) : test de la scène sans écran, à partir de messages du pont.

Ignoré, avec un message, si Godot n'est pas configuré (`GODOT_BIN` dans `.env`).
"""

import subprocess
from pathlib import Path

import pytest

from sim.settings import load_settings

pytestmark = pytest.mark.godot

GODOT = Path(__file__).parent.parent / "godot"


@pytest.fixture(scope="module")
def godot() -> str:
    binaire = load_settings().godot_bin
    if not binaire or not Path(binaire).exists():
        pytest.skip("Godot non configuré : renseigner GODOT_BIN dans .env")
    return binaire


def _godot(binaire: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binaire, "--headless", "--path", str(GODOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def test_scene_applies_bridge_messages(godot: str) -> None:
    _godot(godot, "--import")  # importe les modèles si besoin (premier lancement)
    resultat = _godot(godot, "--script", "res://tests/test_monde.gd")
    sortie = resultat.stdout + resultat.stderr
    assert resultat.returncode == 0, sortie
    assert "test_monde : OK" in sortie
    assert "SCRIPT ERROR" not in sortie
