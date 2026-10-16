# Mondes vivants — Sujet 04 ÉPHÉMÈRE

Simulation multi-agents pilotée par LLM de la viralité d'une tendance. Projet de 5A IABD, EIGSI
Casablanca, en binôme, 8 semaines. Soutenance devant jury avec démonstration en direct.

Contexte détaillé (monde, acteurs, architecture, plan d'expériences) : `docs/contexte-projet.md`.
Énoncé complet et grille d'exigences : `docs/Mondes-Vivants_Projet-5A-IABD.html`.
Schémas d'architecture : `docs/Architecture_Mondes-Vivants_Creme-solaire.pdf`.

## Méthode de travail

Le projet avance **étape par étape**, selon le plan de `docs/journal/journal-de-bord.html`.

1. Une étape à la fois. Ne jamais commencer l'étape suivante sans confirmation explicite.
2. Les questions ouvertes sont posées au moment où elles bloquent, puis tranchées par le binôme.
3. À la fin de chaque étape : compléter le journal de bord (`docs/journal/journal-de-bord.html`),
   régénérer `journal-de-bord.pdf` à la racine (`docs/journal/build.ps1`) et mettre à jour l'état
   du projet dans `README.md`.
4. Après confirmation de fin d'étape : un commit clair et concis qui résume l'étape.

## Le monde

Un été simulé (90 jours, 1 pas = 1 jour) autour du lancement d'une **crème solaire** identique pour
tous, par une **marque fictive** dont le nom reste à définir.

- **Nom de marque** : jamais en dur dans le code ni dans les prompts. Toujours le paramètre
  `BRAND_NAME` lu depuis la configuration. Idem pour l'ingrédient phare et l'indice de protection,
  qui sont **fictifs**. Objectif : empêcher les agents de s'appuyer sur des marques réelles.
- **7 zones** : `pharmacie`, `plage`, `piscine`, `reseaux_sociaux`, `centre_commercial`,
  `terrain_sport`, `domicile`.
- **Sens partagé (exigence B1)** : catégories **fermées**, jamais de texte libre pour ce champ.
  Valeurs : `efficace`, `tendance`, `banal`, `depasse`.
- Aucune allégation médicale ou de santé dans le corpus : le produit est strictement cosmétique.

## Pile technique

| Rôle | Choix |
| --- | --- |
| Inférence LLM | Ollama sur l'hôte (GPU), modèles `qwen3:1.7b`, `qwen3:4b`, `qwen3:8b` |
| Embeddings | modèle d'embeddings Ollama (RAG) |
| Bus d'événements | Apache Kafka, un broker, mode KRaft, sous Docker |
| État courant | Redis |
| Historique des runs | Parquet interrogé avec DuckDB |
| Base de connaissances | ChromaDB |
| Orchestration | Python 3.12 `asyncio`, écrit à la main (pas de framework multi-agents) |
| Visualisation | Godot 4.x en 3D low-poly, rendu léger, relié par un pont FastAPI (WebSocket + REST) |

Machine de développement : HP Omen 16, RTX 5060 (8 Go de VRAM), Ryzen 9 8945HX, 32 Go de RAM.
**Les trois modèles ne tiennent pas ensemble en VRAM** : `1.7b` et `4b` restent chargés, `8b` est
chargé par phase. Grouper les appels par palier avant de les émettre.

## Règles qui s'appliquent à tout le code

Ces règles viennent des exigences notées de l'énoncé. Les respecter à chaque fois.

1. **Deux horloges (C3)**. Chaque événement porte `sim_time_occurred`, `sim_time_processed` et
   `wall_time_emitted`. Ne jamais confondre temps simulé et temps réel.
2. **Pas d'information du futur (C6)**. Un agent ne lit que ce dont le délai de livraison est
   écoulé. Toute requête RAG filtre sur `date_sim <= sim_time` de l'agent. Les violations sont
   détectées et comptées, pas silencieuses.
3. **Schémas versionnés (F4)**. Tout événement est validé par un modèle Pydantic portant
   `schema_version`. Un événement non conforme part dans `sim.dlq` sans arrêter la simulation.
4. **Tolérance aux pannes d'agent (A4)**. Sortie LLM invalide : une relance, puis repli sur une
   règle. Chaque incident est publié sur `sim.incidents` et compté, jamais avalé.
5. **Sortie structurée**. Les agents répondent en JSON imposé via le paramètre `format` d'Ollama :
   `action`, `sens` (catégorie fermée), `message` facultatif, `justification` courte.
6. **Rejeu sans LLM (E3)**. Tout run écrit un journal suffisant pour être rejoué à l'identique,
   sans aucun appel LLM, en reproduisant la même visualisation et les mêmes indicateurs.
7. **Mesure du coût (E4)**. Chaque appel journalise modèle, tokens en entrée et en sortie, durée.
   Chaque run totalise appels, tokens et temps.
8. **Traçabilité (F6)**. Tout indicateur affiché doit pouvoir remonter aux événements bruts qui
   l'ont produit : propager `caused_by` dans les événements.
9. **Nombre d'agents au lancement (C2)**. Jamais en dur : paramètre de configuration.
10. **Appel LLM sur déclencheur, pas à chaque pas**. Un agent dont la situation n'a pas changé
    conserve sa décision précédente. C'est le principal levier de budget.

## Attribution des modèles (exigence A5)

Le palier dépend du **rôle et de l'expérience** du profil, **jamais d'une catégorie sociale**
(âge, sexe, revenu, origine). Les seuils d'adoption hétérogènes sont des paramètres individuels
tirés dans une distribution, pas des attributs sociaux.

| Palier | Rôles | Nb | Modèle |
| --- | --- | --- | --- |
| Grand | pharmaciens, conseillers | 6 | `qwen3:8b` |
| Moyen | créateurs de contenu, journalistes, responsable de marque, habitués du soin solaire | 26 | `qwen3:4b` |
| Petit | baigneurs occasionnels, sceptiques pragmatiques | 52 | `qwen3:1.7b` |
| Règles | fidèles à une autre marque, vendeurs, marque concurrente, météo | 36 | aucun |

Une **seule famille de modèles** (Qwen3) : seule la taille varie entre profils, sinon la
comparaison demandée par A5 n'isole plus rien.

## Structure du dépôt

```
sim/         orchestrateur, horloge simulée, ordonnanceur
agents/      profils LLM et agents à règles
llm/         client Ollama, routeur par palier, schémas de sortie, comptage des coûts
bus/         producteurs et consommateurs Kafka, schémas d'événements
rag/         indexation ChromaDB, récupération filtrée, évaluation
indicators/  entropie du sens, adoption, traçabilité
bridge/      pont FastAPI vers Godot
replay/      écriture et relecture des journaux de run
corpus/      corpus fictif du monde (documents écrits par le binôme)
godot/       projet Godot
runs/        sorties de runs (non versionné)
docs/        contexte, décisions, rapport
```

## Conventions

- Python 3.12, `ruff` pour le formatage et le lint, annotations de type sur les fonctions
  publiques, Pydantic pour tout ce qui traverse Kafka.
- Noms de domaine en **français** (`zone`, `sens`, `agent`, `porteur`), noms techniques en anglais.
- Les secrets vivent dans `.env`, jamais versionné. `.env.example` sans valeurs est committé.
- `runs/`, `.venv/`, `.env` et les données Docker sont dans `.gitignore`.
- Chaque run est identifié par un `run_id` et une graine explicite, pour la reproductibilité.

## Décisions encore ouvertes

Ne pas les trancher seul : demander avant de coder autour.

- Le nom de la marque fictive, à valider par un test de contamination du modèle.
- La formule exacte de l'indicateur de sens partagé : accord modal, entropie normalisée, ou
  information mutuelle entre groupe et sens.
- Le nombre définitif d'agents et la durée simulée.
- Le système d'exploitation et la configuration de la machine de la collaboratrice.
