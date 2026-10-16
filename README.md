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
| 1 | Squelette du dépôt (arborescence, configuration, outillage) | à venir |
| 2 | Infrastructure Docker (Kafka, Redis) | à venir |
| 3 | Mesure de la machine (benchmark Ollama) | à venir |
| 4 | Contrat d'événements | à venir |
| 5 | Horloge simulée et orchestrateur (mode synchrone) | à venir |
| 6 | Couche LLM (routeur par palier, replis, coûts) | à venir |
| 7 | Première tranche verticale | à venir |
| 8 | Historique et rejeu sans LLM | à venir |
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

Les instructions d'installation et de lancement seront ajoutées au fil des étapes.

## Documentation

- [`docs/contexte-projet.md`](docs/contexte-projet.md) : décisions de conception et leurs raisons.
- [`docs/Architecture_Mondes-Vivants_Creme-solaire.pdf`](docs/Architecture_Mondes-Vivants_Creme-solaire.pdf) :
  schémas d'architecture et dimensionnement.
- [`docs/Mondes-Vivants_Projet-5A-IABD.html`](docs/Mondes-Vivants_Projet-5A-IABD.html) : énoncé et
  grille d'exigences.
- [`journal-de-bord.pdf`](journal-de-bord.pdf) : ce qui a été fait à chaque étape, et pourquoi.
