"""Indicateurs d'émergence (étape 14, B1 à B3, F6) : définitions et calcul, sans Kafka.

Le sens partagé (B1) se lit dans le dernier sens déclaré par chaque habitant, catégorie fermée.
Deux mesures complémentaires, décidées par le binôme le 20 oct. :

- la **convergence** `C = 1 − H / log K` (H : entropie de la répartition des sens, K = 4 sens).
  Elle vaut 1 quand tout un groupe donne le même sens, 0 quand les quatre sens sont également
  représentés. Calculée sur toute la population, par rôle et par zone (jauges de la scène).
- l'**information mutuelle** entre le rôle et le sens, normalisée par l'entropie des sens :
  `I(rôle ; sens) / H(sens)`, la part de la diversité des sens qu'explique le rôle. Elle vaut 1
  quand chaque rôle a son propre sens (des sous-cultures), 0 quand tous les rôles se répartissent
  de la même façon (un sens commun aux groupes). Un test de permutation donne sa p-valeur : la
  probabilité d'un écart aussi fort si les sens étaient distribués au hasard entre les rôles.

Pourquoi deux mesures : la convergence seule confond un consensus avec un défaut commun (au
jour 1 de la baseline, tous les agents à règles partent sur « banal » : C = 0,75 sans aucun
échange) ; l'information mutuelle dit si les groupes pensent encore différemment, et révèle quand
le sens ne fait que refléter le rôle, donc le modèle (constat de l'étape 14, question Q2).

Oubli (étape 16, décidé par le binôme le 4 nov.) : un habitant qui a oublié le produit (sa
notoriété est passée sous le seuil) ne compte plus dans le sens partagé ni dans l'information
mutuelle ; il garde sa place dans l'adoption (un porteur n'oublie pas). L'indicateur `oubli`
publie à part la part des habitants qui l'ont oublié, en citant leurs événements d'oubli.

Écart au jour 1 (X1, décidé par le binôme le 4 nov.) : la divergence de Jensen-Shannon (en bits,
entre 0 et 1) entre la répartition des sens du jour et celle du premier jour où des avis existent.
La convergence seule ne distingue pas un consensus construit d'un défaut resté en place (la
baseline, où tous passent à « tendance », contre son témoin, où tous restent « banal ») ; l'écart
au jour 1 dit si le sens a bougé depuis le départ. Il cite les décisions du jour et celles du jour
de référence.

Rôles LLM (X10, décidé le 4 nov.) : le sens partagé, l'information mutuelle et l'écart au jour 1
sont aussi publiés pour le groupe `roles_llm`, les seuls rôles tenus par un modèle en C1 (et par la
baseline C0 en C0). Le « tendance » des fidèles et des vendeurs vient de leur règle : sans ce
groupe, toute comparaison de sens entre C0 et C1 en dépend.

Chaque valeur cite les décisions qu'elle compte (`caused_by`, F6). Le même code sert au service
en direct (`indicators.service`) et au calcul après coup depuis l'historique (`indicators.rapport`).
"""

import math
import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from agents.roles import Role, palier_du_role
from bus import events as ev
from sim.domaine import Sens, debut_du_jour

K = len(Sens)
TIRAGES_PERMUTATION = 200
# Émetteurs de la marque et du concurrent : ils parlent au public, ils n'en font pas partie.
HORS_PUBLIC = frozenset({Role.RESPONSABLE_MARQUE.value, Role.MARQUE_CONCURRENTE.value})
# Noms des indicateurs publiés sur sim.indicators.
SENS_PARTAGE = "sens_partage"
ADOPTION = "adoption"
INFORMATION_MUTUELLE = "information_mutuelle"
INFORMATION_MUTUELLE_P = "information_mutuelle_p"
DERIVES = "derives"
OUBLI = "oubli"
ECART_JOUR_1 = "ecart_jour_1"
# Groupe des rôles tenus par un LLM en C1 (X10) : mêmes agents en C0, où une règle les tient.
ROLES_LLM = "roles_llm"


def role_de(agent_id: str) -> str:
    """Rôle d'un agent d'après son identifiant (`pharmacien_02` → `pharmacien`)."""
    return agent_id.rsplit("_", 1)[0]


def entropie(comptes: Iterable[int]) -> float:
    valeurs = [c for c in comptes if c > 0]
    n = sum(valeurs)
    return -sum(c / n * math.log(c / n) for c in valeurs) if n else 0.0


def convergence(comptes: Iterable[int]) -> float:
    """1 − H / log K : 1 si un seul sens, 0 si les K sens sont à égalité."""
    return 1.0 - entropie(comptes) / math.log(K)


