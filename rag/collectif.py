"""Mémoire collective (début de l'étape 16, décidée par le binôme le 4 nov.) : ce que le groupe a
rendu public, retrouvable par ceux qui pouvaient le savoir.

Ce qui s'y écrit, à la fin de chaque jour et seulement à partir de faits déjà publiés sur le bus :
- les publications « réseaux » (portée : les abonnés de l'auteur et ses proches) ;
- les propos tenus dans une zone (portée : ce lieu) ;
- les perturbations du monde (portée : tous, ou la zone concernée) ;
- un résumé du jour par lieu, calculé par règles et sans LLM (avis donnés, sens dominant,
  messages échangés) : il ne coûte aucun appel et se rejoue à l'identique (E3).

Chaque entrée porte son jour de livraison, sa portée, son auteur et l'événement d'origine
(`cause`, F6). Un agent ne lit que les entrées livrées **avant** son jour (ce qui arrive le jour
même est déjà dans sa perception : on ne le compte pas deux fois, C6), dans sa portée sociale,
d'un autre auteur que lui. Dans le run témoin (`diffusion: false`), rien n'est écrit : sinon le
témoin ne serait plus un témoin.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

import chromadb

from bus import events as ev
from llm.sortie import MOT_DU_SENS
from rag.base import Passage, _nom_collection
from rag.embeddings import Embeddeur
from rag.selection import Candidat, retention
from sim.config import MemoireConfig
from sim.domaine import Canal, Zone, type_de_lieu
from sim.domaine import jour as jour_de

CARACTERES_PAR_FAIT = 160
# Importance d'un fait public, de 1 à 3 : une perturbation marque plus qu'un propos ou un résumé.
IMPORTANCE_MAX = 3
TOUS, LIEU, TYPE, ABONNES = "tous", "lieu", "type", "abonnes"
AUCUN_AUTEUR = ""  # résumé du jour : calculé par le monde, sans auteur


@dataclass(frozen=True)
class FaitPublic:
    """Une entrée de la mémoire collective."""

    source: str  # « collectif:j<jour>:<event_id> » ou « collectif:j<jour>:resume:<lieu> »
    texte: str
    jour: int  # jour de livraison : lisible à partir du lendemain
    portee: str  # tous, lieu, type ou abonnes
    auteur: str = AUCUN_AUTEUR
    lieu: str = ""
    type: str = ""
    cause: str = ""  # événement d'origine (F6) ; vide pour un résumé
    importance: int = 1


def _court(texte: str) -> str:
    texte = " ".join(texte.split())
    if len(texte) <= CARACTERES_PAR_FAIT:
        return texte
    return texte[:CARACTERES_PAR_FAIT].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _portee_de_zone(zone: str) -> dict[str, str]:
    """Une perturbation vise un lieu (« plage_2 ») ou un type de zone qui vaut pour tous ses
    exemplaires (« plage », voir `sim.domaine.concerne`)."""
    if zone in {z.value for z in Zone}:
        return {"portee": TYPE, "type": zone}
    return {"portee": LIEU, "lieu": zone, "type": type_de_lieu(zone).value}


def faits_du_jour(jour: int, events: Iterable[ev.Envelope]) -> list[FaitPublic]:
    """Les faits publics d'un jour, dans l'ordre de publication, puis un résumé par lieu (trié).
    Les messages directs sont privés : ils n'y figurent pas."""
    faits: list[FaitPublic] = []
    avis: dict[str, Counter] = defaultdict(Counter)
    propos: Counter = Counter()
    for e in events:
        match e:
            case ev.AgentMessage() if e.payload.canal is Canal.RESEAUX:
                livraison = jour_de(e.payload.livraison)
                faits.append(
                    FaitPublic(
                        f"collectif:j{livraison}:{e.event_id}",
                        f"Jour {jour}, {e.source_agent_id} a publié : "
                        f"« {_court(e.payload.contenu)} »",
                        jour_de(e.payload.livraison), ABONNES, e.source_agent_id,
                        cause=str(e.event_id),
                    )
                )  # fmt: skip
            case ev.AgentMessage() if e.payload.canal is Canal.ZONE and e.zone:
                lieu, livraison = type_de_lieu(e.zone).value, jour_de(e.payload.livraison)
                faits.append(
                    FaitPublic(
                        f"collectif:j{livraison}:{e.event_id}",
                        f"Jour {jour}, {lieu}, {e.source_agent_id} a dit : "
                        f"« {_court(e.payload.contenu)} »",
                        jour_de(e.payload.livraison), LIEU, e.source_agent_id, e.zone, lieu,
                        cause=str(e.event_id),
                    )
                )  # fmt: skip
                propos[e.zone] += 1
            case ev.Disturbance():
                portee = _portee_de_zone(e.zone) if e.zone else {"portee": TOUS}
                survenue = jour_de(e.sim_time_occurred)
                faits.append(
                    FaitPublic(
                        f"collectif:j{survenue}:{e.event_id}",
                        f"Jour {jour} : {_court(e.payload.description)}",
                        survenue, cause=str(e.event_id), importance=3,
                        **portee,
                    )
                )  # fmt: skip
            case ev.AgentDecision() if e.zone:
                avis[e.zone][e.payload.sens] += 1
    for lieu in sorted(avis):
        n = sum(avis[lieu].values())
        sens, k = max(avis[lieu].items(), key=lambda x: (x[1], x[0].value))
        type_lieu = type_de_lieu(lieu).value
        faits.append(
            FaitPublic(
                f"collectif:j{jour}:resume:{lieu}",
                f"Jour {jour}, {type_lieu} : {n} avis donnés, surtout « {MOT_DU_SENS[sens]} » "
                f"({k} sur {n}) ; {propos[lieu]} propos échangés sur place.",
                jour, LIEU, AUCUN_AUTEUR, lieu, type_lieu,
            )
        )  # fmt: skip
    return faits


