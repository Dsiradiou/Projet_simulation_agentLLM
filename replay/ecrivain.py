"""Écrivain d'historique : lit le bus et enregistre les événements d'un run en Parquet (E3, F5).

C'est un consommateur Kafka comme les autres : ce qui est enregistré est exactement ce qui a
circulé sur le bus. `sim.run` le démarre avant le premier événement et attend, en fin de run,
qu'il ait reçu tout ce que le producteur a publié.

Les événements sont écrits par lots dans `runs/<run_id>/evenements/part-NNNNN.parquet` : un arrêt
brutal ne fait perdre que le lot en cours, et DuckDB lit l'ensemble des fichiers d'un coup.
Chaque ligne garde l'événement brut (JSON du contrat, colonne `brut`) pour un rejeu à l'octet
près, plus les champs de l'enveloppe en colonnes pour les requêtes.

Reconstruire l'historique d'un run depuis Kafka (dans la limite de la rétention de 7 jours) :
    python -m replay.ecrivain --run <run_id>
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from aiokafka import AIOKafkaConsumer, ConsumerRecord

from bus import events as ev
from bus.topics import Topic
from sim.domaine import jour
from sim.run import RUNS_DIR

SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("producteur", pa.string()),
        ("seq", pa.int64()),  # ordre de publication chez le producteur
        ("kafka_ts", pa.int64()),  # horodatage Kafka, en millisecondes
        ("topic", pa.string()),
        ("partition", pa.int32()),
        ("offset", pa.int64()),
        ("event_id", pa.string()),
        ("type", pa.string()),
        ("schema_version", pa.string()),
        ("jour", pa.int32()),
        ("sim_time_occurred", pa.int64()),
        ("sim_time_processed", pa.int64()),
        ("wall_time_emitted", pa.timestamp("us", tz="UTC")),
        ("source_agent_id", pa.string()),
        ("zone", pa.string()),
        ("caused_by", pa.list_(pa.string())),
        ("brut", pa.string()),  # l'événement tel qu'il a circulé : la source du rejeu
    ]
)

LOT_MAX = 5000
FLUSH_S = 10.0


def dossier_evenements(run_id: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / run_id / "evenements"


def _ligne(record: ConsumerRecord, event: ev.Envelope) -> dict:
    headers = {k: v.decode("utf-8") for k, v in (record.headers or ())}
    return {
        "run_id": event.run_id,
        "producteur": headers.get("producteur"),
        "seq": int(headers["seq"]) if "seq" in headers else None,
        "kafka_ts": record.timestamp,
        "topic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
        "event_id": str(event.event_id),
        "type": event.type,
        "schema_version": event.schema_version,
        "jour": jour(event.sim_time_occurred),
        "sim_time_occurred": event.sim_time_occurred,
        "sim_time_processed": event.sim_time_processed,
        "wall_time_emitted": event.wall_time_emitted,
        "source_agent_id": event.source_agent_id,
        "zone": event.zone.value if event.zone else None,
        "caused_by": [str(c) for c in event.caused_by],
        "brut": record.value.decode("utf-8"),
    }


class EcrivainHistorique:
    def __init__(self, bootstrap_servers: str, run_id: str, runs_dir: Path = RUNS_DIR) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.run_id = run_id
        self.dossier = dossier_evenements(run_id, runs_dir)
        self.recus = 0  # événements du run reçus
        self.ecrits = 0
        self.non_conformes = 0
        self._tampon: list[dict] = []
        self._parts = 0
        self._consumer: AIOKafkaConsumer | None = None
        self._attendus: int | None = None
        self._fini = asyncio.Event()  # demande d'arrêt de la lecture
        self._ecrit = asyncio.Event()  # dernier lot écrit, lecteur arrêté

    async def demarrer(self, depuis_debut: bool = False) -> None:
        """Se place en fin de topic (run qui va démarrer) ou au début (reconstruction)."""
        self.dossier.mkdir(parents=True, exist_ok=True)
        self._consumer = AIOKafkaConsumer(
            *(t.value for t in Topic),
            bootstrap_servers=self.bootstrap_servers,
            group_id=None,
            auto_offset_reset="earliest" if depuis_debut else "latest",
            enable_auto_commit=False,
        )
        await self._consumer.start()
        # Résout la position tout de suite : rien de ce qui est publié ensuite n'est manqué.
        async with asyncio.timeout(15):
            while not self._consumer.assignment():
                await asyncio.sleep(0.05)
        for partition in self._consumer.assignment():
            await self._consumer.position(partition)

    async def boucle(self, inactivite_s: float | None = None) -> None:
        """Lit jusqu'à `terminer`, ou jusqu'à `inactivite_s` sans message (reconstruction)."""
        assert self._consumer is not None
        dernier_flush = dernier_message = time.monotonic()
        while not self._fini.is_set():
            lots = await self._consumer.getmany(timeout_ms=200)
            for records in lots.values():
                for record in records:
                    self._recevoir(record)
                    dernier_message = time.monotonic()
            maintenant = time.monotonic()
            if len(self._tampon) >= LOT_MAX or maintenant - dernier_flush >= FLUSH_S:
                self._ecrire()
                dernier_flush = maintenant
            if self._attendus is not None and self.recus >= self._attendus:
                self._fini.set()
            if inactivite_s is not None and maintenant - dernier_message >= inactivite_s:
                self._fini.set()
        self._ecrire()
        await self._consumer.stop()
        self._ecrit.set()

    async def terminer(self, attendus: int, delai_s: float = 30.0) -> int:
        """Fin de run : attend d'avoir reçu les `attendus` événements publiés et que le dernier
        lot soit écrit. Renvoie le nombre d'événements enregistrés."""
        self._attendus = attendus
        if self.recus >= attendus:
            self._fini.set()
        try:
            async with asyncio.timeout(delai_s):
                await self._ecrit.wait()
        except TimeoutError:
            self._fini.set()  # on écrit ce qui a été reçu ; l'écart est signalé par l'appelant
            await self._ecrit.wait()
        return self.ecrits

    def _recevoir(self, record: ConsumerRecord) -> None:
        try:
            event = ev.decode(record.value)
        except ev.EventValidationError:
            self.non_conformes += 1
            return
        if event.run_id != self.run_id:
            return
        self.recus += 1
        self._tampon.append(_ligne(record, event))

    def _ecrire(self) -> None:
        if not self._tampon:
            return
        table = pa.Table.from_pylist(self._tampon, schema=SCHEMA)
        pq.write_table(table, self.dossier / f"part-{self._parts:05d}.parquet")
        self._parts += 1
        self.ecrits += len(self._tampon)
        self._tampon.clear()


async def reconstruire(bootstrap_servers: str, run_id: str, runs_dir: Path = RUNS_DIR) -> int:
    """Relit Kafka depuis le début de la rétention et réécrit l'historique d'un run."""
    dossier = dossier_evenements(run_id, runs_dir)
    for ancien in dossier.glob("part-*.parquet"):
        ancien.unlink()
    ecrivain = EcrivainHistorique(bootstrap_servers, run_id, runs_dir)
    await ecrivain.demarrer(depuis_debut=True)
    await ecrivain.boucle(inactivite_s=3.0)
    return ecrivain.ecrits


def main() -> None:
    from sim.settings import load_settings

    parser = argparse.ArgumentParser(description="Reconstruit l'historique d'un run depuis Kafka.")
    parser.add_argument("--run", required=True, help="run_id à reconstruire")
    args = parser.parse_args()
    n = asyncio.run(reconstruire(load_settings().kafka_bootstrap_servers, args.run))
    print(json.dumps({"run_id": args.run, "evenements": n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
