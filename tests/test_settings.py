import os
from pathlib import Path

import pytest

from sim.settings import Settings, load_settings


def test_defaults_match_env_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    assert load_settings(tmp_path / "absent.env") == Settings()


def test_env_file_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_URL=redis://autre:6379/1\nKAFKA_BOOTSTRAP_SERVERS=fichier:9092\n")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "environnement:9092")

    settings = load_settings(env_file)

    assert settings.redis_url == "redis://autre:6379/1"
    assert settings.kafka_bootstrap_servers == "environnement:9092"  # l'environnement prime
    assert "REDIS_URL" not in os.environ  # lire .env ne modifie pas l'environnement
