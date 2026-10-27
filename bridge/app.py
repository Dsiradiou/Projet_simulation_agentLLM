"""Pont FastAPI entre la simulation et l'interface (Godot à l'étape 10) : D1, D2, D3.

Le pont tourne dans son propre processus et ne parle à la simulation qu'à travers Kafka, Redis et
l'historique Parquet : on peut le laisser ouvert pendant que les runs démarrent et s'arrêtent.

- `WS /ws` : flux des événements du run en cours, précédé d'un instantané du monde (D1).
  `WS /ws?rejeu=<run_id>&vitesse=2` : le même flux, relu depuis l'historique (E3).
- `GET /etat`, `GET /agents`, `GET /agents/{id}` : état du monde et inspection d'un agent (D2).
- `POST /injections` : perturbation appliquée au début du jour simulé suivant (D3).
- `POST /commandes` : pause, reprise ou arrêt, appliqués entre deux jours.
- `GET /runs` : runs enregistrés, rejouables.
- `GET /parametres`, `POST /lancements`, `GET /lancements` : interface de paramétrage ; le pont
  écrit la configuration demandée et lance le run dans son propre processus (`bridge.lancement`).

Messages du WebSocket : les événements du contrat (`docs/evenements/`), en JSON, plus quelques
messages propres au pont, dont le type commence par `pont.` (instantané, nouveau run, rejeu).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from bridge.lancement import DemandeLancement, LancementRefuse, Lanceur, parametres
from bridge.vue import VueMonde
from bus import events as ev
from bus.producer import ordre_record
from bus.topics import Topic
from replay.historique import requete
from replay.rejeu import HistoriqueAbsent, charger, rejouer
from sim.config import ConfigError
from sim.domaine import Lieu, lieux_du_monde, type_de_lieu
from sim.run import RUNS_DIR

STATUTS_ACTIFS = ("en_cours", "pause")
FILE_MAX = 10_000  # au-delà, un client trop lent est déconnecté plutôt que de bloquer le pont


class LecteurEtat(Protocol):
    """Ce que le pont lit de l'état courant (Redis en vrai, un dictionnaire dans les tests)."""

    async def run_courant(self) -> dict[str, str]: ...
    async def monde(self, run_id: str) -> dict[str, str]: ...
    async def zones(self, run_id: str) -> dict[str, dict[str, int]]: ...
    async def agents(self, run_id: str) -> dict[str, dict[str, str]]: ...
    async def agent(self, run_id: str, agent_id: str) -> dict[str, str]: ...


class EtatRedis:
    def __init__(self, redis) -> None:  # redis.asyncio.Redis
        self.redis = redis

    def _etat(self, run_id: str):
        from sim.etat import EtatMonde

        return EtatMonde(self.redis, run_id)

    async def run_courant(self) -> dict[str, str]:
        from sim.etat import lire_run_courant

        return await lire_run_courant(self.redis)

    async def monde(self, run_id: str) -> dict[str, str]:
        return await self._etat(run_id).lire_monde()

    async def zones(self, run_id: str) -> dict[str, dict[str, int]]:
        return await self._etat(run_id).lire_zones()

    async def agents(self, run_id: str) -> dict[str, dict[str, str]]:
        return await self._etat(run_id).lire_agents()

    async def agent(self, run_id: str, agent_id: str) -> dict[str, str]:
        return await self._etat(run_id).lire_agent(agent_id)


class Diffusion:
    """Distribue chaque message du flux en direct à tous les clients connectés."""

    def __init__(self) -> None:
        self._files: set[asyncio.Queue[str]] = set()

    def abonner(self) -> asyncio.Queue[str]:
        file: asyncio.Queue[str] = asyncio.Queue(maxsize=FILE_MAX)
        self._files.add(file)
        return file

    def desabonner(self, file: asyncio.Queue[str]) -> None:
        self._files.discard(file)

    def diffuser(self, message: str) -> None:
        for file in list(self._files):
            try:
                file.put_nowait(message)
            except asyncio.QueueFull:
                self._files.discard(file)

    @property
    def clients(self) -> int:
        return len(self._files)


@dataclass
class Services:
    etat: LecteurEtat
    publier: Callable[[ev.Envelope], Awaitable[None]]
    vue: VueMonde
    diffusion: Diffusion
    runs_dir: Path = RUNS_DIR
    lanceur: Lanceur = field(default_factory=Lanceur)


