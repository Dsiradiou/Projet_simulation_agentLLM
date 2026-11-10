"""La ville : sa taille, son plan urbain et la géographie qui en découle (décidé le 10 nov.).

Au lancement, on ne choisit plus le nombre de bâtiments de chaque type (`zones`) : on choisit une
**taille** de ville et un **plan**, et la ville se peuple d'elle-même, comme une ville réelle.

- **Taille** (`petite`, `moyenne`, `grande`, ou `auto` d'après le nombre d'agents) : elle fixe le
  nombre de lieux de chaque type (`LIEUX_PAR_TAILLE`). Elle suit la population : chaque taille
  impose un minimum d'agents (`AGENTS_PAR_TAILLE`), sans quoi des lieux resteraient vides et des
  habitants ne se croiseraient jamais.
- **Plan** (morphologies urbaines réelles) : `radioconcentrique` (un centre et des anneaux),
  `hippodamien` (un damier), `etoile` (des avenues qui rayonnent depuis le centre), `lineaire` (une
  ville étirée le long de la côte). `hasard` le tire avec la graine du run (campagnes).

Le plan agit sur la simulation, pas seulement sur le décor : chaque habitant vit dans un quartier
(un exemplaire de `domicile`), et fréquente, pour chaque type de lieu, l'exemplaire attribué à son
quartier, le plus proche possible. Le centre reçoit les commerces et la place des réseaux sociaux,
la périphérie les quartiers, l'entre-deux les pharmacies, piscines et terrains ; les plages
longent la côte. L'attribution garantit qu'aucun lieu n'est délaissé : chaque exemplaire sert au
moins un quartier. Deux quartiers éloignés partagent donc moins de lieux, et leurs habitants se
croisent moins : la forme de la ville change qui voit qui.

Tout est déterministe : la géométrie ne dépend que de la taille et du plan, le plan tiré au hasard
et la répartition des habitants dans les quartiers ne dépendent que de la graine (générateurs à
part, E1). Les positions et les routes sont publiées dans `monde.plan` ; la scène Godot ne fait que
les afficher.
"""

import itertools
import math
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from sim.domaine import Zone, lieux_du_monde


class Taille(StrEnum):
    PETITE = "petite"
    MOYENNE = "moyenne"
    GRANDE = "grande"
    AUTO = "auto"  # d'après le nombre d'agents


class Plan(StrEnum):
    RADIOCONCENTRIQUE = "radioconcentrique"
    HIPPODAMIEN = "hippodamien"
    ETOILE = "etoile"
    LINEAIRE = "lineaire"
    HASARD = "hasard"  # tiré avec la graine du run


PLANS_REELS = (Plan.RADIOCONCENTRIQUE, Plan.HIPPODAMIEN, Plan.ETOILE, Plan.LINEAIRE)
TAILLES_REELLES = (Taille.PETITE, Taille.MOYENNE, Taille.GRANDE)

# Lieux de chaque type selon la taille. Jamais plus d'exemplaires d'un type que de quartiers :
# chaque exemplaire peut ainsi servir au moins un quartier. Une seule place des réseaux sociaux.
LIEUX_PAR_TAILLE: dict[Taille, dict[Zone, int]] = {
    Taille.PETITE: {
        Zone.DOMICILE: 2, Zone.PLAGE: 1, Zone.PHARMACIE: 1, Zone.PISCINE: 1,
        Zone.TERRAIN_SPORT: 1, Zone.CENTRE_COMMERCIAL: 1, Zone.RESEAUX_SOCIAUX: 1,
    },
    Taille.MOYENNE: {
        Zone.DOMICILE: 3, Zone.PLAGE: 2, Zone.PHARMACIE: 2, Zone.PISCINE: 1,
        Zone.TERRAIN_SPORT: 2, Zone.CENTRE_COMMERCIAL: 1, Zone.RESEAUX_SOCIAUX: 1,
    },
    Taille.GRANDE: {
        Zone.DOMICILE: 4, Zone.PLAGE: 3, Zone.PHARMACIE: 3, Zone.PISCINE: 2,
        Zone.TERRAIN_SPORT: 3, Zone.CENTRE_COMMERCIAL: 2, Zone.RESEAUX_SOCIAUX: 1,
    },
}  # fmt: skip


