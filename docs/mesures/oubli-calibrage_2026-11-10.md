# Calibrage de l'oubli et de la découverte, durée des flacons (10 nov.)

Choix M1 du binôme (4 nov.) : garder les valeurs de départ, puis les calibrer pendant l'étape 16.
Choix X4 : la durée d'un flacon dépend de l'usage. Constats de départ :
`docs/mesures/memoire-c0_2026-11-04.md`.

## Méthode

- Baseline C0 (`configs/baseline-dev.yaml`, 60 agents à règles), durée portée à **90 jours**,
  graines 42, 7, 1, 2, 3. Oubli et lassitude actifs, comme dans `default.yaml`.
- Runs en mémoire, sans Kafka : même orchestrateur, mêmes routines, même commerce que `sim.run`.
  Les événements `agent.notoriete` donnent les découvertes et les oublis ; les porteurs et les
  agents qui connaissent le produit sont comptés chaque soir.
- Moyenne sur les 5 graines, minimum et maximum entre parenthèses. 59 agents suivis : la marque
  concurrente n'a pas de notoriété.

## Ce qui n'allait pas

1. **Découverte immédiate.** Le renfort du rayon (0,2) égalait le seuil (0,2) : un seul passage en
   pharmacie ou au centre commercial suffisait. 27,8 des 46 découvertes avaient lieu aux jours 1
   et 2, et la médiane tombait au jour 2,4.
2. **Des oublis qui n'en étaient pas.** Les quelques oublis étaient des sauts autour du seuil :
   l'agent repassait sous 0,2, puis un porteur vu le faisait remonter. La redécouverte venait
   **1 à 3 jours plus tard** dans la plupart des cas. En C1, chacune déclenche un appel sans
   délai de réflexion.
3. **Une exposition répétée gardait tout son effet.** Un rayon vu tous les jours rappelait le
   produit autant que le premier jour. Avec une perte de 6,7 % par jour, 0,013 de renfort
   quotidien suffit à maintenir A au-dessus du seuil.

## Pistes essayées

| Variante | Découverte médiane (jour) | Découvertes jours 1-2 | Connus au jour 7 | Oublis | Pic | Porteurs J90 |
| --- | --- | --- | --- | --- | --- | --- |
| Départ (4 nov.) | 2,4 | 27,8 | 56,4 | 4,4 | 53,4 | 43,0 |
| Habituation 0,3 | 2,4 | 27,8 | 56,2 | 7,4 | 52,8 | 44,8 |
| Renforts ambiants réduits (rayon 0,08, porteur 0,04) | 4,7 | 10,0 | 48,2 | 3,8 | 53,2 | 46,6 |
| Demi-vie 5 jours | 2,4 | 27,8 | 55,6 | 7,6 | 53,4 | 44,0 |
| Habituation + renforts réduits + seuil 0,3 | 9,0 | 10,0 | 29,6 | 18,4 (5-47) | 53,0 | 47,8 |
| Idem + **seuil d'oubli 0,15** | 8,4 | 10,0 | 31,2 | 2,0 | 53,6 | 45,4 |
| Idem + **messages réduits** (retenu) | 13,6 | 1,0 | 18,8 | 1,8 | 53,4 | 45,8 |
| Idem, demi-vie 7 jours | 23,8 | 1,0 | 17,0 | 7,4 (0-23) | 53,0 | 49,6 |

Lecture :

- L'habituation seule ou une demi-vie plus courte ajoutent des oublis, mais la découverte reste
  immédiate.
- Une découverte étalée demande un premier contact qui ne suffise plus. Il faut des renforts
  ambiants faibles et un seuil plus haut que chacun d'eux.
- Plus d'oublis, c'est d'abord plus de sauts autour du seuil : une graine monte à 47 oublis,
  presque tous suivis d'une redécouverte le lendemain. Le **seuil d'oubli** plus bas que le seuil
  de découverte (hystérésis) les supprime. Les oublis restants durent en médiane 4 à 8 jours avant
  la redécouverte.
- Réduire les messages ne change pas l'oubli (1,8 contre 2,0). Cela étale la découverte : le
  bouche-à-oreille entre proches devient la voie principale.
- Avec une demi-vie de 7 jours, les runs deviennent instables : de 7 à 54 porteurs au jour 60
  selon la graine. Elle est écartée.

