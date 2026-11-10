# Pont FastAPI — référence pour l'interface

Le pont relie la simulation à l'interface (Godot, étape 10). Il tourne dans son propre processus
et ne parle à la simulation qu'à travers Kafka, Redis et l'historique Parquet : il peut rester
ouvert pendant que les runs démarrent et s'arrêtent.

```powershell
.venv\Scripts\python -m bridge            # http://127.0.0.1:8000, documentation interactive sur /docs
.venv\Scripts\python -m bridge.client     # client de test : affiche le flux comme la console
```

Le pont n'écoute que sur la machine locale.

## Flux WebSocket (D1)

| URL | Contenu |
| --- | --- |
| `ws://127.0.0.1:8000/ws` | Run en cours : un instantané, puis chaque événement publié |
| `ws://127.0.0.1:8000/ws?rejeu=<run_id>&vitesse=2` | Run enregistré, relu depuis Parquet (2 jours simulés par seconde ; sans `vitesse`, sans attente) |

Chaque message est un texte JSON. Ce sont soit les **événements du contrat** (voir
[`docs/evenements/`](evenements/README.md), champ `type` : `agent.decision`, `monde.meteo`…),
soit des **messages du pont**, dont le type commence par `pont.` :

| Type | Quand | Champs |
| --- | --- | --- |
| `pont.instantane` | À la connexion en direct | `run` (run_id, statut, jour, minute), `jour`, `meteo`, `lieux` (zones du run, les sept d'origine pour un run sans plan), `ville` (géographie de la ville, ou `null` : voir plus bas), `zones` (présents et porteurs par zone), `agents` (fiche de chaque agent, avec son `agent_id`), `perturbations` récentes |
| `pont.nouveau_run` | Un nouveau run démarre (son `monde.plan`, ou le pas du jour 1 d'un run plus ancien) | `run_id` (repartir d'un monde vide) |
| `pont.rejeu` | Début d'un rejeu | `run_id`, `evenements` (nombre à venir) |
| `pont.fin_rejeu` | Fin d'un rejeu : c'est au client de fermer la connexion (le pont attend 30 s, puis ferme) | `run_id` |
| `pont.erreur` | Rejeu impossible | `detail` |

Un run commence par l'événement `monde.plan` (étape 15 bis) : la liste de ses zones, `lieux`. Un type
de zone présent une fois garde son nom (`plage`) ; présent plusieurs fois, il est numéroté (`plage_1`,
`plage_2`…), et son nom seul désigne alors tous ses exemplaires (perturbation sur `plage`). Le champ
`zone` des événements porte ces noms. Un run enregistré avant l'étape 15 bis n'a pas de plan : ses zones
sont les sept d'origine.

Depuis le 10 nov., un run lancé avec une **ville** (`ville` dans la configuration, taille et plan urbain)
publie un `monde.plan` 1.1 dont `payload.ville` donne sa géographie : `taille`, `plan`
(`radioconcentrique`, `hippodamien`, `etoile`, `lineaire`), `positions` (lieu → `[x, z]`, en mètres ; la
mer est du côté des z positifs, les plages à z = 11,2), `routes` (segments `[x1, z1, x2, z2]` à tracer) et
`quartiers` (quartier → lieu fréquenté pour chaque type). La scène construit chaque lieu à sa position et
trace les routes ; sans `ville`, elle garde la disposition de la maquette.

Les événements arrivent dans l'ordre de publication, le même en direct et en rejeu. Un client trop
lent (plus de 10 000 messages en attente) est déconnecté plutôt que de ralentir le pont.

## Inspection (D2)

| Requête | Réponse |
| --- | --- |
| `GET /etat` | Run courant et statut (`en_cours`, `pause`, `termine`, `arrete`), jour, météo, zones, perturbations récentes |
| `GET /agents` | Fiche de chaque agent du run courant |
| `GET /agents/{agent_id}` | `etat` : fiche Redis (rôle, palier, zone, sens, porteur, seuil, notoriété A et lassitude L depuis l'étape 16, dernière action et sa justification, moteur, modèle et coût du dernier appel, 5 derniers messages reçus ; pour un agent à règles, la règle appliquée). `historique` : 10 dernières décisions (avec `caused_by`), messages envoyés et messages directs reçus, 5 derniers souvenirs (`agent.souvenir` : épisodes et bilans de semaine, depuis l'étape 16) |
| `GET /runs` | Runs enregistrés, rejouables |

La mémoire proprement dite des agents arrive avec le RAG (étape 12) ; d'ici là, l'historique des
décisions et des messages en tient lieu.

## Injections et commandes (D3)

| Requête | Corps | Effet |
| --- | --- | --- |
| `POST /injections` | `{"nature": "polemique" \| "publication_virale" \| "article_presse" \| "promotion_concurrente" \| "rupture_stock" \| "vague_chaleur" \| "orage", "description": "...", "zone": "pharmacie" (facultatif : un lieu du run, ou un type pour tous ses exemplaires)}` | Publie `controle.injection`. Au **début du jour simulé suivant**, l'orchestrateur publie une `monde.perturbation` (avec `caused_by` vers l'injection), perçue par les agents de la zone, ou par tous sans zone |
| `POST /commandes` | `{"commande": "pause" \| "reprise" \| "arret"}` | Appliquée **entre deux jours**. Un arrêt termine proprement le run (bilan et historique complets) |

