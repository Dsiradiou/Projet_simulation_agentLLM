# Mondes vivants — Sujet 04 ÉPHÉMÈRE

Simulation multi-agents pilotée par LLM de la **viralité d'une tendance** : la montée, le pic et le
déclin d'un produit, et surtout l'évolution du **sens** que chaque groupe lui attribue.

Projet de 5ᵉ année, option IA & Big Data, EIGSI Casablanca. Travail en binôme sur 8 semaines,
soutenance avec démonstration en direct.

## Le monde simulé

Un été de 90 jours simulés (un pas = un jour) autour du lancement d'une **crème solaire** identique
pour tous, par une **marque fictive**. Le nom de la marque, l'ingrédient phare et l'indice de
protection sont des paramètres de configuration inventés, pour que les agents ne s'appuient jamais
sur des marques réelles.

- **7 zones** : pharmacie, plage, piscine, réseaux sociaux, centre commercial, terrain de sport,
  domicile.
- **4 sens possibles**, en catégories fermées : `efficace`, `tendance`, `banal`, `depasse`.
- **Population hybride** : agents LLM de trois tailles (Qwen3 1.7b, 4b, 8b), attribuées selon le
  rôle et l'expérience, plus des agents à règles (météo, vendeurs, marque concurrente, fidèles).

## Architecture prévue

```
Godot 4 (3D)  <--WebSocket-->  Pont FastAPI
                                    |
                             Apache Kafka (bus)
                               |         |         |
                   Orchestrateur    Indicateurs   Écrivain d'historique
                        |
          Ollama / ChromaDB / Redis / DuckDB + Parquet
```

| Rôle | Choix |
| --- | --- |
| Inférence LLM | Ollama en local (GPU), Qwen3 1.7b / 4b / 8b |
| Bus d'événements | Apache Kafka (mode KRaft, Docker) |
| État courant | Redis |
| Historique et rejeu | Parquet + DuckDB |
| Base de connaissances (RAG) | ChromaDB |
| Orchestration | Python 3.12, `asyncio` |
| Visualisation | Godot 4, scène 3D low-poly |

## État du projet

Le développement avance étape par étape. Le détail de chaque étape est expliqué dans
[`journal-de-bord.pdf`](journal-de-bord.pdf).

| # | Étape | État |
| --- | --- | --- |
| 0 | Nettoyage du dépôt | ✅ terminée |
| 1 | Squelette du dépôt (arborescence, configuration, outillage) | ✅ terminée |
| 2 | Infrastructure Docker (Kafka, Redis) | ✅ terminée |
| 3 | Mesure de la machine (benchmark Ollama) | ✅ terminée |
| 4 | Contrat d'événements | ✅ terminée |
| 5 | Horloge simulée et orchestrateur (mode synchrone) | ✅ terminée |
| 6 | Couche LLM (routeur par palier, replis, coûts) | ✅ terminée |
| 7 | Première tranche verticale | ✅ terminée |
| 8 | Historique et rejeu sans LLM | ✅ terminée |
| 9 | Pont FastAPI | à venir |
| 10 | Scène Godot 3D | à venir |
| 11 | Le monde : marque, population | à venir |
| 12 | Corpus fictif et RAG | à venir |
| 13 | Agents à règles et baseline | à venir |
| 14 | Indicateurs d'émergence | à venir |
| 15 | Mode asynchrone et cohérence temporelle | à venir |
| 16 | Campagnes d'expériences | à venir |
| 17 | Charge et pannes | à venir |
| 18 | Préparation de la soutenance | à venir |

## Installation

Prérequis à ce stade : **Python 3.12** (sous Windows : `py install 3.12`), **Docker Desktop**
(sous Windows, avec le moteur WSL2) et **Ollama** (testé avec la version 0.40.4) avec un GPU
NVIDIA d'au moins 8 Go de VRAM. Godot sera ajouté à l'étape qui l'utilise.

### Ollama

Télécharger les modèles (environ 9 Go) :

```powershell
ollama pull qwen3:1.7b
ollama pull qwen3:4b
ollama pull qwen3:8b
```

Définir ces variables d'environnement utilisateur, puis redémarrer Ollama :

| Variable | Valeur | Rôle |
| --- | --- | --- |
| `OLLAMA_MAX_LOADED_MODELS` | `3` | petit + moyen + embeddings chargés ensemble |
| `OLLAMA_NUM_PARALLEL` | `4` | jusqu'à 4 requêtes simultanées par modèle |
| `OLLAMA_KEEP_ALIVE` | `30m` | modèles gardés en mémoire entre deux appels |
| `OLLAMA_FLASH_ATTENTION` | `1` | calcul de l'attention plus rapide |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | cache de contexte deux fois plus petit en VRAM |
| `OLLAMA_CONTEXT_LENGTH` | `2048` | contexte suffisant pour un prompt d'agent (~650 tokens) |

Pour mesurer une autre machine : `.venv\Scripts\python -m llm.benchmark` (quelques minutes).
Pour mesurer la couche LLM (validité des réponses, relances, replis, coûts par modèle) :
`.venv\Scripts\python -m llm.validation` (environ une minute).
Les mesures de référence sont dans [`docs/mesures/`](docs/mesures/).

### Projet

```powershell
# 1. Environnement Python
py -V:3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock   # versions figées
.venv\Scripts\python -m pip install -e . --no-deps          # le projet lui-même
copy .env.example .env                                      # adresses des services, clés facultatives

# 2. Infrastructure : Kafka 4.3.1 (mode KRaft) et Redis 8.8.3
docker compose up -d --wait
.venv\Scripts\python -m bus.topics                          # crée les 7 topics sim.*

# 3. Vérification
.venv\Scripts\python -m pytest
```