## Réglage retenu (`configs/default.yaml`)

| Paramètre | Avant | Après | Pourquoi |
| --- | --- | --- | --- |
| `seuil` (découverte) | 0,2 | 0,3 | Plusieurs expositions, ou le message d'un proche (0,3) |
| `seuil_oubli` (nouveau) | — | 0,15 | Hystérésis : on garde en tête plus facilement qu'on ne découvre |
| `habituation` (nouveau) | — | 0,3 | Rayon et porteurs vus jour après jour deviennent le décor |
| `renforts.rayon` | 0,2 | 0,1 | Voir le produit en rayon ne suffit pas à le découvrir |
| `renforts.porteur_vu` / plafond | 0,1 / 0,3 | 0,05 / 0,15 | Même raison |
| `renforts.proche` | 0,5 | 0,3 | Un proche suffit, mais tout juste |
| `renforts.publication` | 0,3 | 0,15 | Une publication seule ne suffit pas |
| `renforts.article` | 0,4 | 0,25 | Même raison |
| `demi_vie_jours` | 10 ± 3 | 10 ± 3 | Inchangée : 7 jours rend les runs instables |

L'habituation est une moyenne glissante des jours où l'exposition était là :
`F ← F + 0,3·(présente − F)`. Le renfort ambiant est multiplié par `1 − F`. Un rayon vu chaque jour
rapporte 0,1, puis 0,07, puis 0,049, et ainsi de suite ; seul, il ne fait jamais découvrir le
produit. Un jour sans exposition fait baisser F : une exposition qui revient après une pause
rappelle de nouveau le produit. Messages et articles sont toujours nouveaux, ils ne s'habituent pas.

## Durée des flacons selon l'usage (X4)

Un flacon contient 16 journées d'usage ordinaire. Une journée à la plage ou à la piscine en
consomme 2, une journée au terrain de sport 1,5, et un jour ailleurs 1. L'usage d'un jour se
décompte au matin suivant, d'après le lieu où l'agent a passé la veille.

| | Avant (12 jours pour tous) | Après |
| --- | --- | --- |
| Durée moyenne d'un flacon | 12,0 jours | 11,3 jours |
| Écart type, minimum et maximum | 0, de 12 à 12 | 2,5, de 8 à 16 |
| Baigneurs occasionnels, habitués du soin | 12 | 9,7 et 10,4 |
| Pharmaciens, vendeurs | 12 | 14,7 et 16 |

Avec 16 et 1,5 au lieu de 14 et 1,25, la durée moyenne reste proche de 12 jours et la dispersion
est nettement plus forte (écart type 2,5 contre 1,5). Les rachats d'une même vague d'achats ne
tombent plus le même jour. Le creux constaté à l'étape 13 (19 flacons vides au jour 25) venait
d'une vague d'achats au jour 1 en C1. En C0, où l'adoption est déjà étalée, le maximum de flacons
vides par jour varie peu (7,8 contre 7,2).

## Résultat avec les deux réglages (C0, 5 graines, 90 jours)

| | 4 nov. | 10 nov. |
| --- | --- | --- |
| Agents qui connaissent le produit aux jours 1 / 7 / 30 | 24,8 / 56,4 / 59 | 12,8 / 18,8 / 57,6 |
| Découverte médiane | jour 2,4 | jour 13,8 (9 à 22) |
| Oublis (agents) | 4,4 (3,8), redécouverts en 1 à 3 jours | 2,2 (1,4), redécouverts en 4 à 8 jours (médiane) |
| Porteurs aux jours 30 / 60 / 90 | 37 / 45,4 / 43 | 30,6 / 42 / 45,8 |
| Pic (porteurs, jour) | 53,4, jour 41 | 54,2, jour 54 |

## Limites

- **L'oubli reste rare, à cause de la structure du modèle et non du réglage.** Un porteur
  n'oublie pas (décision du binôme), et environ 90 % des agents sont porteurs au pic. Il n'y a
  presque personne pour oublier. L'oubli jouera quand la mode reculera plus nettement : c'est la
  saturation (X3), à observer pendant les campagnes.