class SuiviDirect:
    """Lit le bus et suit le dernier run lancé : alimente la vue et la diffusion WebSocket."""

    def __init__(self, bootstrap_servers: str, services: Services) -> None:
        self.services = services
        self._consumer = AIOKafkaConsumer(
            *(t.value for t in Topic),
            bootstrap_servers=bootstrap_servers,
            group_id=None,
            auto_offset_reset="latest",
            enable_auto_commit=False,
        )

    async def demarrer(self) -> None:
        await self._consumer.start()
        courant = await self.services.etat.run_courant()
        self.services.vue.reinitialiser(courant.get("run_id"))

    async def arreter(self) -> None:
        await self._consumer.stop()

    async def boucle(self) -> None:
        while True:
            lots = await self._consumer.getmany(timeout_ms=200)
            for record in sorted((r for rs in lots.values() for r in rs), key=ordre_record):
                try:
                    event = ev.decode(record.value)
                except ev.EventValidationError:
                    continue  # isolé par l'orchestrateur (sim.dlq) ; le pont n'écrit rien
                self.recevoir(event, record.value.decode("utf-8"))

    def recevoir(self, event: ev.Envelope, brut: str) -> None:
        vue, diffusion = self.services.vue, self.services.diffusion
        # Un run commence par son plan (étape 15 bis), ou, avant lui, par le pas du jour 1.
        nouveau = isinstance(event, ev.WorldPlan) or (
            isinstance(event, ev.ClockStep) and event.payload.jour == 1
        )
        if event.run_id != vue.run_id and (nouveau or vue.run_id is None):
            vue.reinitialiser(event.run_id)
            diffusion.diffuser(json.dumps({"type": "pont.nouveau_run", "run_id": event.run_id}))
        if event.run_id == vue.run_id:
            vue.appliquer(event)
            diffusion.diffuser(brut)


# --- requêtes de l'interface -----------------------------------------------------------------


class DemandeInjection(BaseModel):
    nature: ev.Perturbation
    description: str = Field(min_length=1, max_length=500)
    zone: Lieu | None = Field(
        default=None,
        description="Zone concernée : un lieu du run, ou un type de zone pour tous ses "
        "exemplaires ; tout le monde si vide.",
    )


class DemandeCommande(BaseModel):
    commande: Literal["pause", "reprise", "arret"]


async def _run_actif(services: Services) -> tuple[str, int]:
    """Run en cours (ou en pause) et minute simulée courante ; 409 sinon."""
    courant = await services.etat.run_courant()
    if courant.get("statut") not in STATUTS_ACTIFS:
        raise HTTPException(409, "aucun run en cours")
    return courant["run_id"], int(courant.get("minute") or 0)


def _lieux(monde: dict[str, str]) -> list[str]:
    """Zones du run courant ; les sept zones d'origine pour un run qui ne les a pas écrites."""
    return json.loads(monde["lieux"]) if monde.get("lieux") else list(lieux_du_monde())


async def instantane_direct(services: Services) -> dict:
    courant = await services.etat.run_courant()
    run_id = courant.get("run_id")
    monde = await services.etat.monde(run_id) if run_id else {}
    return {
        "type": "pont.instantane",
        "run": courant or None,
        "jour": int(courant.get("jour") or monde.get("jour") or 0),  # jour en cours
        "meteo": json.loads(monde["meteo"]) if monde.get("meteo") else None,
        "lieux": _lieux(monde),
        "zones": await services.etat.zones(run_id) if run_id else {},
        "agents": [
            {"agent_id": a, **_fiche_lisible(f)}
            for a, f in sorted((await services.etat.agents(run_id)).items())
        ]
        if run_id
        else [],
        "perturbations": services.vue.instantane()["perturbations"],
    }


def _fiche_lisible(fiche: dict[str, str]) -> dict:
    fiche = dict(fiche)
    if "messages_recus" in fiche:
        fiche["messages_recus"] = json.loads(fiche["messages_recus"] or "[]")
    if fiche.get("palier") == "regles":
        fiche["regle"] = (
            "seuil d'adoption : adopte si la part de porteurs de sa zone atteint son seuil"
        )
    return fiche


