"""État courant du monde dans Redis (F3), lu par le pont vers Godot et par l'inspection (D2).

Clés, toutes préfixées par le run pour que deux runs ne se mélangent pas :
- `ephemere:<run_id>:agent:<agent_id>` : hash (role, palier, zone, sens, porteur, jour)
- `ephemere:<run_id>:zones`            : hash zone -> JSON {presents, porteurs}
- `ephemere:<run_id>:monde`            : hash (jour, minute, meteo)
"""

import json

from redis.asyncio import Redis

from agents.base import Agent
from bus.events import WeatherPayload
from sim.domaine import Zone

PREFIXE = "ephemere"
DUREE_DE_VIE_S = 2 * 24 * 3600  # l'état d'un run n'a pas à survivre longtemps


class EtatMonde:
    def __init__(self, redis: Redis, run_id: str) -> None:
        self.redis = redis
        self.run_id = run_id
        self._base = f"{PREFIXE}:{run_id}"

    async def reinitialiser(self) -> None:
        keys = [key async for key in self.redis.scan_iter(f"{self._base}:*")]
        if keys:
            await self.redis.delete(*keys)

    async def ecrire_jour(
        self, jour: int, minute: int, agents: list[Agent], meteo: WeatherPayload | None
    ) -> None:
        zones = {zone: {"presents": 0, "porteurs": 0} for zone in Zone}
        async with self.redis.pipeline(transaction=True) as pipe:
            for agent in agents:
                zones[agent.zone]["presents"] += 1
                zones[agent.zone]["porteurs"] += int(agent.porteur)
                key = f"{self._base}:agent:{agent.agent_id}"
                pipe.hset(
                    key,
                    mapping={
                        "role": agent.role.value,
                        "palier": agent.palier.value if agent.palier else "regles",
                        "zone": agent.zone.value,
                        "sens": agent.sens.value if agent.sens else "",
                        "porteur": int(agent.porteur),
                        "jour": jour,
                    },
                )
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
        raw = await self.redis.hgetall(f"{self._base}:agent:{agent_id}")
        return {k.decode(): v.decode() for k, v in raw.items()}

    async def lire_zones(self) -> dict[str, dict[str, int]]:
        raw = await self.redis.hgetall(f"{self._base}:zones")
        return {k.decode(): json.loads(v) for k, v in raw.items()}
