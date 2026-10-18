# Contexte du projet — Mondes vivants, sujet 04 ÉPHÉMÈRE

Ce document rassemble les décisions prises avant le début du développement, et leurs raisons.
Il est destiné à être lu par un humain ou par Claude Code quand une décision de conception doit
être reprise. Les instructions permanentes, elles, sont dans `CLAUDE.md` à la racine.

État au 16 octobre 2026 : conception terminée, développement non commencé.

---

## 1. Le sujet et son cadre théorique

Sujet 04 ÉPHÉMÈRE : simuler la **viralité d'une tendance**, c'est-à-dire sa montée, son pic et son
déclin, et surtout l'évolution du **sens** que les groupes lui attribuent.

Trois articles servent de cadre et de point de comparaison.

### Granovetter (1978), *Threshold Models of Collective Behavior*

Chaque acteur a un **seuil** : la proportion d'autres personnes qui doivent déjà avoir agi pour
qu'il agisse à son tour. Ce qui compte n'est pas la moyenne des seuils mais leur **distribution** :
deux groupes de préférences moyennes identiques peuvent produire des issues opposées. On ne peut
donc pas déduire les dispositions individuelles du résultat collectif.

*Conséquence pour le projet* : les seuils d'adoption des agents sont **hétérogènes** et tirés dans
une distribution paramétrable. C'est un levier d'expérience, et un des points où le LLM peut se
distinguer d'une baseline.

### Bass (1969), *A New Product Growth Model for Consumer Durables*

Modèle agrégé de diffusion avec deux coefficients : **p** (innovation, influence externe comme la
publicité) et **q** (imitation, bouche-à-oreille). Produit une courbe cumulée en S et une courbe de
ventes en cloche, avec un pic si q > p.

*Conséquence pour le projet* : c'est la **baseline naturelle** pour l'exigence E1. Il faut ajuster
p et q sur les runs et montrer ce que la simulation LLM fait que cette courbe ne reproduit pas.

### Berger & Le Mens (2009), *How adoption speed affects the abandonment of cultural tastes*

Plus un goût est adopté vite, plus il est abandonné vite : une montée rapide est lue comme le signe
d'une mode passagère, ce qui dissuade les adoptants potentiels et détourne les adoptants actuels.
Le déclin n'est donc pas symétrique de la montée.

*Conséquence pour le projet* : hypothèse directement testable. Faire varier la vitesse de montée
entre configurations et observer le déclin.

---

## 2. Comment le monde a été choisi