- Le calibrage est fait sur C0, où les décisions sont des règles. Il reste à le contrôler en C1
  par un run de développement (`configs/dev.yaml`, graine 42), à comparer à
  `20261104-121332-dev-s42` : moins de découvertes aux jours 1 et 2, des oublis sans sauts, des
  appels mieux étalés au début.

## Contrôle en C1 (run du 10 nov.)

`20261110-101324-dev-s42` (`dev.yaml`, graine 42, 60 jours, oubli calibré et flacons X4), comparé à
`20261104-121332-dev-s42` (même configuration avant le calibrage). Le rejeu sans LLM est fidèle.
Courbes : `comparaison_2026-11-10_1044.md` et `indicateurs_2026-11-10_1043.md`.

| | 4 nov. | 10 nov. |
| --- | --- | --- |
| Découvertes aux jours 1-2 / 3-7 / 8-14 / après | 44 / 5 / 0 / 0 | 0 / 11 / 34 / 4 |
| Découverte médiane | jour 2 | jour 11 |
| Première décision LLM : médiane, porteurs | jour 2, 16 sur 42 | jour 10, 12 sur 42 |
| Porteurs aux jours 1 / 10 / 30 / 60 | 9 / 28 / 36 / 44 | 2 / 10 / 28 / 37 |
| Pic, puis déclin depuis le pic | jour 60, 44 ; 0 | jour 40, 45 ; **8** |
| Oublis | 0 | 0 |
| Rachats à 12 jours pile (sur l'ensemble des écarts entre deux achats) | 60 sur 143 | 8 sur 126 ; 91 entre 8 et 16 jours |
| Sens final des rôles LLM (efficace / tendance / banal / dépassé) | 17 / 7 / 14 / 3 | 20 / 11 / 7 / 3 |
| Écart au jour 1 en fin de run (tous, rôles LLM) | 0,21 ; 0,08 | 0,24 ; 0,23 |
| Appels LLM, tokens en entrée, durée | 833 ; 1,30 M ; 1 789 s | 724 ; 1,08 M ; 1 411 s |
| Prompts réduits (faits publics retirés) | 314 (314) | 88 (88) |

Lecture :

1. **La découverte s'étale comme en C0** : plus aucune aux jours 1 et 2, la médiane passe au jour 11.
   L'adoption démarre lentement, puis monte jusqu'au pic du jour 40.
2. **Un vrai recul en cours de saison** : 8 porteurs de moins entre le pic et le jour 60. Le run du
   4 nov. ne reculait pas (une graine, à confirmer sur 5 graines, X3).
3. **Toujours aucun oubli en C1** : la structure qui limite l'oubli en C0 joue ici aussi.
4. **Flacons (X4)** : les rachats ne sont plus calés sur 12 jours. Le pic de 10 flacons vides au jour 56
   suit la vague de décisions du jour 45 : la promotion concurrente a fait décider les 60 agents,
   donc un vrai événement, pas l'ancien artefact.
5. **Premier contact (X2)** : 12 porteurs sur 42 à la première décision, contre 16. Moins
   d'adoptions immédiates, mais l'écart avec la baseline reste fort.
6. **Coût** : 13 % d'appels et 21 % de durée en moins, car il y a moins d'agents à décider au début.
   Les prompts réduits passent de 314 à 88.

## Écart au jour 1 (X1) et rôles LLM (X10), sur les runs de référence

Calculés après coup sur des runs antérieurs (`indicators.rapport`, colonnes recalculées) :

| Run | Convergence fin | Écart au jour 1, fin | Rôles LLM : répartition finale |
| --- | --- | --- | --- |
| Baseline C0 `20261022-130213-baseline-dev-s42` | 0,79 | **0,83** | efficace 5, tendance 36 |
| Témoin C0 `20261024-173700-temoin-baseline-dev-s42` | 0,68 | **0,00** | efficace 5, tendance 2, banal 34 |
| C1 avec mémoire `20261104-121332-dev-s42` | 0,11 | 0,21 | efficace 17, tendance 7, banal 14, dépassé 3 |

L'écart au jour 1 sépare la baseline de son témoin, ce que la convergence ne faisait pas (B3).
Côté X10, une fois fidèles et vendeurs retirés, le « tendance » de la baseline concerne toujours
36 agents sur 41. En C1, il ne concerne plus que 7 agents sur 41.
