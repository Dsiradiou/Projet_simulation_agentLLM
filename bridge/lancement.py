"""Lancement d'un run depuis l'interface de paramétrage (Godot).

L'interface choisit une configuration de base (`configs/*.yaml`), puis le nombre d'agents de
chaque rôle, le nombre de zones de chaque type, le mode d'exécution (synchrone ou asynchrone),
le moteur des agents (LLM ou règles, baseline C0), la durée et la graine. Le pont en fait une
variante `herite_de: <base>` écrite dans `runs/lancements/<horodatage>/interface.yaml` (validée
par `sim.config.load_config` avant tout lancement), puis démarre `python -m sim.run` dans un
processus à part. Le run se déroule comme lancé depuis un terminal : son manifeste recopie la
configuration complète (E2), et sa sortie est gardée dans `sortie.log`, à côté de la
configuration.

Le pont ne pilote pas le run qu'il a lancé autrement que par `sim.control` (pause, reprise,
arrêt) : le processus lui survit s'il est fermé, et un seul run lancé par le pont tourne à la fois.
"""

import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from agents.roles import Palier, Role, palier_du_role
from sim.config import Config, ConfigError, Mode, MoteurAgents, load_config
from sim.domaine import ZONES_PAR_TYPE_MAX, Zone
from sim.run import RUNS_DIR

CONFIGS_DIR = Path("configs")
DOSSIER_LANCEMENTS = RUNS_DIR / "lancements"
NOM_CONFIGURATION = "interface.yaml"  # le run_id en reprend le nom : …-interface-s42
AGENTS_PAR_ROLE_MAX = 60
DUREE_MAX_JOURS = 120
ROLES_UNIQUES = (Role.MARQUE_CONCURRENTE, Role.METEO)  # présents ou absents, jamais en nombre
LIGNES_JOURNAL = 12

NombreAgents = Annotated[int, Field(ge=0, le=AGENTS_PAR_ROLE_MAX)]
NombreZones = Annotated[int, Field(ge=1, le=ZONES_PAR_TYPE_MAX)]


class DemandeLancement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = Field(
        default="default.yaml",
        pattern=r"^[a-z0-9_-]+\.yaml$",
        description="Configuration de `configs/` dont la variante hérite.",
    )
    population: dict[Role, NombreAgents] = Field(description="Agents par rôle, tous les rôles.")
    zones: dict[Zone, NombreZones] = Field(default_factory=dict)
    mode: Mode
    moteur: MoteurAgents = MoteurAgents.LLM
    duree_jours: int = Field(ge=1, le=DUREE_MAX_JOURS)
    graine: NonNegativeInt | None = None


class LancementRefuse(ValueError):
    """Demande valide en apparence, mais configuration refusée par `load_config`."""


def bases(configs_dir: Path = CONFIGS_DIR) -> list[str]:
    return sorted(p.name for p in configs_dir.glob("*.yaml"))


def charger_base(nom: str, configs_dir: Path = CONFIGS_DIR) -> Config:
    """Configuration de base ; FileNotFoundError si elle n'existe pas dans `configs/`."""
    if nom not in bases(configs_dir):
        raise FileNotFoundError(nom)
    return load_config(configs_dir / nom)


def parametres(nom: str, configs_dir: Path = CONFIGS_DIR) -> dict:
    """Ce que l'interface affiche : chaque réglage, sa valeur dans la configuration de base et
    ses bornes. L'interface construit son formulaire à partir de cette description, sans
    connaître elle-même les rôles ni les zones."""
    config = charger_base(nom, configs_dir)
    ordre = {Palier.GRAND: 0, Palier.MOYEN: 1, Palier.PETIT: 2, None: 3}
    roles = sorted(Role, key=lambda r: ordre[palier_du_role(r)])
    return {
        "base": nom,
        "bases": bases(configs_dir),
        "roles": [
            {
                "role": role.value,
                "palier": palier.value if (palier := palier_du_role(role)) else "regles",
                "modele": config.llm.modeles[palier] if palier else None,
                "nombre": config.population.get(role, 0),
                "max": 1 if role in ROLES_UNIQUES else AGENTS_PAR_ROLE_MAX,
            }
            for role in roles
        ],
        "zones": [
            {"zone": zone.value, "nombre": config.zones.get(zone, 1), "max": ZONES_PAR_TYPE_MAX}
            for zone in Zone
        ],
        "modes": [m.value for m in Mode],
        "mode": config.simulation.mode.value,
        "moteurs": [m.value for m in MoteurAgents],
        "moteur": config.agents.moteur.value,
        "duree_jours": config.simulation.duree_jours,
        "duree_max": DUREE_MAX_JOURS,
        "graine": config.run.seed,
    }