@dataclass(frozen=True)
class Fourchette:
    """Agents (hors météo) pour une taille : `minimum` imposé, `conseille` au-delà duquel la ville
    devient dense, `auto` : plus grand nombre pour lequel `auto` choisit cette taille."""

    minimum: int
    conseille: int
    auto: int | None


AGENTS_PAR_TAILLE: dict[Taille, Fourchette] = {
    Taille.PETITE: Fourchette(minimum=12, conseille=50, auto=40),
    Taille.MOYENNE: Fourchette(minimum=30, conseille=110, auto=90),
    Taille.GRANDE: Fourchette(minimum=60, conseille=400, auto=None),
}

# Ce que reçoit le centre, puis l'entre-deux ; les quartiers vont à la périphérie.
CENTRE = (Zone.CENTRE_COMMERCIAL, Zone.RESEAUX_SOCIAUX)
ENTRE_DEUX = (Zone.PHARMACIE, Zone.PISCINE, Zone.TERRAIN_SPORT, Zone.CENTRE_COMMERCIAL)

# Géométrie, dans les unités de la scène (environ un mètre) : la côte est à z = 0, la mer au-delà
# (z > 0), la ville dans les terres (z < 0).
PAS = 36.0  # distance entre deux lieux voisins : leurs bâtiments ne se chevauchent pas
Z_PLAGE = 11.2  # centre de l'aire des plages, sur le sable
Z_PREMIERE_RANGEE = -16.0  # lieu des terres le plus proche de la côte
ECART_PLAGES = 60.0  # une plage occupe une soixantaine de mètres de côte


@dataclass(frozen=True)
class PlanVille:
    """Géographie d'une ville : lieux, positions (x, z), routes, et lieux de chaque quartier."""

    taille: Taille
    plan: Plan
    lieux: tuple[str, ...]
    positions: Mapping[str, tuple[float, float]]
    routes: tuple[tuple[float, float, float, float], ...]
    # Quartier (lieu de type domicile) -> pour chaque type, le lieu que fréquentent ses habitants.
    frequentes: Mapping[str, Mapping[Zone, str]]

    @property
    def quartiers(self) -> tuple[str, ...]:
        return tuple(lieu for lieu in self.lieux if lieu.startswith(Zone.DOMICILE.value))


# --- taille et plan effectifs --------------------------------------------------------------------


def taille_pour(agents: int) -> Taille:
    """Taille que choisit `auto` pour ce nombre d'agents."""
    for taille in TAILLES_REELLES:
        borne = AGENTS_PAR_TAILLE[taille].auto
        if borne is None or agents <= borne:
            return taille
    return Taille.GRANDE


def taille_effective(taille: Taille, agents: int) -> Taille:
    return taille_pour(agents) if taille is Taille.AUTO else taille


def plan_effectif(plan: Plan, graine: int) -> Plan:
    """Le plan tiré au hasard ne dépend que de la graine (générateur à part)."""
    if plan is not Plan.HASARD:
        return plan
    return random.Random(f"{graine}:ville:plan").choice(PLANS_REELS)


def nombres_par_type(taille: Taille) -> dict[Zone, int]:
    return dict(LIEUX_PAR_TAILLE[taille])


def lieux_de_la_ville(taille: Taille) -> tuple[str, ...]:
    return lieux_du_monde(nombres_par_type(taille))


# --- géométrie des plans -------------------------------------------------------------------------


Point = tuple[float, float]
Segment = tuple[float, float, float, float]


def _cercle(centre: Point, rayon: float, cotes: int = 32) -> list[Segment]:
    cx, cz = centre
    pts = [
        (
            cx + rayon * math.cos(2 * math.pi * i / cotes),
            cz + rayon * math.sin(2 * math.pi * i / cotes),
        )
        for i in range(cotes + 1)
    ]
    return [(a[0], a[1], b[0], b[1]) for a, b in itertools.pairwise(pts)]


def _sur_anneau(centre: Point, rayon: float, n: int, decalage: float = 0.0) -> list[Point]:
    """n points régulièrement espacés ; le premier du côté de la côte (vers +z), décalé de
    `decalage` pas angulaires."""
    cx, cz = centre
    if n == 0:
        return []
    return [
        (
            cx + rayon * math.sin(2 * math.pi * (i + decalage) / n),
            cz + rayon * math.cos(2 * math.pi * (i + decalage) / n),
        )
        for i in range(n)
    ]