class MemoireCollective:
    """Faits publics d'un run, dans une collection ChromaDB à part (`collectif-<run_id>`)."""

    def __init__(self, client: chromadb.ClientAPI, embeddeur: Embeddeur, run_id: str) -> None:
        self.embeddeur = embeddeur
        self.collection = client.get_or_create_collection(
            _nom_collection(f"collectif-{run_id}", embeddeur.modele),
            metadata={"hnsw:space": "cosine"},
        )

    async def consigner(self, faits: list[FaitPublic]) -> int:
        if not faits:
            return 0
        vecteurs = await self.embeddeur.documents([f.texte for f in faits])
        self.collection.upsert(
            ids=[f.source for f in faits],
            embeddings=vecteurs,
            documents=[f.texte for f in faits],
            metadatas=[
                {
                    "jour": f.jour, "portee": f.portee, "auteur": f.auteur, "lieu": f.lieu,
                    "type": f.type, "cause": f.cause, "importance": f.importance,
                }
                for f in faits
            ],
        )  # fmt: skip
        return len(faits)

    def candidats(
        self,
        vecteur: list[float],
        *,
        agent_id: str,
        jour: int,
        lieu: str,
        abonnements: tuple[str, ...] | None,
        contacts: list[str],
        deja_percus: set[str],
        reglage: MemoireConfig,
    ) -> list[Candidat]:
        """Faits publics candidats (`selection.choisir`), filtrés sur la date, la portée sociale
        et l'auteur ; sans ceux qui sont déjà dans la perception du jour (`deja_percus` : leurs
        événements d'origine)."""
        if not self.collection.count():
            return []
        portees: list[dict] = [
            {"portee": TOUS},
            {"$and": [{"portee": LIEU}, {"lieu": lieu}]},
            {"$and": [{"portee": TYPE}, {"type": type_de_lieu(lieu).value}]},
        ]
        if abonnements is None:  # agents de test : reçoivent toutes les publications
            portees.append({"portee": ABONNES})
        elif suivis := sorted(set(abonnements) | set(contacts)):
            portees.append({"$and": [{"portee": ABONNES}, {"auteur": {"$in": suivis}}]})
        filtre = {
            "$and": [
                {"jour": {"$lt": jour}},  # livré avant aujourd'hui (C6)
                {"auteur": {"$ne": agent_id}},  # ses propres paroles sont dans sa mémoire
                {"$or": portees},
            ]
        }
        r = self.collection.query(
            query_embeddings=[vecteur],
            n_results=min(reglage.candidats + len(deja_percus), self.collection.count()),
            where=filtre,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        prox = reglage.proximite
        resultat = []
        for i, texte, meta, dist, vec in zip(
            r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0],
            r["embeddings"][0], strict=True,
        ):  # fmt: skip
            if meta["cause"] and meta["cause"] in deja_percus:
                continue
            auteur, portee = str(meta["auteur"]), str(meta["portee"])
            if auteur in contacts:
                proximite = prox.proche
            elif abonnements is not None and auteur in abonnements:
                proximite = prox.abonnement
            elif portee in (LIEU, TYPE):
                proximite = prox.zone
            else:
                proximite = prox.tous if portee == TOUS else prox.abonnement
            importance = int(meta["importance"]) / IMPORTANCE_MAX
            reste = retention(jour - int(meta["jour"]), importance, reglage)
            if reste < reglage.seuil_retention:
                continue
            passage = Passage(
                i, texte, int(meta["jour"]), "collectif", float(dist), int(meta["importance"])
            )
            resultat.append(
                Candidat(passage, tuple(float(x) for x in vec), reste, importance, proximite)
            )
            if len(resultat) == reglage.candidats:
                break
        return resultat
