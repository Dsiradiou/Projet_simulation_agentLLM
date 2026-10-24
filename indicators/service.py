"""Service des indicateurs (étape 14, B2) : un consommateur Kafka qui calcule et publie, à la fin
de chaque jour simulé, les indicateurs d'émergence sur `sim.indicators`.

Il ne touche pas à la simulation : il lit ce que l'orchestrateur publie, comme l'écrivain
d'historique et le pont. Les événements arrivent sur plusieurs topics, donc dans un ordre qui
peut différer de leur publication : le service les remet dans l'ordre exact de l'orchestrateur
(en-tête `seq`) avant de les appliquer. À `horloge.fin_jour`, tous les faits du jour ont été
appliqués : il publie le sens partagé (global, par rôle, par zone), l'adoption, l'information
mutuelle rôle × sens et sa p-valeur, et le taux de dérive des décisions LLM du jour.

Chaque valeur cite dans `caused_by` les décisions qu'elle compte (F6). Les indicateurs sont
enregistrés dans l'historique comme tout événement : le rejeu les restitue sans les recalculer.
`sim.run` démarre le service avant le premier événement du run et attend, en fin de run, qu'il
ait publié les indicateurs du dernier jour.
"""

import asyncio
import logging

from aiokafka import AIOKafkaConsumer, ConsumerRecord

from bus import events as ev
from bus.producer import EventProducer
from bus.topics import Topic
from indicators.calcul import DERIVES, EtatPopulation, Mesure
from indicators.derives import Detecteur

log = logging.getLogger(__name__)
PRODUCTEUR = "indicateurs"
# Tous les topics où publie l'orchestrateur : sa numérotation doit arriver sans trou.
SUIVIS = tuple(t for t in Topic if t is not Topic.INDICATORS)


def mesure_des_derives(examinees: int, signalements: list, fin: int) -> Mesure | None:
    if not examinees:
        return None
    par_nature: dict[str, int] = {}
    for s in signalements:
        for d in s.derives:
            par_nature[d.value] = par_nature.get(d.value, 0) + 1
    return Mesure(
        DERIVES,
        len(signalements) / examinees,
        None,
        tuple(s.decision_id for s in signalements),
        fin - fin % (24 * 60),
        fin,
        examinees,
        dict(sorted(par_nature.items())),
    )


class CalculateurDuRun:
    """Le calcul lui-même, sans Kafka : appliqué aux événements dans l'ordre de publication,
    il renvoie les indicateurs à publier à chaque fin de jour. Sert au service et aux tests."""

    def __init__(self, run_id: str, graine: int) -> None:
        self.run_id = run_id
        self.graine = graine
        self.etat = EtatPopulation()
        self.detecteur = Detecteur()

    def appliquer(self, event: ev.Envelope) -> list[ev.Indicator]:
        self.etat.appliquer(event)
        self.detecteur.appliquer(event)
        if not isinstance(event, ev.ClockEnd):
            return []
        jour, fin = event.payload.jour, event.sim_time_occurred
        mesures = self.etat.mesurer(jour, self.graine)
        derives = mesure_des_derives(*self.detecteur.fin_du_jour(jour), fin)
        if derives is not None:
            mesures.append(derives)
        return [
            ev.Indicator(
                run_id=self.run_id,
                sim_time_occurred=fin,
                sim_time_processed=fin,
                source_agent_id=PRODUCTEUR,
                zone=m.groupe.removeprefix("zone:")
                if m.groupe and m.groupe.startswith("zone:")
                else None,
                caused_by=m.caused_by,
                payload=m.payload(),
            )  # fmt: skip
            for m in mesures
        ]


class ServiceIndicateurs:
    def __init__(self, bootstrap_servers: str, run_id: str, graine: int) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.calcul = CalculateurDuRun(run_id, graine)
        self.run_id = run_id
        self.publies = 0
        self.dernier_jour = 0
        self._prochain = 0  # prochain numéro de publication attendu de l'orchestrateur
        self._en_attente: dict[int, ev.Envelope] = {}
        self._consumer: AIOKafkaConsumer | None = None
        self._producer = EventProducer(bootstrap_servers, producteur=PRODUCTEUR)
        self._fini = asyncio.Event()
        self._arrete = asyncio.Event()
        self._jours_attendus: int | None = None

    async def demarrer(self) -> None:
        """Se place en fin de topic avant le premier événement du run."""
        await self._producer.start()
        self._consumer = AIOKafkaConsumer(
            *(t.value for t in SUIVIS),
            bootstrap_servers=self.bootstrap_servers,
            group_id=None,
            auto_offset_reset="latest",
            enable_auto_commit=False,
        )
        await self._consumer.start()
        async with asyncio.timeout(15):
            while not self._consumer.assignment():
                await asyncio.sleep(0.05)
        for partition in self._consumer.assignment():
            await self._consumer.position(partition)

    async def boucle(self) -> None:
        assert self._consumer is not None
        try:
            while not self._fini.is_set():
                lots = await self._consumer.getmany(timeout_ms=200)
                for records in lots.values():
                    for record in records:
                        await self._recevoir(record)
                if self._jours_attendus is not None and self.dernier_jour >= self._jours_attendus:
                    self._fini.set()
        finally:
            await self._consumer.stop()
            await self._producer.stop()
            self._arrete.set()

    async def _recevoir(self, record: ConsumerRecord) -> None:
        headers = {k: v.decode("utf-8") for k, v in (record.headers or ())}
        if headers.get("producteur") != "orchestrateur" or "seq" not in headers:
            return
        try:
            event: ev.Envelope | None = ev.decode(record.value)
        except ev.EventValidationError:
            event = None  # non conforme : sa place dans l'ordre est gardée, sans effet
        if event is not None and event.run_id != self.run_id:
            return
        self._en_attente[int(headers["seq"])] = event
        while self._prochain in self._en_attente:  # ordre exact de publication
            event = self._en_attente.pop(self._prochain)
            self._prochain += 1
            if event is None:
                continue
            for indicateur in self.calcul.appliquer(event):
                await self._producer.publish(indicateur)
                self.publies += 1
            if isinstance(event, ev.ClockEnd):
                self.dernier_jour = event.payload.jour

    async def terminer(self, jours: int, delai_s: float = 30.0) -> int:
        """Fin de run : attend les indicateurs du jour `jours`, puis s'arrête. Renvoie le nombre
        d'indicateurs publiés (attendus par l'écrivain d'historique)."""
        self._jours_attendus = jours
        if self.dernier_jour >= jours:
            self._fini.set()
        try:
            async with asyncio.timeout(delai_s):
                await self._arrete.wait()
        except TimeoutError:
            log.warning(
                "indicateurs : jour %d non atteint (dernier : %d)", jours, self.dernier_jour
            )
            self._fini.set()
            await self._arrete.wait()
        return self.publies
