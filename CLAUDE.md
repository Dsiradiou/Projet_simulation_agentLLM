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
tous, par une **marque fictive** : **Vaelys**, à l'ingrédient **nacrelle**, indice **Halo 4** (sur
une échelle inventée de 1 à 5), choisis le 18 oct. après un test de contamination
(`python -m llm.contamination`).

- **Nom de marque** : jamais en dur dans le code ni dans les prompts. Toujours `config.monde`
  (`brand_name`, `ingredient`, `indice_protection`). Objectif : empêcher les agents de s'appuyer
  sur des marques réelles. Tout nouveau nom fictif passe d'abord le test de contamination.
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

Machine de développement : HP Omen 16, RTX 5060 (8 Go de VRAM), Ryzen 9 8945HX, 16 Go de RAM (15,1 Go visibles par Windows) : lancer les longs runs depuis un terminal séparé, la mémoire vive est la ressource la plus juste.
**Les trois modèles ne tiennent pas ensemble en VRAM** : `1.7b` et `4b` restent chargés, `8b` est
chargé par phase. Grouper les appels par palier avant de les émettre. Mesuré (étape 3) : charger
le `8b` décharge les deux autres, et un aller-retour de palier coûte environ 13 s. Réglages
Ollama et mesures de référence : `README.md` et `docs/mesures/`.

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
configs/     configurations de runs en YAML, validées par sim/config.py
tests/       tests pytest
docs/        contexte, décisions, rapport, journal de bord
```

## Commandes

Depuis la racine, avec l'environnement `.venv` (Python 3.12) :

| Action | Commande |
| --- | --- |
| Installer | `.venv\Scripts\python -m pip install -r requirements.lock` puis `pip install -e . --no-deps` |
| Infrastructure | `docker compose up -d --wait` puis `.venv\Scripts\python -m bus.topics` |
| Benchmark Ollama | `.venv\Scripts\python -m llm.benchmark` (résultats dans `docs/mesures/`) |
| Mesure couche LLM | `.venv\Scripts\python -m llm.validation` (résultats dans `docs/mesures/`) |
| Run LLM (tranche) | `.venv\Scripts\python -m sim.run --config configs/tranche.yaml` |
| Run factice (sans LLM) | `.venv\Scripts\python -m sim.run --factice --jours 5` |
| Baseline C0 (sans LLM) | `.venv\Scripts\python -m sim.run --config configs/baseline.yaml [--graine 7]` |
| Comparer des runs | `.venv\Scripts\python -m replay.comparaison <run_id> <run_id> ...` |
| Console en direct | `.venv\Scripts\python -m bus.console` (avant le run, second terminal) |
| Rejeu sans LLM | `.venv\Scripts\python -m replay.rejeu <run_id>` (`--verifier` : bilan identique ?) |
| Pont FastAPI | `.venv\Scripts\python -m bridge` (puis `python -m bridge.client`) ; référence `docs/pont.md` |
| Scène Godot | `<GODOT_BIN> --path godot` (direct) ou `-- --rejeu <run_id> --vitesse 2` ; test : `pytest -m godot` |
| Évaluation du RAG | `.venv\Scripts\python -m rag.evaluation --modeles bge-m3` ; `--annoter <run_id>` ; `--noter <csv>` |
| Historique (DuckDB) | `.venv\Scripts\python -m replay.historique --requete <nom>` ou `--sql "..."` |
| Indicateurs | `.venv\Scripts\python -m indicators.rapport <run_id> ...` ; `indicators.trace <run_id> --variation` |
| Doc des événements | `.venv\Scripts\python -m bus.export_schemas` (après toute modif de `bus/events.py`) |
| Tests | `.venv\Scripts\python -m pytest` (`-m "not integration and not ollama and not godot"` sans services) |
| Lint et format | `.venv\Scripts\python -m ruff check .` et `ruff format .` |
| Journal PDF | `powershell -ExecutionPolicy Bypass -File docs/journal/build.ps1` |

Dépendances : les directes sont déclarées avec une version exacte dans `pyproject.toml`. Après tout
ajout, régénérer le verrou avec `pip freeze --exclude-editable > requirements.lock` (encodage ASCII).
Une dépendance n'est ajoutée qu'à l'étape qui l'utilise.

## Conventions

- Python 3.12, `ruff` pour le formatage et le lint, annotations de type sur les fonctions
  publiques, Pydantic pour tout ce qui traverse Kafka.
- Noms de domaine en **français** (`zone`, `sens`, `agent`, `porteur`), noms techniques en anglais.
- Les secrets vivent dans `.env`, jamais versionné. `.env.example` sans valeurs est committé.
- `runs/`, `.venv/`, `.env` et les données Docker sont dans `.gitignore`.
- Chaque run est identifié par un `run_id` et une graine explicite, pour la reproductibilité.
- Tout paramètre de simulation vient de `configs/*.yaml` via `sim.config.load_config`. Les
  services et les clés viennent de `.env` via `sim.settings.load_settings`.
- Événements : un type = une classe de `bus/events.py`, publiée avec `bus.producer`, lue avec
  `bus.consumer` (qui envoie les messages non conformes dans `sim.dlq`). Changer la forme d'un
  payload impose d'incrémenter son `schema_version` et de régénérer `docs/evenements/`.
- Temps simulé : minutes entières depuis le jour 1 à 00:00 (`sim.domaine`), jamais de flottant.
- Reproductibilité : tout tirage aléatoire d'un agent passe par `sim.run.agent_rng(seed,
  agent_id)` ; les publications d'un jour sont triées par identifiant d'agent ; ne jamais
  départager ni ordonner par `event_id` (tiré au hasard) ni par l'ordre d'un `set`. Un test
  compare deux runs de même graine dans deux processus distincts.
- Un agent ne lit ni Kafka ni Redis : l'orchestrateur lui fournit une `Perception` et publie
  sa `Decision` (`agents/base.py`), ainsi que les incidents qu'elle porte (`Decision.incidents`).
- Couche LLM : schéma de sortie dans `llm/sortie.py`, appels via `llm.routeur.Routeur` (modèle
  du palier lu dans `llm.modeles`), prompts dans `agents/prompts.py` (marque toujours lue dans
  `config.monde`). Toute tentative, même ratée, compte dans le coût de la décision.
- Historique : un run est enregistré par `replay.ecrivain` (lecteur Kafka lancé par
  `sim.run.executer_run`) et rejoué par `replay.rejeu` depuis Parquet, jamais en rappelant un
  modèle. L'ordre exact entre topics vient des en-têtes Kafka `producteur`/`seq` posés par
  `EventProducer` ; trier avec `bus.producer.ordre_publication`. Tout nouveau compteur de
  `Bilan` doit pouvoir être recalculé par `replay.bilan.bilan_depuis` (sinon le rejeu diverge).
- Pont : processus séparé, ne parle à la simulation que par Kafka, Redis et Parquet ; il n'écrit
  que sur `sim.control`. L'orchestrateur relève `sim.control` avant chaque jour : une injection
  devient une `monde.perturbation` (avec `caused_by`) au début du jour suivant, pause et arrêt
  s'appliquent entre deux jours. Toute route ou message `pont.*` ajouté se documente dans
  `docs/pont.md`.
- Scène Godot (`godot/`, maquette A retenue le 18 oct.) : elle n'a aucune logique de simulation,
  elle applique les messages du pont (`scripts/monde.gd`). Modèles 3D : kits CC0 seulement, rangés
  par kit dans `godot/assets/` avec leur licence, crédités dans `godot/LICENCES.md`. Tout nouveau
  type de message affiché se teste dans `godot/tests/test_monde.gd`.
- RAG (étape 12) : corpus dans `corpus/documents/` (règles dans `corpus/README.md` : gabarits
  `{marque}`…, aucune allégation de santé, faits seulement pour les documents datés). Toute
  recherche filtre sur la date (`jour <= jour de l'agent`, C6) et le rôle ; les souvenirs d'un
  agent ne viennent que de jours passés. Chaque décision publie ses `sources` (F2). Embeddings :
  `bge-m3`, choisi sur mesure. Un nouveau document s'accompagne d'une question dans
  `corpus/evaluation/reference.yaml`.
- Les noms de topics viennent de l'énumération `bus.topics.Topic`, jamais d'une chaîne en dur.
  La création automatique de topics est désactivée sur le broker.

## Constats ouverts

Les défauts constatés et pas encore réglés sont listés, avec l'étape prévue pour les régler, dans
`docs/contexte-projet.md` (section 9, « Constats ouverts »). Les relire au début de chaque étape.

## Population et configurations (décidé le 18 oct.)

- `configs/default.yaml` : la référence, 120 agents sur 90 jours.
- `configs/dev.yaml` : 60 agents sur 60 jours, mêmes proportions, pour les essais de développement.
- `configs/tranche.yaml` : la tranche verticale (4 agents, 5 jours).
- Une population réduite de soutenance sera ajoutée à l'étape 18 (C8).
- Une variante déclare `herite_de: default.yaml` et ne précise que ce qui change ; la population
  est alors remplacée en entier.

## Le monde vivant (étape 11)

- Réseau social (`reseau`) : une publication « réseaux » ne touche que les abonnés de son auteur ;
  les messages directs visent les proches (`contacts`). Graphe tiré avec la graine du run.
- Routines (`routines`) : déplacements du matin par règles, selon le rôle et le temps
  (`sim/routines.py`), publiés à 08:00 avec la météo pour cause.
- Déclencheurs (`declencheurs`) : en plus des messages et perturbations, exposition aux porteurs,
  météo marquée et lassitude. Chaque décision publie son `motif` ; le bilan les compte.
- Budget de parole (`agents.messages_par_semaine`) : c'est le rôle qui décide de parler ; budget
  épuisé, le schéma JSON n'autorise plus que `message: null`.

## Règles du monde et baseline (étape 13)

- Météo saisonnière (`sim/meteo.py`, section `meteo`) : montée puis plateau, pas de rentrée
  (décision du 19 oct.) ; conditions et indice UV toujours cohérents.
- Commerce (`sim/commerce.py`) : stock par point de vente ; un nouveau porteur, ou un porteur dont
  le flacon est fini (12 jours), doit acheter ; en rupture, l'achat échoue (décision publiée avec
  `porteur: false`). Achats dans un ordre tiré avec la graine. Les vendeurs commandent selon les
  ventes de la semaine. Le motif `flacon_vide` déclenche une décision sans délai de réflexion.
- Les perturbations ont des effets mécaniques (`reagir`) : vague de chaleur, orage, rupture de
  stock, promotion concurrente. Elles agissent avant la météo du jour.
- Agents à règles (`agents/regles.py`) : fidèles et vendeurs (`AgentAuSeuil`), marque concurrente
  (`AgentConcurrent`, calendrier jours 21 et 45), baseline C0 (`AgentRegleC0` : seuil + Bass +
  déclin) quand `agents.moteur: regles`. Une même graine donne les mêmes agents, zones, seuils et
  météo en C0 et en C1 : seule la décision change (E1).
- Comparer des runs : `python -m replay.comparaison <run_id>...` (courbes, pic, déclin, dispersion).

## Indicateurs d'émergence (étape 14)

- Sens partagé (décidé le 20 oct.) : **convergence** `1 − H / log 4` (globale, par rôle, par zone)
  et **information mutuelle** rôle × sens normalisée, avec p-valeur par permutation
  (`indicators/calcul.py`). Les deux se lisent ensemble.
- `indicators.service` : consommateur lancé par `sim.run`, qui publie les indicateurs à chaque
  `horloge.fin_jour` ; chaque valeur cite ses décisions (`caused_by`). Le même code sert au calcul
  après coup (`indicators.rapport`) et à la trace (`indicators.trace`, F6/B4).
- Dérives des décisions LLM (`indicators/derives.py`) : heuristiques, taux = borne basse.
- Run témoin B3 : `simulation.diffusion: false` (aucun message remis, part de porteurs masquée) ;
  `configs/temoin*.yaml`.

## Décisions encore ouvertes

Ne pas les trancher seul : demander avant de coder autour.

- Ajouter ou non une mesure de déplacement du sens par rapport au jour 1 : la convergence seule
  ne distingue pas la baseline de son témoin (B3, étape 14).
- Le système d'exploitation et la configuration de la machine de la collaboratrice.