def ecrire_configuration(
    demande: DemandeLancement,
    dossier: Path,
    configs_dir: Path = CONFIGS_DIR,
    maintenant: datetime | None = None,
) -> Path:
    """Écrit la variante demandée et la valide. LancementRefuse si `load_config` la refuse."""
    base = configs_dir / demande.base
    if not base.is_file():
        raise LancementRefuse(f"configuration de base inconnue : {demande.base}")
    dossier.mkdir(parents=True, exist_ok=True)
    variante = {
        "herite_de": Path(os.path.relpath(base.resolve(), dossier.resolve())).as_posix(),
        "simulation": {"mode": demande.mode.value, "duree_jours": demande.duree_jours},
        "agents": {"moteur": demande.moteur.value},
        "population": {r.value: n for r, n in demande.population.items()},
        "zones": {z.value: n for z, n in demande.zones.items()},
    }
    if demande.graine is not None:
        variante["run"] = {"seed": demande.graine}
    quand = (maintenant or datetime.now()).strftime("%d/%m/%Y à %H:%M")
    chemin = dossier / NOM_CONFIGURATION
    chemin.write_text(
        f"# Écrite par l'interface de paramétrage le {quand}.\n"
        + yaml.safe_dump(variante, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        load_config(chemin)
    except ConfigError as exc:
        raise LancementRefuse(str(exc)) from exc
    return chemin


def _commande_run(config: Path) -> list[str]:
    return [sys.executable, "-m", "sim.run", "--config", str(config)]


class Lanceur:
    """Démarre un run dans son propre processus et en suit l'état (un seul à la fois)."""

    def __init__(
        self,
        dossier: Path = DOSSIER_LANCEMENTS,
        configs_dir: Path = CONFIGS_DIR,
        commande: Callable[[Path], list[str]] = _commande_run,
    ) -> None:
        self.dossier = dossier
        self.configs_dir = configs_dir
        self.commande = commande
        self._processus: subprocess.Popen | None = None
        self._config: Path | None = None

    @property
    def actif(self) -> bool:
        return self._processus is not None and self._processus.poll() is None

    def lancer(self, demande: DemandeLancement, maintenant: datetime | None = None) -> dict:
        maintenant = maintenant or datetime.now()
        dossier = self.dossier / maintenant.strftime("%Y%m%d-%H%M%S")
        config = ecrire_configuration(demande, dossier, self.configs_dir, maintenant)
        options: dict = {}
        if sys.platform == "win32":  # un Ctrl+C dans le terminal du pont n'arrête pas le run
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        env = os.environ | {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        with (dossier / "sortie.log").open("wb") as journal:
            self._processus = subprocess.Popen(
                self.commande(config),
                stdout=journal,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                **options,
            )
        self._config = config
        return self.etat()

    def etat(self) -> dict:
        """`aucun` (rien lancé), `en_cours`, `termine` ou `echec`, avec la fin de la sortie du
        run et son identifiant dès qu'il l'a affiché."""
        if self._processus is None or self._config is None:
            return {"statut": "aucun"}
        code = self._processus.poll()
        lignes = _fin_du_journal(self._config.parent / "sortie.log")
        run_id = next(
            (m.group(1) for ligne in lignes if (m := re.match(r"Run (\S+) :", ligne))), None
        )
        return {
            "statut": "en_cours" if code is None else "termine" if code == 0 else "echec",
            "code": code,
            "pid": self._processus.pid,
            "run_id": run_id,
            "configuration": self._config.as_posix(),
            "journal": (self._config.parent / "sortie.log").as_posix(),
            "dernieres_lignes": lignes[-LIGNES_JOURNAL:],
        }


def _fin_du_journal(chemin: Path) -> list[str]:
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ligne for ligne in texte.splitlines() if ligne.strip()]