def creer_app(services: Services | None = None, bootstrap_servers: str | None = None) -> FastAPI:
    """Avec `services`, l'application utilise ces dépendances (tests) ; sinon elle se connecte
    à Kafka et Redis au démarrage, d'après `.env`."""

    @asynccontextmanager
    async def cycle(app: FastAPI) -> AsyncIterator[None]:
        if services is not None:
            app.state.services = services
            yield
            return
        from redis.asyncio import Redis

        from bus.producer import EventProducer
        from sim.settings import load_settings

        settings = load_settings()
        bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        redis = Redis.from_url(settings.redis_url)
        producer = EventProducer(bootstrap, producteur="pont")
        await producer.start()
        app.state.services = reels = Services(
            etat=EtatRedis(redis), publier=producer.publish, vue=VueMonde(), diffusion=Diffusion()
        )
        suivi = SuiviDirect(bootstrap, reels)
        await suivi.demarrer()
        tache = asyncio.create_task(suivi.boucle())
        try:
            yield
        finally:
            tache.cancel()
            await suivi.arreter()
            await producer.stop()
            await redis.aclose()

    app = FastAPI(
        title="Pont ÉPHÉMÈRE",
        description="Flux en direct ou en rejeu, inspection des agents, injections et commandes.",
        lifespan=cycle,
    )

    def svc(request: Request) -> Services:
        return request.app.state.services

    @app.get("/etat")
    async def etat(request: Request) -> dict:
        snapshot = await instantane_direct(svc(request))
        snapshot.pop("agents")
        return snapshot

    @app.get("/agents")
    async def agents(request: Request) -> list[dict]:
        s = svc(request)
        run_id = (await s.etat.run_courant()).get("run_id")
        if not run_id:
            return []
        fiches = await s.etat.agents(run_id)
        return [{"agent_id": a, **_fiche_lisible(f)} for a, f in sorted(fiches.items())]

    @app.get("/agents/{agent_id}")
    async def agent(agent_id: str, request: Request) -> dict:
        s = svc(request)
        run_id = (await s.etat.run_courant()).get("run_id")
        fiche = await s.etat.agent(run_id, agent_id) if run_id else {}
        historique = s.vue.agents.get(agent_id)
        if not fiche and historique is None:
            raise HTTPException(404, f"agent inconnu : {agent_id}")
        return {
            "agent_id": agent_id,
            "run_id": run_id,
            "etat": _fiche_lisible(fiche) if fiche else None,
            "historique": historique.detail() if historique else None,
        }

    @app.post("/injections", status_code=202)
    async def injecter(demande: DemandeInjection, request: Request) -> dict:
        s = svc(request)
        run_id, minute = await _run_actif(s)
        if demande.zone is not None:
            lieux = _lieux(await s.etat.monde(run_id))
            if demande.zone not in set(lieux) | {type_de_lieu(lieu).value for lieu in lieux}:
                raise HTTPException(422, f"zone absente de ce run : {demande.zone}")
        injection = ev.Injection(
            run_id=run_id,
            sim_time_occurred=minute,
            sim_time_processed=minute,
            source_agent_id="pont",
            zone=demande.zone,
            payload=ev.InjectionPayload(nature=demande.nature, description=demande.description),
        )
        await s.publier(injection)
        return {
            "event_id": str(injection.event_id),
            "run_id": run_id,
            "prise_en_compte": "au début du prochain jour simulé",
        }

    @app.post("/commandes", status_code=202)
    async def commander(demande: DemandeCommande, request: Request) -> dict:
        s = svc(request)
        run_id, minute = await _run_actif(s)
        commande = ev.Command(
            run_id=run_id,
            sim_time_occurred=minute,
            sim_time_processed=minute,
            source_agent_id="pont",
            payload=ev.CommandPayload(commande=demande.commande),
        )
        await s.publier(commande)
        return {"event_id": str(commande.event_id), "run_id": run_id, "commande": demande.commande}

    @app.get("/runs")
    async def runs(request: Request) -> list[dict]:
        lignes = requete("runs", svc(request).runs_dir)
        return [
            {"run_id": r[0], "config": r[1], "seed": r[2], "debut": str(r[3]), "evenements": r[4]}
            for r in lignes
            if r[4]
        ]

    @app.get("/parametres")
    async def lire_parametres(request: Request, base: str = "default.yaml") -> dict:
        lanceur = svc(request).lanceur
        try:
            return parametres(base, lanceur.configs_dir)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"configuration inconnue : {base}") from exc
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/lancements", status_code=202)
    async def lancer(demande: DemandeLancement, request: Request) -> dict:
        s = svc(request)
        if s.lanceur.actif:
            raise HTTPException(409, "un run lancé depuis l'interface est déjà en cours")
        if (await s.etat.run_courant()).get("statut") in STATUTS_ACTIFS:
            raise HTTPException(409, "un run est déjà en cours : l'arrêter avant d'en lancer un")
        try:
            return s.lanceur.lancer(demande)
        except LancementRefuse as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/lancements")
    async def lancement(request: Request) -> dict:
        return svc(request).lanceur.etat()

    @app.websocket("/ws")
    async def flux(websocket: WebSocket, rejeu: str | None = None, vitesse: float | None = None):
        s = websocket.app.state.services
        await websocket.accept()
        if rejeu:
            await _flux_rejeu(websocket, s, rejeu, vitesse)
            return
        file = s.diffusion.abonner()
        try:
            await websocket.send_json(await instantane_direct(s))
            while True:
                await websocket.send_text(await file.get())
        except WebSocketDisconnect:
            pass
        finally:
            s.diffusion.desabonner(file)

    return app


async def _flux_rejeu(
    websocket: WebSocket, services: Services, run_id: str, vitesse: float | None
) -> None:
    try:
        events = charger(run_id, services.runs_dir)
    except HistoriqueAbsent as exc:
        await websocket.send_json({"type": "pont.erreur", "detail": str(exc)})
        await websocket.close()
        return
    try:
        await websocket.send_json(
            {"type": "pont.rejeu", "run_id": run_id, "evenements": len(events)}
        )
        async for event in rejouer(events, vitesse):
            await websocket.send_text(ev.encode(event).decode("utf-8"))
        await websocket.send_json({"type": "pont.fin_rejeu", "run_id": run_id})
        await websocket.close()
    except WebSocketDisconnect:
        pass
