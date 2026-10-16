from pathlib import Path

import pytest
import yaml

from agents.roles import Role
from sim.config import ConfigError, Mode, load_config

DEFAULT = Path(__file__).parent.parent / "configs" / "default.yaml"


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _default_data() -> dict:
    return yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))


def test_default_config_matches_design() -> None:
    config = load_config(DEFAULT)
    assert config.nb_agents == 120
    assert config.simulation.duree_jours == 90
    assert config.simulation.mode is Mode.SYNCHRONE
    assert set(config.population) == set(Role)


def test_agent_count_comes_from_config(tmp_path: Path) -> None:
    data = _default_data()
    data["population"] = {"pharmacien": 1, "baigneur_occasionnel": 2, "meteo": 1}
    assert load_config(_write(tmp_path, data)).nb_agents == 4


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("simulation", "mode", "parallele"),
        ("simulation", "duree_jours", 0),
        ("run", "seed", -1),
        ("monde", "brand_name", "  "),
        ("population", "astronaute", 3),
        ("population", "vendeur", -2),
        ("simulation", "duree_jour", 90),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, section: str, key: str, value) -> None:
    data = _default_data()
    data[section][key] = value
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, data))


def test_empty_population_is_rejected(tmp_path: Path) -> None:
    data = _default_data()
    data["population"] = {}
    with pytest.raises(ConfigError, match="aucun agent"):
        load_config(_write(tmp_path, data))


def test_missing_file_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="lecture impossible"):
        load_config(tmp_path / "absent.yaml")
