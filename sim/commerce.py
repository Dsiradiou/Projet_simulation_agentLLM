"""Commerce à règles (étape 13) : stocks de Vaelys par point de vente, achats, ruptures, réassorts.

Un agent qui devient porteur, ou qui rachète quand son flacon est fini, achète un flacon : là où
il se trouve si c'est un point de vente qui en a, sinon au point de vente le mieux fourni. En
rupture partout, l'achat échoue : sa décision est publiée sans qu'il devienne porteur, et il le
saura le lendemain. Quand un point de vente tombe à zéro, une perturbation « rupture de stock »
est publiée dans sa zone. Les vendeurs réassortissent chaque semaine : ils commandent ce qu'ils
ont vendu dans la semaine, avec une marge, sans descendre sous un minimum. L'offre suit donc la
demande, et une rupture signale une demande qui s'emballe.

Une rupture injectée par l'interface vide réellement les rayons (constat de l'étape 9) ; une
promotion de la marque concurrente dure une semaine et relève le seuil des fidèles.
"""

from uuid import UUID

from bus.events import (
    Disturbance,
    DisturbancePayload,
    Envelope,
    Perturbation,
    Stock,
    StockPayload,
)
from sim.config import CommerceConfig
from sim.domaine import Produit, Zone

DUREE_PROMOTION_JOURS = 7


class Commerce:
    def __init__(
        self, config: CommerceConfig, population: int, vendeurs: dict[Zone, str] | None = None
    ) -> None:
        self.config = config
        echelle = population / 100
        self.reassort_minimum = max(1, round(config.reassort_pour_100 * echelle))
        self.ventes_semaine: dict[Zone, int] = {zone: 0 for zone in config.points_de_vente}
        self.stock: dict[Zone, int] = {
            zone: round(config.stock_initial_pour_100 * echelle) for zone in config.points_de_vente
        }
        self.vendeurs = vendeurs or {}
        self.achats = 0
        self.achats_manques = 0
        self.ruptures = 0
        self.reassorts = 0
        self.rates_veille: set[str] = set()  # ont raté leur achat hier (perception du jour)
        self._rates: set[str] = set()
        self._modifies: set[Zone] = set()
        self._en_rupture: set[Zone] = {z for z, q in self.stock.items() if q == 0}
        self._promotion_jusqu_a = 0

    # --- ce que voient les agents ----------------------------------------------------------------

    def promotion_concurrente(self, jour: int) -> bool:
        return jour <= self._promotion_jusqu_a

    # --- effets des perturbations --------------------------------------------------------------

    def reagir(self, perturbation: Disturbance, jour: int) -> None:
        nature = perturbation.payload.nature
        if nature is Perturbation.RUPTURE_STOCK:
            zones = [perturbation.zone] if perturbation.zone in self.stock else list(self.stock)
            for zone in zones:
                self.stock[zone] = 0
                self._modifies.add(zone)
                self._en_rupture.add(zone)  # déjà annoncée par la perturbation elle-même
        elif nature is Perturbation.PROMOTION_CONCURRENTE:
            self._promotion_jusqu_a = jour + DUREE_PROMOTION_JOURS - 1

    # --- déroulé du jour -----------------------------------------------------------------------

    def debut_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]:
        """Réassort hebdomadaire par les vendeurs, publié en début de journée."""
        self.rates_veille, self._rates = self._rates, set()
        self._modifies.clear()
        if jour == 1 or jour % 7 != self.config.jour_reassort:
            return []
        events: list[Envelope] = []
        for zone in self.stock:
            commande = round(self.ventes_semaine[zone] * self.config.marge_commande)
            self.stock[zone] += max(self.reassort_minimum, commande)
            self.ventes_semaine[zone] = 0
            self._en_rupture.discard(zone)
            self.reassorts += 1
            events.append(self._evenement_stock(zone, minute, run_id))
        return events

    def acheter(self, agent_id: str, zone: Zone) -> Zone | None:
        """Un flacon pour cet agent : renvoie le point de vente, ou None en rupture."""
        if self.stock.get(zone, 0) > 0:
            lieu = zone
        else:
            fournis = [z for z, q in self.stock.items() if q > 0]
            if not fournis:
                self.achats_manques += 1
                self._rates.add(agent_id)
                return None
            lieu = max(fournis, key=lambda z: (self.stock[z], z.value))
        self.stock[lieu] -= 1
        self.ventes_semaine[lieu] += 1
        self._modifies.add(lieu)
        self.achats += 1
        return lieu

    def fin_du_jour(self, minute: int, run_id: str) -> list[Envelope]:
        """Niveaux de stock modifiés aujourd'hui, et ruptures nouvelles (une perturbation dans la
        zone, perçue le lendemain par les agents qui s'y trouvent)."""
        events: list[Envelope] = []
        for zone in sorted(self._modifies, key=lambda z: z.value):
            stock = self._evenement_stock(zone, minute, run_id)
            events.append(stock)
            if self.stock[zone] == 0 and zone not in self._en_rupture:
                self._en_rupture.add(zone)
                self.ruptures += 1
                events.append(self._rupture(zone, minute, run_id, stock.event_id))
        self._modifies.clear()
        return events

    # --- événements ----------------------------------------------------------------------------

    def _evenement_stock(self, zone: Zone, minute: int, run_id: str) -> Stock:
        return Stock(
            run_id=run_id,
            sim_time_occurred=minute,
            sim_time_processed=minute,
            source_agent_id=self.vendeurs.get(zone, "commerce"),
            zone=zone,
            payload=StockPayload(produit=Produit.MARQUE, quantite=self.stock[zone]),
        )

    def _rupture(self, zone: Zone, minute: int, run_id: str, cause: UUID) -> Disturbance:
        lieu = "à la pharmacie" if zone is Zone.PHARMACIE else "au centre commercial"
        return Disturbance(
            run_id=run_id,
            sim_time_occurred=minute,
            sim_time_processed=minute,
            source_agent_id="commerce",
            zone=zone,
            caused_by=(cause,),
            payload=DisturbancePayload(
                nature=Perturbation.RUPTURE_STOCK,
                description=f"Plus aucun flacon de la crème en rayon {lieu}, jusqu'au réassort.",
            ),
        )
