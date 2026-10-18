"""État courant du monde dans Redis (F3), lu par le pont vers Godot et par l'inspection (D2).

Clés, préfixées par le run pour que deux runs ne se mélangent pas :
- `ephemere:run_courant`               : hash (run_id, statut, jour, minute) du dernier run
  lancé, tenu à jour en continu : le pont y lit l'heure simulée courante pour dater une injection
- `ephemere:<run_id>:agent:<agent_id>` : hash de l'agent (rôle, palier, zone, sens, porteur,
  seuil, dernière décision et sa justification, coût du dernier appel, messages reçus)
- `ephemere:<run_id>:zones`            : hash zone -> JSON {presents, porteurs}
- `ephemere:<run_id>:monde`            : hash (jour, minute, meteo)

Statuts d'un run : `en_cours`, `pause`, `termine`, `arrete`.
"""

import json
from collections.abc import Mapping

from redis.asyncio import Redis

from agents.base import Agent, Perception
from bus.events import WeatherPayload
from sim.domaine import Zone

PREFIXE = "ephemere"
CLE_RUN_COURANT = f"{PREFIXE}:run_courant"
DUREE_DE_VIE_S = 2 * 24 * 3600  # l'état d'un run n'a pas à survivre longtemps
MESSAGES_RECUS_MAX = 5


def _texte(raw: Mapping[bytes, bytes]) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in raw.items()}


def _fiche(agent: Agent, jour: int, recus: Perception | None) -> dict[str, str | int]:
    d = agent.derniere_decision
    appel = d.appel_llm if d else None
    seuil = getattr(agent, "seuil", None)
    messages = [
        {
            "de": m.source_agent_id,
            "canal": m.payload.canal.value,
            "contenu": m.payload.contenu,
            "jour": jour,
        }
        for m in (recus.messages[-MESSAGES_RECUS_MAX:] if recus else ())
    ]
    return {
        "role": agent.role.value,
        "palier": agent.palier.value if agent.palier else "regles",
        "zone": agent.zone.value,
        "sens": agent.sens.value if agent.sens else "",
        "porteur": int(agent.porteur),
        "seuil": "" if seuil is None else f"{seuil:.3f}",
        "jour": jour,
        "action": d.action.value if d else "",
        "moteur": d.moteur.value if d else "",
        "justification": d.justification if d else "",
        "modele": appel.modele if appel else "",
        "tokens_sortie": appel.tokens_sortie if appel else 0,
        "duree_s": appel.duree_s if appel else 0,
        "tentatives": appel.tentatives if appel else 0,
        "messages_recus": json.dumps(messages, ensure_ascii=False),
    }


class EtatMonde:
    def __init__(self, redis: Redis, run_id: str) -> None:
        self.redis = redis
        self.run_id = run_id
        self._base = f"{PREFIXE}:{run_id}"

    async def reinitialiser(self) -> None:
        keys = [key async for key in self.redis.scan_iter(f"{self._base}:*")]
        if keys:
            await self.redis.delete(*keys)

    async def ecrire_statut(self, statut: str, jour: int, minute: int) -> None:
        await self.redis.hset(
            CLE_RUN_COURANT,
            mapping={"run_id": self.run_id, "statut": statut, "jour": jour, "minute": minute},
        )

    async def ecrire_jour(
        self,
        jour: int,
        minute: int,
        agents: list[Agent],
        meteo: WeatherPayload | None,
        perceptions: Mapping[str, Perception] | None = None,
    ) -> None:
        zones = {zone: {"presents": 0, "porteurs": 0} for zone in Zone}
        async with self.redis.pipeline(transaction=True) as pipe:
            for agent in agents:
                zones[agent.zone]["presents"] += 1
                zones[agent.zone]["porteurs"] += int(agent.porteur)
                key = f"{self._base}:agent:{agent.agent_id}"
                recus = perceptions.get(agent.agent_id) if perceptions else None
                pipe.hset(key, mapping=_fiche(agent, jour, recus))
                pipe.expire(key, DUREE_DE_VIE_S)
            pipe.hset(
                f"{self._base}:zones",
                mapping={zone.value: json.dumps(counts) for zone, counts in zones.items()},
            )
            pipe.hset(
                f"{self._base}:monde",
                mapping={
                    "jour": jour,
                    "minute": minute,
                    "meteo": meteo.model_dump_json() if meteo else "",
                },
            )
            for suffix in ("zones", "monde"):
                pipe.expire(f"{self._base}:{suffix}", DUREE_DE_VIE_S)
            await pipe.execute()

    async def lire_agent(self, agent_id: str) -> dict[str, str]:
        return _texte(await self.redis.hgetall(f"{self._base}:agent:{agent_id}"))

    async def lire_agents(self) -> dict[str, dict[str, str]]:
        prefixe = f"{self._base}:agent:"
        cles = sorted([k.decode() async for k in self.redis.scan_iter(f"{prefixe}*")])
        async with self.redis.pipeline(transaction=False) as pipe:
            for cle in cles:
                pipe.hgetall(cle)
            fiches = await pipe.execute()
        return {cle.removeprefix(prefixe): _texte(f) for cle, f in zip(cles, fiches, strict=True)}

    async def lire_zones(self) -> dict[str, dict[str, int]]:
        raw = await self.redis.hgetall(f"{self._base}:zones")
        return {k.decode(): json.loads(v) for k, v in raw.items()}

    async def lire_monde(self) -> dict[str, str]:
        return _texte(await self.redis.hgetall(f"{self._base}:monde"))


async def lire_run_courant(redis: Redis) -> dict[str, str]:
    """Dernier run lancé et son statut, ou {} si aucun."""
    return _texte(await redis.hgetall(CLE_RUN_COURANT))