def _au_centre(centre: Point, n: int) -> list[Point]:
    """Lieux du centre, côte à côte."""
    cx, cz = centre
    return [(cx + PAS * (i - (n - 1) / 2), cz) for i in range(n)]


def _radioconcentrique(nc: int, nm: int, nq: int) -> tuple[list[Point], Point, list[Segment]]:
    """Un centre et deux anneaux : l'entre-deux sur le premier, les quartiers sur le second."""
    r1 = max(PAS * 1.2, PAS * nm / (2 * math.pi), PAS * (nc + 1) / 2)
    r2 = max(r1 + PAS, PAS * nq / (2 * math.pi))
    centre = (0.0, Z_PREMIERE_RANGEE - r2)
    points = (
        _au_centre(centre, nc)
        + _sur_anneau(centre, r1, nm, 0.5)
        + _sur_anneau(centre, r2, nq, 0.0 if nq % 2 else 0.5)
    )
    routes = _cercle(centre, r1 / 2 + 6) + _cercle(centre, (r1 + r2) / 2)
    for x, z in _sur_anneau(centre, r2 + PAS / 2, max(nq, 4), 0.25):  # radiales
        routes.append((centre[0], centre[1], x, z))
    routes.append((centre[0], centre[1], centre[0], 0.0))  # avenue vers la mer
    return points, centre, routes


def _hippodamien(nc: int, nm: int, nq: int) -> tuple[list[Point], Point, list[Segment]]:
    """Un damier d'îlots ; les îlots en trop restent des espaces verts."""
    n = nc + nm + nq
    colonnes = math.ceil(math.sqrt(n * 1.6))
    rangees = math.ceil(n / colonnes)
    x0 = -PAS * (colonnes - 1) / 2
    points = [
        (x0 + PAS * c, Z_PREMIERE_RANGEE - PAS * r) for r in range(rangees) for c in range(colonnes)
    ]
    centre = (0.0, Z_PREMIERE_RANGEE - PAS * (rangees - 1) / 2)
    routes: list[Segment] = []
    gauche, droite = x0 - PAS / 2, x0 + PAS * (colonnes - 0.5)
    fond = Z_PREMIERE_RANGEE - PAS * (rangees - 0.5)
    for c in range(colonnes + 1):
        x = gauche + PAS * c
        routes.append((x, 0.0, x, fond))
    for r in range(rangees + 1):
        z = Z_PREMIERE_RANGEE + PAS / 2 - PAS * r
        routes.append((gauche, z, droite, z))
    return points, centre, routes


def _etoile(nc: int, nm: int, nq: int) -> tuple[list[Point], Point, list[Segment]]:
    """Des avenues qui rayonnent depuis une place proche du front de mer, en éventail vers les
    terres (une ville côtière en étoile) : un quartier au bout de chaque avenue, l'entre-deux le
    long des avenues, et une avenue qui descend à la mer."""
    branches = max(3, nq, min(nm, 5))  # au-delà de 5, les lieux d'un même niveau se touchent
    niveaux = max(1, math.ceil(nm / branches))
    bout = PAS * (niveaux + 1.5)
    centre = (0.0, Z_PREMIERE_RANGEE - PAS * 0.6)
    # Angles comptés depuis la direction de la mer (+z) : de 100° à 260°, vers l'intérieur.
    angles = [math.radians(100 + 160 * i / (branches - 1)) for i in range(branches)]
    direction = [(math.sin(a), math.cos(a)) for a in angles]
    milieu = [
        (centre[0] + dx * PAS * (k + 0.5), centre[1] + dz * PAS * (k + 0.5))
        for k in range(1, niveaux + 1)
        for dx, dz in direction
    ][:nm]
    # Quartiers au bout des avenues, répartis sur l'éventail.
    bouts = [(centre[0] + dx * bout, centre[1] + dz * bout) for dx, dz in direction]
    routes = [
        (centre[0], centre[1], centre[0] + dx * (bout + PAS / 2), centre[1] + dz * (bout + PAS / 2))
        for dx, dz in direction
    ]
    routes.append((centre[0], centre[1], centre[0], 0.0))
    return _au_centre(centre, nc) + milieu + bouts, centre, routes


