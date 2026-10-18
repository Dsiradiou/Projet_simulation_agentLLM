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
| `pont.instantane` | À la connexion en direct | `run` (run_id, statut, jour, minute), `jour`, `meteo`, `zones` (présents et porteurs par zone), `agents` (fiche de chaque agent), `perturbations` récentes |
| `pont.nouveau_run` | Un nouveau run démarre | `run_id` (repartir d'un monde vide) |
| `pont.rejeu` | Début d'un rejeu | `run_id`, `evenements` (nombre à venir) |
| `pont.fin_rejeu` | Fin d'un rejeu | `run_id` |
| `pont.erreur` | Rejeu impossible | `detail` |

Les événements arrivent dans l'ordre de publication, le même en direct et en rejeu. Un client trop
lent (plus de 10 000 messages en attente) est déconnecté plutôt que de ralentir le pont.

## Inspection (D2)

| Requête | Réponse |
| --- | --- |
| `GET /etat` | Run courant et statut (`en_cours`, `pause`, `termine`, `arrete`), jour, météo, zones, perturbations récentes |
| `GET /agents` | Fiche de chaque agent du run courant |
| `GET /agents/{agent_id}` | `etat` : fiche Redis (rôle, palier, zone, sens, porteur, seuil, dernière action et sa justification, moteur, modèle et coût du dernier appel, 5 derniers messages reçus ; pour un agent à règles, la règle appliquée). `historique` : 10 dernières décisions (avec `caused_by`), messages envoyés et messages directs reçus |
| `GET /runs` | Runs enregistrés, rejouables |

La mémoire proprement dite des agents arrive avec le RAG (étape 12) ; d'ici là, l'historique des
décisions et des messages en tient lieu.

## Injections et commandes (D3)

| Requête | Corps | Effet |
| --- | --- | --- |
| `POST /injections` | `{"nature": "rupture_stock" \| "polemique" \| "vague_chaleur", "description": "...", "zone": "pharmacie" (facultatif)}` | Publie `controle.injection`. Au **début du jour simulé suivant**, l'orchestrateur publie une `monde.perturbation` (avec `caused_by` vers l'injection), perçue par les agents de la zone, ou par tous sans zone |
| `POST /commandes` | `{"commande": "pause" \| "reprise" \| "arret"}` | Appliquée **entre deux jours**. Un arrêt termine proprement le run (bilan et historique complets) |

Réponses : `202` si la demande est publiée, `409` s'il n'y a aucun run en cours ou en pause,
`422` si la demande est invalide (nature, zone ou commande inconnue, description vide).

Une injection est datée de l'heure simulée courante du run (`sim_time_occurred`) ; la perturbation
qui en découle l'est du début du jour où elle prend effet. L'écart entre les deux est le délai de
prise en compte (C7).
