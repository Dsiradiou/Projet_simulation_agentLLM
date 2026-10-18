"""Mesure de la couche LLM sur les vrais modèles (étape 6) : validité, relances, replis, coûts.

Fait décider de vrais agents LLM (un par rôle, schéma et prompts définitifs de l'étape 6) sur des
situations synthétiques variées, palier par palier comme le fait l'orchestrateur. Rien ne passe
par Kafka : seule la couche LLM est mesurée.

Usage : python -m llm.validation [--decisions 24] [--config configs/default.yaml]
Écrit les mesures brutes (JSON) et un résumé (Markdown) dans docs/mesures/.
"""

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from agents.agent_llm import AgentLLM
from agents.base import Decision, Perception
from agents.regles import tirer_seuil
from agents.roles import PALIER_PAR_ROLE, Palier, Role
from bus.events import (
    AgentMessage,
    AgentMessagePayload,
    Disturbance,
    DisturbancePayload,
    Moteur,
    Perturbation,
    WeatherPayload,
)
from llm.client import ClientOllama
from llm.routeur import Routeur, options_de
from sim.config import Config, load_config
from sim.domaine import Action, Canal, Sens, Zone
from sim.run import agent_rng
from sim.settings import load_settings

OUT_DIR = Path("docs/mesures")

MESSAGES = [
    "La texture ne colle pas et elle tient après la baignade.",
    "Trop cher pour ce que c'est, franchement.",
    "Tout le monde en a déjà à la plage, ça devient banal.",
    "Vu une vidéo hier : application avant le match, très partagée.",
    "Mon ancienne crème fait aussi bien.",
    "Le pharmacien dit de réappliquer toutes les deux heures, quelle que soit la marque.",
    "C'est fini cette mode, plus personne n'en parle.",
]
CANAUX = [Canal.ZONE, Canal.RESEAUX, Canal.DIRECT]
ACTION_HABITUELLE = {
    Sens.EFFICACE: Action.APPLIQUER,
    Sens.TENDANCE: Action.EN_PARLER,
    Sens.BANAL: Action.IGNORER,
    Sens.DEPASSE: Action.ABANDONNER,
}
METEOS = [
    WeatherPayload(indice_uv=9, temperature_c=33, conditions="ensoleille"),
    WeatherPayload(indice_uv=4, temperature_c=26, conditions="nuageux"),
    WeatherPayload(indice_uv=11, temperature_c=38, conditions="canicule"),
]


@dataclass
class Mesure:
    agent_id: str
    role: str
    palier: str
    modele: str
    moteur: str
    tentatives: int
    incidents: list[str]
    sens: str
    action: str
    canal: str | None
    tokens_entree: int
    tokens_sortie: int
    duree_s: float
    justification: str
    message: str | None


def situation(i: int, agent_id: str, contacts: list[str], monde_marque: str) -> Perception:
    """Situation synthétique numéro i : météo, part de porteurs, messages, parfois un événement."""
    messages = tuple(
        AgentMessage(
            run_id="validation",
            sim_time_occurred=0,
            sim_time_processed=0,
            source_agent_id=contacts[(i + k) % len(contacts)],
            zone=Zone.PLAGE,
            payload=AgentMessagePayload(
                canal=CANAUX[(i + k) % len(CANAUX)],
                destinataires=(agent_id,) if CANAUX[(i + k) % len(CANAUX)] is Canal.DIRECT else (),
                contenu=MESSAGES[(i + k) % len(MESSAGES)],
                livraison=0,
            ),
        )
        for k in range(i % 4)
    )
    evenements = ()
    if i % 5 == 4:
        evenements = (
            Disturbance(
                run_id="validation",
                sim_time_occurred=0,
                sim_time_processed=0,
                source_agent_id="controle",
                payload=DisturbancePayload(
                    nature=list(Perturbation)[i % len(Perturbation)],
                    description=f"Rupture de stock de {monde_marque} dans plusieurs pharmacies.",
                ),
            ),
        )
    return Perception(
        jour=2 + i,
        minute=0,
        zone=list(Zone)[i % len(Zone)],
        messages=messages,
        evenements=evenements,
        meteo=METEOS[i % len(METEOS)],
        part_porteurs_zone=(i * 13 % 70) / 100,
    )


def precedente(i: int, palier: Palier) -> Decision | None:
    """Une situation sur trois sans avis préalable, les autres avec un avis de la veille."""
    if i % 3 == 0:
        return None
    sens = list(Sens)[i % 4]
    return Decision(
        action=ACTION_HABITUELLE[sens],
        sens=sens,
        justification="Avis de la veille.",
        porteur=i % 2 == 0,
        moteur=Moteur.LLM,
        palier=palier,
    )


async def _decision(
    config: Config,
    routeur: Routeur,
    semaphore: asyncio.Semaphore,
    i: int,
    role: Role,
    ids: list[str],
) -> Mesure:
    agent_id = f"{role.value}_01"
    palier = PALIER_PAR_ROLE[role]
    rng = agent_rng(config.run.seed, agent_id)
    contacts = [x for x in ids if x != agent_id][:4]
    seuil = tirer_seuil(rng, config.agents.seuil_adoption)
    agent = AgentLLM(
        agent_id, role, Zone.PLAGE, rng, routeur, config.monde, config.run.seed, contacts, seuil
    )
    agent.derniere_decision = precedente(i, palier)
    perception = situation(i, agent_id, contacts, config.monde.brand_name)
    async with semaphore:
        d = await agent.decider(perception)
    assert d.appel_llm
    message = d.messages[0] if d.messages else None
    return Mesure(
        agent_id=agent_id,
        role=role.value,
        palier=palier.value,
        modele=d.appel_llm.modele,
        moteur=d.moteur.value,
        tentatives=d.appel_llm.tentatives,
        incidents=[f"{x.nature.value}: {x.detail}" for x in d.incidents],
        sens=d.sens.value,
        action=d.action.value,
        canal=message.canal.value if message else None,
        tokens_entree=d.appel_llm.tokens_entree,
        tokens_sortie=d.appel_llm.tokens_sortie,
        duree_s=d.appel_llm.duree_s,
        justification=d.justification,
        message=message.contenu if message else None,
    )