def _lineaire(nc: int, nm: int, nq: int) -> tuple[list[Point], Point, list[Segment]]:
    """Une ville étirée le long de la côte, de part et d'autre d'une avenue parallèle au rivage."""
    n = nc + nm + nq
    rangees = 1 if n <= 8 else 2
    colonnes = math.ceil(n / rangees)
    x0 = -PAS * (colonnes - 1) / 2
    points = [
        (x0 + PAS * c, Z_PREMIERE_RANGEE - PAS * r) for r in range(rangees) for c in range(colonnes)
    ]
    centre = (0.0, Z_PREMIERE_RANGEE - PAS * (rangees - 1) / 2)
    avenue = Z_PREMIERE_RANGEE - PAS / 2 if rangees == 2 else Z_PREMIERE_RANGEE + PAS / 2 - 4
    routes = [(x0 - PAS / 2, avenue, x0 + PAS * (colonnes - 0.5), avenue)]
    return points, centre, routes


GEOMETRIES = {
    Plan.RADIOCONCENTRIQUE: _radioconcentrique,
    Plan.HIPPODAMIEN: _hippodamien,
    Plan.ETOILE: _etoile,
    Plan.LINEAIRE: _lineaire,
}


# --- attribution des emplacements ----------------------------------------------------------------


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _angle(p: Point, centre: Point) -> float:
    return math.atan2(p[0] - centre[0], p[1] - centre[1]) % (2 * math.pi)


def _eloignes(candidats: list[Point], n: int, centre: Point) -> list[Point]:
    """n candidats loin du centre et loin les uns des autres (échantillonnage du point le plus
    éloigné), pour des quartiers répartis sur toute la périphérie."""
    restants = sorted(candidats, key=lambda p: (-_distance(p, centre), _angle(p, centre)))
    choisis = [restants.pop(0)] if restants and n else []
    while len(choisis) < n and restants:
        meilleur = max(
            restants,
            key=lambda p: (min(_distance(p, c) for c in choisis), _distance(p, centre)),
        )
        restants.remove(meilleur)
        choisis.append(meilleur)
    return choisis


def _entrelacer(types: Iterable[tuple[Zone, int]]) -> list[Zone]:
    """pharmacie, piscine, terrain, pharmacie, terrain… : les exemplaires d'un même type se
    suivent le moins possible, donc se répartissent autour du centre."""
    files = [[z] * n for z, n in types if n > 0]
    ordre: list[Zone] = []
    while any(files):
        for f in files:
            if f:
                ordre.append(f.pop())
    return ordre


def _nom(zone: Zone, k: int, n: int) -> str:
    return zone.value if n == 1 else f"{zone.value}_{k}"


def _placer(taille: Taille, plan: Plan) -> tuple[dict[str, Point], list[Segment], Point]:
    nombres = nombres_par_type(taille)
    au_centre = [Zone.CENTRE_COMMERCIAL, Zone.RESEAUX_SOCIAUX]
    reste = dict(nombres)
    for z in au_centre:
        reste[z] -= 1
    entre_deux = _entrelacer((z, reste[z]) for z in ENTRE_DEUX)
    nq = nombres[Zone.DOMICILE]
    points, centre, routes = GEOMETRIES[plan](len(au_centre), len(entre_deux), nq)

    libres = list(points)
    centraux = sorted(libres, key=lambda p: (_distance(p, centre), _angle(p, centre)))[:2]
    for p in centraux:
        libres.remove(p)
    peripherie = _eloignes(libres, nq, centre)
    for p in peripherie:
        libres.remove(p)
    milieu = sorted(libres, key=lambda p: (_distance(p, centre), _angle(p, centre)))
    milieu = sorted(milieu[: len(entre_deux)], key=lambda p: _angle(p, centre))

    compteurs = dict.fromkeys(Zone, 0)
    positions: dict[str, Point] = {}

    def poser(zone: Zone, p: Point) -> None:
        compteurs[zone] += 1
        positions[_nom(zone, compteurs[zone], nombres[zone])] = p

    for zone, p in zip(sorted(au_centre, key=lambda z: z.value), centraux, strict=True):
        poser(zone, p)
    for zone, p in zip(entre_deux, milieu, strict=True):
        poser(zone, p)
    for p in sorted(peripherie, key=lambda p: _angle(p, centre)):
        poser(Zone.DOMICILE, p)

    # Les plages, le long de la côte, en face de la ville.
    terres = [p for lieu, p in positions.items()]
    gauche, droite = min(x for x, _ in terres), max(x for x, _ in terres)
    n_plages = nombres[Zone.PLAGE]
    if plan is Plan.LINEAIRE and n_plages > 1:
        pas = max(ECART_PLAGES, (droite - gauche) / (n_plages - 1))
    else:
        pas = ECART_PLAGES
    for k in range(n_plages):
        poser(Zone.PLAGE, ((gauche + droite) / 2 + pas * (k - (n_plages - 1) / 2), Z_PLAGE))
    return positions, routes, centre