def jensen_shannon(p: dict[str, int], q: dict[str, int]) -> float:
    """Divergence de Jensen-Shannon entre deux répartitions (comptes), en bits : 0 pour deux
    répartitions identiques, 1 pour deux répartitions sans aucun sens en commun."""
    n_p, n_q = sum(p.values()), sum(q.values())
    if not n_p or not n_q:
        return 0.0
    total = 0.0
    for s in set(p) | set(q):
        a, b = p.get(s, 0) / n_p, q.get(s, 0) / n_q
        m = (a + b) / 2
        total += sum(x * math.log2(x / m) for x in (a, b) if x > 0) / 2
    return min(1.0, max(0.0, total))


def du_role_llm(role: str) -> bool:
    try:
        return palier_du_role(Role(role)) is not None
    except ValueError:
        return False


def information_mutuelle(paires: list[tuple[str, str]]) -> tuple[float, float]:
    """I(groupe ; sens) en nats, et sa version normalisée I / H(sens) (0 si un seul sens)."""
    n = len(paires)
    if n == 0:
        return 0.0, 0.0
    h_sens = entropie(Counter(s for _, s in paires).values())
    par_groupe: dict[str, Counter[str]] = {}
    for groupe, sens in paires:
        par_groupe.setdefault(groupe, Counter())[sens] += 1
    h_conditionnelle = sum(sum(c.values()) / n * entropie(c.values()) for c in par_groupe.values())
    mi = max(0.0, h_sens - h_conditionnelle)
    return mi, (mi / h_sens if h_sens > 0 else 0.0)


def p_permutation(
    paires: list[tuple[str, str]], rng: random.Random, tirages: int = TIRAGES_PERMUTATION
) -> float:
    """P-valeur de l'information mutuelle observée contre des sens redistribués au hasard
    entre les groupes (mêmes effectifs, même répartition globale)."""
    observee, _ = information_mutuelle(paires)
    groupes = [g for g, _ in paires]
    sens = [s for _, s in paires]
    au_moins = 0
    for _ in range(tirages):
        rng.shuffle(sens)
        if information_mutuelle(list(zip(groupes, sens, strict=True)))[0] >= observee - 1e-12:
            au_moins += 1
    return (1 + au_moins) / (1 + tirages)


def rng_du_jour(graine: int, jour: int, groupe: str | None = None) -> random.Random:
    """Tirages du test de permutation : reproductibles pour un run, un jour et un groupe. Le
    global garde les tirages d'avant l'étape 16 (mêmes p-valeurs pour les anciens runs)."""
    return random.Random(f"indicateurs:{graine}:{jour}" + (f":{groupe}" if groupe else ""))


@dataclass
class Avis:
    role: str
    sens: Sens
    porteur: bool
    decision_id: UUID
    minute: int


@dataclass
class Mesure:
    """Une valeur d'indicateur, avant d'être publiée en `indicateur.valeur`."""

    nom: str
    valeur: float
    groupe: str | None
    caused_by: tuple[UUID, ...]
    fenetre_debut: int
    fenetre_fin: int
    effectif: int
    repartition: dict[str, int] | None = None

    def payload(self) -> ev.IndicatorPayload:
        return ev.IndicatorPayload(
            nom=self.nom,
            valeur=round(self.valeur, 6),
            groupe=self.groupe,
            fenetre_debut=self.fenetre_debut,
            fenetre_fin=self.fenetre_fin,
            effectif=self.effectif,
            repartition=self.repartition,
        )


def _repartition(avis: Iterable[Avis]) -> dict[str, int]:
    comptes = Counter(a.sens.value for a in avis)
    return {s.value: comptes[s.value] for s in Sens if comptes[s.value]}