async def mesurer(config: Config, n: int) -> tuple[list[Mesure], dict[str, float]]:
    settings = load_settings()
    routeur = Routeur(ClientOllama(settings.ollama_host, options_de(config.llm)), config.llm)
    roles = list(PALIER_PAR_ROLE)
    ids = [f"{r.value}_01" for r in roles]
    mesures: list[Mesure] = []
    durees: dict[str, float] = {}
    for palier in (Palier.PETIT, Palier.MOYEN, Palier.GRAND):
        modele = routeur.modele(palier)
        durees[f"chargement {modele}"] = round(await routeur.preparer(palier), 2)
        roles_palier = [r for r in roles if PALIER_PAR_ROLE[r] is palier]
        semaphore = asyncio.Semaphore(config.llm.parallelisme[palier])
        debut = time.perf_counter()
        mesures += await asyncio.gather(
            *(
                _decision(config, routeur, semaphore, i, roles_palier[i % len(roles_palier)], ids)
                for i in range(n)
            )
        )
        durees[f"groupe {modele}"] = round(time.perf_counter() - debut, 1)
    return mesures, durees


def _resume(mesures: list[Mesure]) -> list[dict]:
    lignes = []
    for modele in sorted({m.modele for m in mesures}, key=lambda x: float(x.split(":")[1][:-1])):
        ms = [m for m in mesures if m.modele == modele]
        durees = sorted(m.duree_s / m.tentatives for m in ms)
        lignes.append(
            {
                "modele": modele,
                "decisions": len(ms),
                "valides_1er_coup": sum(m.tentatives == 1 and m.moteur == "llm" for m in ms),
                "relances": sum(m.tentatives - 1 for m in ms),
                "replis": sum(m.moteur == "repli" for m in ms),
                "latence_mediane_s": round(statistics.median(durees), 2),
                "latence_p90_s": round(durees[int(0.9 * (len(durees) - 1))], 2),
                "tokens_entree": round(statistics.mean(m.tokens_entree / m.tentatives for m in ms)),
                "tokens_sortie": round(statistics.mean(m.tokens_sortie / m.tentatives for m in ms)),
                "sens": dict(Counter(m.sens for m in ms)),
                "messages": dict(Counter(m.canal or "aucun" for m in ms)),
                "hors_francais": sum(
                    _hors_francais(m.justification + (m.message or "")) for m in ms
                ),
            }
        )
    return lignes


def _hors_francais(texte: str) -> bool:
    """Caractère d'un autre système d'écriture (le 1.7b glisse parfois du chinois)."""
    return any(ord(c) > 0x2FFF for c in texte)


def ecrire(mesures: list[Mesure], chargements: dict[str, float], config: Config) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    resume = _resume(mesures)
    base = OUT_DIR / f"couche-llm_{stamp}"
    base.with_suffix(".json").write_text(
        json.dumps(
            {"resume": resume, "chargements": chargements, "mesures": [asdict(m) for m in mesures]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lignes = [
        f"# Couche LLM — {stamp.replace('_', ' ')}",
        "",
        f"- Agents LLM réels (un par rôle), situations synthétiques variées, {len(mesures)} "
        "décisions, groupées par palier avec le parallélisme de la configuration.",
        f"- Réglages : sortie max {config.llm.tokens_sortie_max} tokens, contexte "
        f"{config.llm.contexte_tokens}, température {config.llm.temperature}, délai max "
        f"{config.llm.delai_max_s:g} s, réflexion désactivée.",
        f"- Durées (s) : {chargements}",
        "",
        "| Modèle | Décisions | Valides au 1er coup | Relances | Replis | Latence médiane (s) "
        "| p90 (s) | Tokens entrée | Tokens sortie | Texte hors français |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in resume:
        lignes.append(
            f"| {r['modele']} | {r['decisions']} | {r['valides_1er_coup']} | {r['relances']} "
            f"| {r['replis']} | {r['latence_mediane_s']} | {r['latence_p90_s']} "
            f"| {r['tokens_entree']} | {r['tokens_sortie']} | {r['hors_francais']} |"
        )
    lignes += ["", "## Sens et messages", "", "| Modèle | Sens | Messages |", "| --- | --- | --- |"]
    for r in resume:
        lignes.append(f"| {r['modele']} | {r['sens']} | {r['messages']} |")
    lignes += ["", "## Incidents", ""]
    incidents = [(m.agent_id, i) for m in mesures for i in m.incidents]
    lignes += [f"- `{a}` : {i[:240]}" for a, i in incidents] or ["Aucun."]
    lignes += ["", "## Exemples de justifications", ""]
    for modele in {m.modele for m in mesures}:
        for m in [x for x in mesures if x.modele == modele][:3]:
            msg = f" — message [{m.canal}] « {m.message} »" if m.message else ""
            lignes.append(
                f"- {m.modele}, {m.role} : {m.sens}/{m.action} — « {m.justification} »{msg}"
            )
    base.with_suffix(".md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return base.with_suffix(".md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decisions", type=int, default=24, help="décisions par palier")
    args = parser.parse_args()
    config = load_config(args.config)
    mesures, chargements = asyncio.run(mesurer(config, args.decisions))
    chemin = ecrire(mesures, chargements, config)
    print(chemin.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