def _attribuer(
    positions: Mapping[str, Point], nombres: Mapping[Zone, int]
) -> dict[str, dict[Zone, str]]:
    """Pour chaque type, le lieu de chaque quartier : la répartition la plus courte en distance
    totale où chaque exemplaire sert au moins un quartier (au plus 4 quartiers : on les essaie
    toutes). Égalité : l'ordre des noms départage."""
    quartiers = [lieu for lieu in positions if lieu.startswith(Zone.DOMICILE.value)]
    quartiers.sort()
    resultat: dict[str, dict[Zone, str]] = {q: {Zone.DOMICILE: q} for q in quartiers}
    for zone in Zone:
        if zone is Zone.DOMICILE:
            continue
        exemplaires = sorted(lieu for lieu in positions if _type(lieu) is zone)
        meilleure: tuple[float, tuple[str, ...]] | None = None
        for choix in itertools.product(exemplaires, repeat=len(quartiers)):
            if len(set(choix)) < min(len(exemplaires), len(quartiers)):
                continue
            paires = zip(quartiers, choix, strict=True)
            cout = sum(_distance(positions[q], positions[e]) for q, e in paires)
            cle = (round(cout, 6), choix)
            if meilleure is None or cle < meilleure:
                meilleure = cle
        assert meilleure is not None
        for q, e in zip(quartiers, meilleure[1], strict=True):
            resultat[q][zone] = e
    return resultat


def _type(lieu: str) -> Zone:
    from sim.domaine import type_de_lieu

    return type_de_lieu(lieu)


@lru_cache(maxsize=64)
def construire_plan(taille: Taille, plan: Plan) -> PlanVille:
    """Géographie d'une ville d'une taille et d'un plan réels (ni `auto`, ni `hasard`)."""
    if taille not in TAILLES_REELLES or plan not in PLANS_REELS:
        raise ValueError("taille et plan doivent être résolus (ni auto, ni hasard)")
    positions, routes, _ = _placer(taille, plan)
    lieux = lieux_de_la_ville(taille)
    arrondies = {
        lieu: (round(positions[lieu][0], 1), round(positions[lieu][1], 1)) for lieu in lieux
    }
    return PlanVille(
        taille=taille,
        plan=plan,
        lieux=lieux,
        positions=arrondies,
        routes=tuple(tuple(round(v, 1) for v in r) for r in routes),  # type: ignore[misc]
        frequentes=_attribuer(arrondies, nombres_par_type(taille)),
    )


# --- habitants -----------------------------------------------------------------------------------


def quartiers_des_agents(
    roles: Mapping[str, str], quartiers: tuple[str, ...], graine: int
) -> dict[str, str]:
    """Quartier de chaque agent. Rôle par rôle, les agents sont mélangés (générateur à part) puis
    distribués à tour de rôle, en reprenant là où le rôle précédent s'est arrêté : les quartiers ont
    le même nombre d'habitants à un près, et chacun reçoit un peu de chaque rôle."""
    rng = random.Random(f"{graine}:ville:quartiers")
    par_role: dict[str, list[str]] = {}
    for agent_id in sorted(roles):
        par_role.setdefault(roles[agent_id], []).append(agent_id)
    resultat: dict[str, str] = {}
    k = 0
    for role in sorted(par_role):
        agents = par_role[role]
        rng.shuffle(agents)
        for agent_id in agents:
            resultat[agent_id] = quartiers[k % len(quartiers)]
            k += 1
    return resultat