@dataclass
class EtatPopulation:
    """Dernier avis de chaque habitant et zone où il se trouve, tenus à partir des événements."""

    avis: dict[str, Avis] = field(default_factory=dict)
    zones: dict[str, str] = field(default_factory=dict)  # lieu de chacun
    # Étape 16 : habitants qui ont oublié le produit, avec leur événement d'oubli et son heure.
    oublis: dict[str, tuple[UUID, int]] = field(default_factory=dict)
    notoriete_suivie: bool = False  # le run publie la notoriété (sinon : pas d'indicateur oubli)
    # X1 : répartition des sens au premier jour où des avis existent, par groupe (None : global),
    # avec les décisions qui la composent.
    references: dict[str | None, tuple[dict[str, int], tuple[UUID, ...]]] = field(
        default_factory=dict
    )

    def appliquer(self, event: ev.Envelope) -> None:
        match event:
            case ev.AgentDecision():
                agent_id = event.source_agent_id
                if event.zone is not None:
                    self.zones[agent_id] = event.zone
                if role_de(agent_id) in HORS_PUBLIC:
                    return
                self.avis[agent_id] = Avis(
                    role_de(agent_id),
                    event.payload.sens,
                    event.payload.porteur,
                    event.event_id,
                    event.sim_time_occurred,
                )
            case ev.AgentMove():
                self.zones[event.source_agent_id] = event.payload.vers
            case ev.AgentNotoriete():
                self.notoriete_suivie = True
                agent_id = event.source_agent_id
                if event.payload.nature is ev.Notoriete.OUBLI:
                    self.oublis[agent_id] = (event.event_id, event.sim_time_occurred)
                else:
                    self.oublis.pop(agent_id, None)

    def mesurer(self, jour: int, graine: int) -> list[Mesure]:
        """Indicateurs de fin de jour : sens partagé (global, par rôle, par zone), adoption et
        information mutuelle rôle × sens avec sa p-valeur. Groupes et causes triés : deux calculs
        sur les mêmes événements donnent exactement les mêmes valeurs (rejeu, E3)."""
        if not self.avis:
            return []
        fin = debut_du_jour(jour + 1) - 1
        tous = [self.avis[a] for a in sorted(self.avis)]
        agents = sorted(a for a in self.avis if a not in self.oublis)  # ceux qui y pensent
        mesures_oubli = self._mesurer_oubli(jour, fin, len(tous))
        if not agents:
            return mesures_oubli
        actifs = [self.avis[a] for a in agents]

        def mesure(nom: str, valeur: float, groupe: str | None, avis: list[Avis], **kw) -> Mesure:
            return Mesure(
                nom, valeur, groupe, tuple(a.decision_id for a in avis),
                min(a.minute for a in avis), fin, len(avis), **kw,
            )  # fmt: skip

        mesures = [mesure(ADOPTION, sum(a.porteur for a in tous) / len(tous), None, tous)]
        llm = [a for a in actifs if du_role_llm(a.role)]
        for groupe, avis in ((None, actifs), (ROLES_LLM, llm)):
            if not avis:
                continue
            repartition = _repartition(avis)
            mesures.append(
                mesure(SENS_PARTAGE, convergence(repartition.values()), groupe, avis,
                       repartition=repartition)
            )  # fmt: skip
            paires = [(a.role, a.sens.value) for a in avis]
            _, normalisee = information_mutuelle(paires)
            mesures.append(mesure(INFORMATION_MUTUELLE, normalisee, groupe, avis))
            p = p_permutation(paires, rng_du_jour(graine, jour, groupe))
            mesures.append(mesure(INFORMATION_MUTUELLE_P, p, groupe, avis))
            reference, ids = self.references.setdefault(
                groupe, (repartition, tuple(a.decision_id for a in avis))
            )
            ecart = mesure(ECART_JOUR_1, jensen_shannon(repartition, reference), groupe, avis,
                           repartition=repartition)  # fmt: skip
            ecart.caused_by = tuple(dict.fromkeys(ecart.caused_by + ids))
            mesures.append(ecart)
        groupes: dict[str, list[Avis]] = {}
        for agent_id in agents:
            groupes.setdefault(f"role:{self.avis[agent_id].role}", []).append(self.avis[agent_id])
        for agent_id in agents:
            if agent_id in self.zones:
                groupes.setdefault(f"zone:{self.zones[agent_id]}", []).append(self.avis[agent_id])
        for groupe in sorted(groupes):
            avis = groupes[groupe]
            rep = _repartition(avis)
            mesures.append(
                mesure(SENS_PARTAGE, convergence(rep.values()), groupe, avis, repartition=rep)
            )
        return mesures + mesures_oubli

    def _mesurer_oubli(self, jour: int, fin: int, habitants: int) -> list[Mesure]:
        """Part des habitants (ceux qui ont déjà donné un avis) qui ont oublié le produit ; elle
        cite les événements d'oubli. Rien pour un run sans notoriété (runs d'avant l'étape 16)."""
        if not self.notoriete_suivie or not habitants:
            return []
        oublis = [self.oublis[a] for a in sorted(self.oublis) if a in self.avis]  # par agent
        debut = min((minute for _, minute in oublis), default=debut_du_jour(jour))
        return [
            Mesure(OUBLI, len(oublis) / habitants, None, tuple(i for i, _ in oublis), debut, fin,
                   habitants)
        ]  # fmt: skip
