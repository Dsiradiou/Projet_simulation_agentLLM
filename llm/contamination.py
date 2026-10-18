"""Test de contamination des noms fictifs (étape 11) : que « savent » déjà les modèles d'un nom ?

Un bon nom de marque (ou d'ingrédient) ne doit rien évoquer de réel aux modèles : s'ils croient le
connaître, ils le décriront avec leurs connaissances au lieu de s'en tenir au monde simulé. Pour
chaque nom candidat, chaque modèle Qwen3 est interrogé sans aucun contexte, plusieurs fois, et
l'on compte les réponses où il prétend connaître le nom ou cite une marque réelle.

Usage : python -m llm.contamination [--noms Zelvane Halyss ...] [--essais 4]
Écrit les mesures (JSON) et un résumé (Markdown) dans docs/mesures/.
"""

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ollama import AsyncClient, ResponseError

from sim.config import load_config
from sim.settings import load_settings

OUT_DIR = Path("docs/mesures")

MARQUES_CANDIDATES = [
    "Zelvane", "Halyss", "Corvalie", "Brizéa", "Aubelys",
    "Vaelys", "Nérilia", "Ostrane", "Kaliré", "Tessaly",
]  # fmt: skip
INGREDIENTS_CANDIDATS = ["orvaline", "kalmérine", "tessiflore", "nacrelle"]

# Marques réelles de soin solaire et de cosmétique : en citer une trahit une connaissance réelle.
MARQUES_REELLES = [
    "nivea", "la roche-posay", "avène", "avene", "bioderma", "garnier", "l'oréal", "loreal",
    "vichy", "uriage", "biotherm", "clarins", "lancaster", "hawaiian tropic", "banana boat",
    "neutrogena", "caudalie", "nuxe", "ombrelle", "mixa", "eucerin", "ducray", "isdin",
    "piz buin", "coppertone", "svr", "filorga", "decathlon", "sephora", "yves rocher",
]  # fmt: skip

QUESTIONS = [
    "Que sais-tu de « {nom} » ? Si tu ne le connais pas, dis-le.",
    "« {nom} » : de quoi s'agit-il exactement, et qui le fabrique ou le commercialise ?",
]
SCHEMA = {
    "type": "object",
    "properties": {
        "connu": {"type": "string", "enum": ["oui", "non", "incertain"]},
        "categorie": {"type": "string", "maxLength": 80},
        "precisions": {"type": "string", "maxLength": 300},
    },
    "required": ["connu", "categorie", "precisions"],
}


@dataclass
class Reponse:
    nom: str
    modele: str
    question: int
    connu: str
    categorie: str
    precisions: str
    marque_reelle: str | None


def marque_citee(texte: str) -> str | None:
    bas = texte.lower()
    for marque in MARQUES_REELLES:
        if re.search(rf"(?<![a-zà-ÿ]){re.escape(marque)}(?![a-zà-ÿ])", bas):
            return marque
    return None


async def interroger(client: AsyncClient, modele: str, nom: str, essais: int) -> list[Reponse]:
    reponses = []
    for q, question in enumerate(QUESTIONS):
        for essai in range(essais):
            try:
                r = await client.chat(
                    model=modele,
                    messages=[{"role": "user", "content": question.format(nom=nom)}],
                    format=SCHEMA,
                    think=False,
                    options={"temperature": 0.8, "num_predict": 220, "seed": 1000 * q + essai},
                )
                contenu = r.message.content or "{}"
            except ResponseError as exc:  # génération dégénérée (boucle), coupée par Ollama
                contenu = json.dumps(
                    {"connu": "incertain", "categorie": "", "precisions": f"({exc})"}
                )
            try:
                d = json.loads(contenu)
            except json.JSONDecodeError:
                d = {"connu": "incertain", "categorie": "", "precisions": "(réponse illisible)"}
            texte = f"{d.get('categorie', '')} {d.get('precisions', '')}"
            reponses.append(
                Reponse(
                    nom,
                    modele,
                    q,
                    d.get("connu", ""),
                    d.get("categorie", ""),
                    d.get("precisions", ""),
                    marque_citee(texte),
                )  # fmt: skip
            )
    return reponses


def resumer(reponses: list[Reponse]) -> list[dict]:
    lignes = []
    for nom in dict.fromkeys(r.nom for r in reponses):
        rs = [r for r in reponses if r.nom == nom]
        par_modele = {}
        for modele in dict.fromkeys(r.modele for r in rs):
            m = [r for r in rs if r.modele == modele]
            par_modele[modele] = sum(r.connu == "oui" for r in m) / len(m)
        lignes.append(
            {
                "nom": nom,
                "reponses": len(rs),
                "pretend_connaitre": round(sum(r.connu == "oui" for r in rs) / len(rs), 2),
                "par_modele": {k: round(v, 2) for k, v in par_modele.items()},
                "marques_reelles": sorted({r.marque_reelle for r in rs if r.marque_reelle}),
                "solaire_evoque": round(
                    sum(
                        bool(re.search(r"solaire|soleil|uv", (r.categorie + r.precisions).lower()))
                        for r in rs
                    )
                    / len(rs),
                    2,
                ),  # fmt: skip
                "exemple": next((r.precisions for r in rs if r.connu == "oui"), ""),
            }
        )
    lignes.sort(key=lambda d: (d["pretend_connaitre"], len(d["marques_reelles"])))
    return lignes


async def mesurer(noms: list[str], essais: int) -> list[Reponse]:
    config = load_config("configs/default.yaml")
    client = AsyncClient(host=load_settings().ollama_host)
    reponses: list[Reponse] = []
    for modele in sorted(
        set(config.llm.modeles.values()), key=lambda m: float(m.split(":")[1][:-1])
    ):
        for nom in noms:
            reponses += await interroger(client, modele, nom, essais)
    return reponses


def ecrire(reponses: list[Reponse], resume: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"contamination_{datetime.now():%Y-%m-%d_%H%M}"
    base.with_suffix(".json").write_text(
        json.dumps({"resume": resume, "reponses": [asdict(r) for r in reponses]},
                   ensure_ascii=False, indent=2), encoding="utf-8")  # fmt: skip
    lignes = [
        "# Test de contamination des noms fictifs",
        "",
        f"Chaque nom est soumis sans contexte aux modèles ({len(QUESTIONS)} questions × essais). "
        "« Prétend connaître » : part des réponses `connu = oui`. Marques réelles : marques de "
        "cosmétique citées dans les réponses. Classement : du moins au plus contaminé.",
        "",
        "| Nom | Prétend connaître | Par modèle | Marques réelles citées | Évoque le solaire "
        "| Exemple |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in resume:
        par = ", ".join(f"{k.split(':')[1]} {v:.0%}" for k, v in d["par_modele"].items())
        lignes.append(
            f"| {d['nom']} | {d['pretend_connaitre']:.0%} | {par} | "
            f"{', '.join(d['marques_reelles']) or '—'} | {d['solaire_evoque']:.0%} | "
            f"{d['exemple'][:90].replace('|', '/')} |"
        )
    base.with_suffix(".md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return base.with_suffix(".md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--noms", nargs="+", default=MARQUES_CANDIDATES + INGREDIENTS_CANDIDATS)
    parser.add_argument("--essais", type=int, default=4)
    args = parser.parse_args()
    reponses = asyncio.run(mesurer(args.noms, args.essais))
    print(ecrire(reponses, resumer(reponses)).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