Les tests d'intégration sont ignorés, avec un message, si les services ne répondent pas :
`-m integration` pour Kafka et Redis, `-m ollama` pour les modèles. Sans aucun service :
`pytest -m "not integration and not ollama"`. Pour arrêter l'infrastructure : `docker compose down`.

Sous Linux ou macOS : `python3.12 -m venv .venv`, puis `.venv/bin/python` à la place de
`.venv\Scripts\python`.

## Lancer un run

Kafka, Redis et Ollama doivent être lancés. Le run vérifie la présence des modèles avant de
démarrer. Pour suivre un run en direct, ouvrir d'abord le **client console** dans un second
terminal : il attend le prochain run et affiche chaque événement (météo, décisions avec leur coût,
messages, déplacements, incidents).

```powershell
# terminal 1 : affichage en direct
.venv\Scripts\python -m bus.console                  # ou --run <run_id> pour relire un run

# terminal 2 : un run
.venv\Scripts\python -m sim.run --config configs/tranche.yaml  # 3 agents LLM + 1 à règles, 5 jours (~1 min)
.venv\Scripts\python -m sim.run --jours 3                      # population complète (120 agents)
.venv\Scripts\python -m sim.run --factice --jours 5            # agents factices, sans LLM
.venv\Scripts\python -m sim.run --factice --jours 3 --latence 0.1  # latences mesurées x 0,1
```

Les rôles à règles sont tenus par un agent à seuil provisoire jusqu'à l'étape 13, et la météo
est factice.

Chaque run reçoit un identifiant (`20261017-173539-default-s42` : date, configuration, graine)
et un dossier `runs/<run_id>/` contenant `manifest.json` (configuration complète, graine,
versions, commit git), `bilan.json` (compteurs du run, dont le nombre d'événements publiés et
enregistrés) et `evenements/` (historique Parquet).

## Historique et rejeu

Chaque run est enregistré en Parquet dans `runs/<run_id>/evenements/` par un lecteur Kafka que le
run démarre lui-même : on garde exactement ce qui a circulé sur le bus. Un run passé se rejoue
**sans aucun appel LLM**, avec le même affichage que la console en direct :

```powershell
.venv\Scripts\python -m replay.rejeu <run_id>              # tout, sans attente
.venv\Scripts\python -m replay.rejeu <run_id> --vitesse 2  # 2 jours simulés par seconde
.venv\Scripts\python -m replay.rejeu <run_id> --verifier   # bilan recalculé = bilan.json ?
.venv\Scripts\python -m replay.ecrivain --run <run_id>     # reconstruit l'historique depuis Kafka (7 jours)
```

L'historique de tous les runs s'interroge avec DuckDB, par des requêtes nommées ou en SQL libre
sur les vues `evenements`, `runs`, `decisions`, `messages`, `incidents` :

```powershell
.venv\Scripts\python -m replay.historique                           # liste des runs
.venv\Scripts\python -m replay.historique --requete cout_par_modele  # ou sens_par_jour, incidents_par_nature, messages_par_palier
.venv\Scripts\python -m replay.historique --sql "SELECT palier, count(*) FROM decisions GROUP BY 1"
```

## Configuration

Les paramètres d'un run sont dans un fichier YAML de `configs/`, vérifié au chargement :
une clé inconnue ou une valeur invalide bloque le lancement avec un message explicite.
`configs/default.yaml` décrit la configuration principale : 120 agents, 90 jours simulés,
mode synchrone, graine 42, délais de livraison des messages par canal, modèle et parallélisme
de chaque palier, réglages des appels LLM et loi des seuils d'adoption. Le nombre d'agents se règle rôle par rôle dans la section `population`, sans
toucher au code.

## Structure du dépôt

| Dossier | Contenu |
| --- | --- |
| `sim/` | Orchestrateur, horloge simulée, chargement de la configuration |
| `agents/` | Rôles, profils LLM, agents à règles |
| `llm/` | Client Ollama, routeur par palier, mesure des coûts |
| `bus/` | Kafka : producteurs, consommateurs, schémas d'événements |
| `rag/` | Base de connaissances ChromaDB, récupération filtrée |
| `indicators/` | Indicateurs d'émergence et traçabilité |
| `bridge/` | Pont FastAPI vers Godot |
| `replay/` | Journaux de run et rejeu sans LLM |
| `corpus/` | Documents fictifs du monde |
| `godot/` | Projet Godot (scène 3D) |
| `configs/` | Configurations de runs (YAML) |
| `tests/` | Tests automatisés |
| `docs/` | Conception, énoncé, journal de bord |

## Documentation

- [`docs/contexte-projet.md`](docs/contexte-projet.md) : décisions de conception et leurs raisons.
- [`docs/evenements/`](docs/evenements/README.md) : contrat d'événements (enveloppe, 12 types,
  JSON Schema), généré depuis le code.
- [`docs/mesures/`](docs/mesures/) : benchmark des modèles sur la machine de référence.
- [`docs/Architecture_Mondes-Vivants_Creme-solaire.pdf`](docs/Architecture_Mondes-Vivants_Creme-solaire.pdf) :
  schémas d'architecture et dimensionnement.
- [`docs/Mondes-Vivants_Projet-5A-IABD.html`](docs/Mondes-Vivants_Projet-5A-IABD.html) : énoncé et
  grille d'exigences.
- [`journal-de-bord.pdf`](journal-de-bord.pdf) : ce qui a été fait à chaque étape, et pourquoi.