Réponses : `202` si la demande est publiée, `409` s'il n'y a aucun run en cours ou en pause,
`422` si la demande est invalide (nature, zone ou commande inconnue, zone absente du run, description
vide).

## Paramétrage et lancement d'un run (étape 15 bis)

| Requête | Corps | Effet |
| --- | --- | --- |
| `GET /parametres?base=default.yaml` | — | Description du formulaire, tirée de la configuration de base : `bases` (fichiers de `configs/`), `roles` (rôle, palier, modèle, nombre, maximum), `zones` (type, nombre, maximum 4 ; ancien réglage), `ville` (`taille` et `plan` de la base, ou `auto` et `hasard` ; `tailles` : pour chacune, `lieux`, `minimum` d'agents imposé, nombre `conseille`, `auto_jusqu_a` ; `plans`), `modes`, `mode`, `moteurs`, `moteur`, `duree_jours`, `duree_max`, `graine`. `404` si la base n'existe pas |
| `POST /lancements` | `{"base": "dev.yaml", "population": {"pharmacien": 3, …}, "ville": {"taille": "auto" \| "petite" \| "moyenne" \| "grande", "plan": "hasard" \| "radioconcentrique" \| …}, "mode": "synchrone" \| "asynchrone", "moteur": "llm" \| "regles", "duree_jours": 60, "graine": 42}` | Écrit `runs/lancements/<horodatage>/interface.yaml` (`herite_de` la base, seuls ces réglages changent ; la population est remplacée en entier), la valide avec `load_config` (une ville trop grande pour le nombre d'agents est refusée ; sans `ville`, l'ancien `zones` reste accepté), puis lance `python -m sim.run --config <fichier>` dans son propre processus, sortie dans `sortie.log`. `202` avec l'état du lancement ; `409` si un run tourne déjà (lancé par le pont ou depuis un terminal) ; `422` si la demande ou la configuration est invalide |
| `GET /lancements` | — | État du dernier run lancé par le pont : `statut` (`aucun`, `en_cours`, `termine`, `echec`), `code` de sortie, `run_id` (dès que le run l'a affiché), `configuration`, `journal`, `dernieres_lignes` de sa sortie |

Le run lancé est un run ordinaire : son manifeste recopie la configuration complète, il se pilote par
`POST /commandes`. Son processus est détaché : il survit à un Ctrl+C dans le terminal du pont et à la
fermeture de ce terminal, mais **pas** à l'arrêt de toute l'arborescence de processus du pont
(gestionnaire des tâches, tâche de fond d'un outil) : ne pas arrêter ainsi un pont dont un run tourne.
Un seul run lancé par le pont tourne à la fois.

Une injection est datée de l'heure simulée courante du run (`sim_time_occurred`) ; la perturbation
qui en découle l'est du début du jour où elle prend effet. L'écart entre les deux est le délai de
prise en compte (C7).