Six mondes ont été comparés (plateforme de vidéos courtes, lancement produit, écosystème de
développeurs, pratique d'entreprise, scène musicale, quartier). Critères : cycle complet réalisable
dans le budget d'appels, sens qui évolue réellement, corpus RAG écrivable, baseline Bass possible,
lisibilité dans Godot, risque que le LLM récite des connaissances réelles.

Le **lancement d'un produit de consommation** a été retenu (25/35), derrière le monde « entreprise »
(31/35) mais préféré pour sa simplicité de mesure et la facilité de la baseline Bass.

Douze produits ont ensuite été comparés. La **crème solaire** a été retenue pour trois raisons :

1. **Saisonnalité** : elle donne un cycle montée, pic, déclin sans avoir à l'inventer.
2. **Produit identique pour tous** : Bass et Granovetter s'appliquent directement, contrairement à
   un produit personnalisé où il n'y a plus d'objet commun qui circule.
3. **Usage visible en public** (plage, piscine, terrain de sport), donc imitation observable et
   proportion de porteurs affichable à l'écran.

**Piège identifié** : si le déclin s'explique uniquement par la fin de l'été, la baseline à règles
le reproduit et le LLM n'apporte rien (E1 tombe). Deux parades obligatoires : donner **la même
saisonnalité** à la baseline, et montrer un déclin d'usage **en cours de saison**.

**Marque** : une piste a d'abord été envisagée autour d'une marque réelle, puis abandonnée. Une
marque réelle ferait réciter au LLM ce qu'il sait d'elle, et l'énoncé impose un monde fictif et
neutre. Le nom sera inventé, sans parenté phonétique avec une marque connue, et validé par un test
de contamination : demander à un LLM sans contexte ce qu'il sait du nom choisi.

---

## 3. Les zones

| Zone | Ce qui s'y passe | Sens qui s'y forme |
| --- | --- | --- |
| `pharmacie` | Lancement, conseil, rupture de stock | fiable, recommandé |
| `plage` | Application, réapplication, regards | geste de statut ou geste obligé |
| `piscine` | Exposition prolongée, groupes d'amis | routine de groupe |
| `reseaux_sociaux` | Avis, démonstrations, polémiques | tendance ou marketing |
| `centre_commercial` | Promotions, concurrence | banal |
| `terrain_sport` | Usage fonctionnel, résistance à la sueur | efficace ou inadapté |
| `domicile` | Rachat ou abandon, décision privée | dépassé |

Une zone « lycée / campus » figurait dans une version antérieure. Elle a été **retirée** : le monde
se concentre sur l'été, donc hors période scolaire.

Il n'y a pas de marché de seconde main pour un cosmétique. Le signal d'abandon est donc le
**non-rachat** ou le passage à un produit concurrent, pas la revente.

---

## 4. Le sens partagé (exigence B1)

L'énoncé demande de définir « en termes observables » ce que « contexte commun » signifie. Ici :
**le sens que chaque groupe attribue au produit, et le degré auquel ce sens est partagé**.

Trois avis indépendants ont convergé sur la même base de mesure :

1. Chaque action d'agent déclare un `sens` parmi des **catégories fermées** imposées par un schéma
   JSON. Les catégories fermées limitent aussi la récitation de connaissances réelles et
   raccourcissent les réponses.
2. Pour chaque groupe on calcule la **distribution** des sens sur une fenêtre glissante.
3. Le degré de partage se mesure par une **entropie normalisée** : `C = 1 − H / log K`, proche de 1
   quand le groupe converge sur un seul sens.

Trois formulations restent à départager :

| Formulation | Avantage | Limite |
| --- | --- | --- |
| Accord modal (part du sens dominant) | Simple, lisible à l'écran | Ignore la dispersion des autres sens |
| Entropie normalisée | Mesure fine, facile à justifier | Un peu moins intuitive |
| Information mutuelle groupe-sens | Détecte la convergence **entre** groupes, donc la vraie émergence | Plus lourde à expliquer au jury |

**Preuve demandée (B3)** : comparer un run avec diffusion à un run témoin **sans diffusion**, où
chaque agent reste sur son sens initial. L'indicateur doit y rester plat. Prévoir aussi un test de
permutation des sens.

---

## 5. Les acteurs

| Acteur | Nb | Palier | Déclencheur d'appel LLM |
| --- | --- | --- | --- |
| Pharmaciens, conseillers | 6 | Grand | Question reçue, communiqué, rupture de stock |
| Créateurs de contenu beauté | 6 | Moyen | Tendance perçue, publication planifiée |
| Journalistes beauté | 3 | Moyen | Communiqué ou polémique |
| Responsable de la marque | 1 | Moyen | Bilan hebdomadaire des indicateurs |
| Habitués du soin solaire | 16 | Moyen | Message d'un proche, avis lu |
| Baigneurs occasionnels | 36 | Petit | Exposition à un porteur ou à un message |
| Sceptiques pragmatiques | 16 | Petit | Pression de l'entourage |
| Fidèles à une autre marque | 22 | Règles | Loyauté fixe, seuil de changement |
| Vendeurs et caissiers | 12 | Règles | Stock, promotions, rayonnage |
| Marque concurrente | 1 | Règles | Calendrier scripté |
| Météo et saison | 1 | Règles | Horloge : indice UV, chaleur, affluence |

Total : 120 agents, dont 84 LLM (70 %) et 36 à règles (30 %).

**Pourquoi ce découpage.** Les LLM servent là où le sens se négocie par la conversation. Les règles
portent la mécanique (stocks, promotions, météo, loyauté), qu'un LLM résoudrait plus lentement sans
rien apporter. Cette répartition est aussi le levier de sobriété demandé par E5.

---

## 6. Architecture

Voir le PDF `Architecture_Mondes-Vivants_Creme-solaire.pdf` pour les schémas. En résumé :

```
Godot 4  <--WebSocket-->  Pont FastAPI
                               |
                        Apache Kafka (bus)
                          |         |        |
              Orchestrateur    Indicateurs   Écrivain d'historique
                   |
     Ollama / ChromaDB / Redis / DuckDB+Parquet
```

Les agents ne s'appellent **jamais** directement : tout passe par des événements Kafka. C'est ce
qui permet d'enregistrer un run complet, de le rejouer et de calculer les indicateurs sans toucher
à la simulation.

### Où vit chaque donnée (exigence F3)

| Donnée | Besoin | Stockage |
| --- | --- | --- |
| Flux d'événements | Écriture continue, ordre garanti, rétention courte | Kafka |
| État courant du monde | Lectures et écritures très fréquentes, éphémère | Redis |
| Historique des runs | Volume cumulé, requêtes analytiques, conservation longue | Parquet + DuckDB |
| Base de connaissances | Recherche par similarité avec filtres | ChromaDB |
| Journal de rejeu | Un fichier par run, lecture séquentielle | JSONL ou Parquet |

### Topics Kafka

`sim.world.events`, `sim.agent.messages`, `sim.agent.decisions`, `sim.indicators`, `sim.control`,
`sim.incidents`, `sim.dlq`.

### Enveloppe commune des événements

`event_id`, `schema_version`, `run_id`, `type`, `sim_time_occurred`, `sim_time_processed`,
`wall_time_emitted`, `source_agent_id`, `zone`, `payload`, `caused_by`.

### Budget de VRAM (8 Go)

| Poste | Estimation |
| --- | --- |
| `qwen3:1.7b` | ~1,4 Go |
| `qwen3:4b` | ~2,5 Go |
| `qwen3:8b` | ~5,2 Go |
| Embeddings | ~0,3 Go |
| Cache de contexte | ~0,5 à 1 Go |
| Windows + Godot | ~1 à 1,5 Go |

Phase A (petit + moyen + embeddings) ≈ 6,2 Go : tient avec marge. Phase B (grand + embeddings)
≈ 7,2 Go : tient de justesse, parallélisme limité. D'où l'ordonnancement par palier.

### Alternatives écartées

| Alternative | Raison du rejet |
| --- | --- |
| Tous les agents sur des API gratuites | ~1 000 requêtes/jour contre ~2 000 appels par run, et dix runs minimum |
| Un seul grand modèle pour tous | Contraire à E5, trop lent sur 8 Go ; sert uniquement de référence |
| Familles de modèles différentes selon les profils | Ajoute un facteur de variation non isolé, brouille A5 |
| Framework multi-agents (LangGraph, CrewAI, AG2) | Masque l'horloge et les appels, complique C3, C4, C6 |
| PostgreSQL pour tout | Plus lourd ; Parquet et DuckDB suffisent et se transportent |
| Godot relié directement à Kafka | Pas de client Kafka natif pour Godot ; le pont sert aussi D2 et D3 |
| Ollama dans Docker | Le passage du GPU sous Windows ajoute de la complexité sans bénéfice |

---

## 7. Le RAG (exigences F1, F2, A1)

Deux collections ChromaDB.

- **`corpus_monde`** : documents fictifs écrits par le binôme — communiqués de la marque, fiches
  produit (ingrédient et indice **fictifs**), avis clients, articles de presse beauté, conseils de
  pharmacien, historique de lancements précédents. Métadonnées : type, date simulée de publication,
  zone, rôle de l'auteur.
- **`memoires`** : épisodes vécus par chaque agent LLM, avec date simulée et importance. Un résumé
  périodique compresse les épisodes anciens, pour qu'un agent se souvienne d'un échange vieux de
  plusieurs jours simulés (A1).

**Récupération** : top-4 par similarité, filtré par `date_sim <= sim_time` de l'agent (C6) et par
visibilité du rôle — un vendeur ne lit pas le courrier interne de la marque.

**Évaluation (F2)** : sur un échantillon d'une trentaine de décisions, journaliser les passages
retrouvés, annoter leur pertinence (0 ou 1), reporter la précision au rang 4. Le journal de
récupération fait partie de chaque run.

---

## 8. Plan d'expériences (E1, E2, E4, E5)

| Configuration | Composition | Rôle | Runs |
| --- | --- | --- | --- |
| C0 : règles seules | 120 agents à règles | Baseline de E1 | ≥ 5, idéalement 20 |
| C1 : mixte | 84 LLM sur 3 paliers + 36 règles | Configuration principale | ≥ 5 |
| C2 : tout grand modèle | Tous les agents LLM sur `qwen3:8b` | Référence de E5 | ≥ 5 |
| C3 : tout petit modèle | Tous les agents LLM sur `qwen3:1.7b` | Borne basse du coût | ≥ 5 |

Mêmes scénarios et mêmes graines entre configurations, sinon la comparaison ne vaut rien.

**Dimensionnement estimé** (hypothèses, à remplacer par un benchmark réel) : ~2 155 appels par run,
soit environ 47 minutes en séquentiel, 25 à 55 minutes avec du parallélisme.

**Dimensionnement mesuré** (benchmark du 16 octobre, `docs/mesures/benchmark_2026-10-16_1143.md`) :
latence médiane en séquentiel 0,76 s (1.7b), 1,49 s (4b), 2,14 s (8b). Avec les mêmes 2 155
appels : environ 41 minutes en séquentiel, environ 25 minutes avec le meilleur parallélisme de
chaque palier, plus environ 13 s par aller-retour vers le palier grand.

**Suivi des expériences** : ClearML a été étudié et jugé **pertinent mais facultatif**. Il servirait
à suivre les runs (E1, E2, E4) et à référencer les traces de rejeu (E3), en restant **hors de la
boucle de simulation** : il observe, il ne pilote pas. Un simple dossier de runs avec des fichiers
JSON et un script de comparaison peut suffire sur 8 semaines. Le jury ne note pas l'outil mais ce
qu'il permet de démontrer.

---

## 9. Risques identifiés

| Risque | Parade |
| --- | --- |
| VRAM saturée, rechargements fréquents | Ordonnancement par palier, contexte court, parallélisme progressif |
| Petits modèles qui cassent le JSON ou sortent du rôle | Schéma imposé, validation, une relance, repli sur règle, incidents comptés |
| Bridage thermique du portable | Secteur, profil de performance, pauses entre runs, températures consignées |
| Connaissances réelles du LLM sur les crèmes solaires | Marque, ingrédient et indice fictifs, catégories fermées, test de contamination |
| Incohérence temporelle | Délai de livraison, filtre de date dans le RAG, violations comptées |
| Quota d'API tombé pendant la démonstration | Cœur 100 % local, et rejeu (E3) comme filet |
| Saisonnalité qui explique tout | Même saisonnalité dans la baseline, déclin mesuré en cours de saison |
| Allégations de santé dans le corpus | Produit strictement cosmétique, aucune promesse médicale |

### Constats ouverts, à régler aux étapes indiquées

Constatés en cours de développement (mesures ou runs), pas encore corrigés. Relire cette table au
début de chaque étape ; retirer une ligne quand le constat est réglé, en le notant dans le journal
des décisions.

| Constat | Origine | Étape | Piste |
| --- | --- | --- | --- |
| Un message « réseaux » touche toute la population : tous les agents sont redéclenchés chaque jour, soit 7 560 appels par run au lieu de ~2 155 | Étape 5, run factice | 11 | Diffuser aux seuls abonnés de l'émetteur |
| La propension à parler dépend de la taille du modèle, pas du rôle : le 8b envoie un message à chaque décision (24/24), le 4b 7/24, le 1.7b jamais. Biaise la diffusion et la comparaison A5 | Étape 6, `docs/mesures/couche-llm_2026-10-18_1131.md` | 11, mesure à 14 | Consignes de parole par rôle dans les prompts, budget de messages par agent ; mesurer la part de messages par palier et par rôle |
| Le 1.7b produit parfois une action incohérente avec son sens (« banal » + « acheter ») et des justifications trop longues | Étape 6, même mesure | 14 | Compter dans le taux de sortie de rôle ; renforcer le prompt si le taux est élevé |
| Le 1.7b laisse parfois passer un caractère chinois dans le texte | Étape 6, essai préalable | 14 | Compté par `llm.validation` ; à intégrer au taux de sortie de rôle |
| Le 8b répond « tendance » 24/38 au benchmark, 14/24 à la mesure de la couche LLM : biais de prior | Étapes 3 et 6 | 14, 16 | À mettre en regard du run témoin B3 (Q2 du jury) |
| Même graine, réponses LLM pas identiques au token près (traitement en lot d'Ollama) | Étape 6 | 8 | Rejeu exact par le journal du run, jamais par un nouvel appel |
| Observations inventées : le jour 1, le pharmacien dit avoir vu un message du créateur qu'il n'a jamais reçu (information du futur dans le texte, invisible pour le contrôle de `caused_by`) | Étape 7, `docs/mesures/tranche_2026-10-18_1142.md` | 14 (et prompt à 11) | Détecter un identifiant d'agent cité dans la justification sans message reçu de lui, et le compter ; consigne « ne cite que ce qui figure ci-dessus » |
| Le chargement des modèles domine le temps : 46,6 s sur 64,6 s (une bascule par jour, ~9 s), soit ~14 min sur 90 jours | Étape 7 | 16 | Mesurer sur un run complet ; comparer au temps d'appel |
| Personnages flous : le pharmacien (8b) parle comme un particulier et reste « tendance » tous les jours ; un message coupé net par la longueur maximale ; texte de message qui contredit l'action (« testé » mais non porteur) | Étape 7 | 11 | Prompts par rôle (ton, expertise, consignes de longueur) |
| Météo factice incohérente (« canicule » à UV 6, « nuageux » à UV 8) | Étape 7 | 13 | Vraie météo à règles, saisonnière et cohérente |

---

## 10. Clés API

Le cœur du projet n'a besoin d'**aucune clé** : Ollama tourne en local. Les clés servent de renfort
et de plan B. Quotas relevés en octobre 2026, à revérifier car ils changent souvent.

| Fournisseur | Où créer la clé | Limites gratuites constatées |
| --- | --- | --- |
| Groq | console.groq.com, rubrique API Keys | 30 req/min, 1 000 req/jour, 8 000 tokens/min sur certains modèles |
| Google AI Studio | aistudio.google.com, « Get API key » | Variables par modèle, remise à zéro à minuit heure du Pacifique |
| GitHub Models | Jeton d'accès personnel avec `models:read` | 15 req/min et 150/jour sur les modèles « low » |
| OpenRouter | openrouter.ai, rubrique Keys | 20 req/min, 50 req/jour sans achat de crédits — **écarté** |
| Mistral (Experiment) | La Plateforme, plan gratuit | Non précisées ; **les requêtes peuvent servir à l'entraînement** |

Chaque membre du binôme crée sa propre clé. Les clés vivent dans `.env`, jamais versionné, jamais
dans un prompt ni une capture d'écran. En cas de fuite : révoquer immédiatement.

---

## 11. Journal des décisions

| Date | Décision |
| --- | --- |
| 7 oct. | Sujet 04 retenu. Trois articles synthétisés. |
| 7 oct. | Monde « lancement d'un produit de consommation » retenu parmi six candidats. |
| 13 oct. | Zones détaillées, produit encore indéterminé parmi douze candidats. |
| 14 oct. | Produit **commun** (non personnalisé) : crème solaire. Marque fictive, nom à définir. |
| 14 oct. | Zone lycée/campus retirée : le monde se concentre sur l'été. |
| 14 oct. | 11 rôles d'acteurs définis, trois paliers de modèles Qwen3 plus agents à règles. |
| 16 oct. | Architecture arrêtée : Ollama, Kafka, Redis, ChromaDB, DuckDB+Parquet, pont FastAPI, Godot. |
| 16 oct. | Prototype antérieur abandonné, développement repris de zéro étape par étape. |
| 16 oct. | Python 3.12. Scène Godot en 3D low-poly sur rendu léger : bâtiments distincts par zone, plage avec sable et mer, personnages animés. Modèles 3D gratuits, choisis par le binôme sur maquettes. |
| 16 oct. | Dépendances : pip + `requirements.lock`. Configuration des runs en YAML validée par Pydantic. |
| 16 oct. | Kafka 4.3.1 et Redis 8.8.3 sous Docker. Une partition par topic (ordre total, rejeu), rétention 7 jours, création automatique des topics désactivée. Redis sans persistance. Client Kafka : aiokafka. |
| 16 oct. | Ollama 0.40.4 réglé (3 modèles chargés, 4 requêtes parallèles, contexte 2048, cache q8_0). Benchmark : latences proches des hypothèses, sorties de ~110 tokens au lieu de ~70, JSON invalide uniquement par troncature à 160 tokens. |
| 16 oct. | Contrat d'événements v1.0 : 12 types sur les 7 topics, JSON validé par Pydantic, documentation générée depuis le code. Temps simulé en minutes entières. 7 actions d'agent fermées. Un événement par fait observable. |
| 17 oct. | Orchestrateur synchrone : barrière quotidienne (décisions à 10:00 sur l'état de la veille), ordre des paliers alterné d'un jour à l'autre (bascules divisées par deux), délais de livraison zone 0 / direct 2 h / réseaux 6 h, parallélisme petit 2 / moyen 4 / grand 4. |
| 17 oct. | À traiter à l'étape 11 : un message « réseaux » touche aujourd'hui toute la population, ce qui redéclenche tous les agents chaque jour (7 560 appels par run au lieu de ~2 155). Limiter la diffusion aux abonnés. |
| 18 oct. | Couche LLM : schéma JSON imposé à Ollama (justification d'abord, sens et action fermés, message facultatif avec canal ; déplacements laissés aux règles). Une relance, puis repli « seuil + continuité » (avis de la veille, sinon seuil de Granovetter individuel, loi normale bornée 0,35 ± 0,15). Porteur déduit de l'action. Modèle de chaque palier en configuration (C2/C3 sans code). |
| 18 oct. | Tranche verticale : 3 agents LLM (un par palier) + 1 agent à seuil, 5 jours, de bout en bout par Kafka et Redis, lus par un client console. 11 appels, 0 incident, 65 s dont 47 s de chargement de modèles. Le client console lit sans groupe Kafka et n'écrit jamais sur le bus. |
| 18 oct. | Mesure sur les vrais modèles : 72/72 réponses valides au premier coup. Le 8b envoie un message à chaque décision, le 1.7b jamais : propension à parler liée à la taille du modèle, à corriger à l'étape 11 (prompts par rôle, budget de messages) et à mesurer à l'étape 14. |

---

## 12. Prochaines étapes de développement

1. Mesurer la machine : benchmark par modèle, séquentiel puis parallèle. Remplace les latences
   supposées et alimente E4.
2. Monter l'infrastructure : `docker-compose.yml` (Kafka KRaft + Redis), environnement Python,
   structure du dépôt, `.gitignore`.
3. Première tranche verticale : 3 agents LLM (un par palier) et 1 agent à règles, 5 pas, événements
   publiés sur Kafka et lus par un client console.
4. Brancher Godot via le pont, avec une scène minimale.
5. Écrire le corpus fictif et indexer le RAG, tester la récupération filtrée par date.
6. Indicateurs, écrivain d'historique, lecteur de rejeu.
