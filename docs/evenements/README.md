# Contrat d'événements

> Fichier généré par `python -m bus.export_schemas` à partir de `bus/events.py`.
> Ne pas le modifier à la main.

Tout ce qui circule sur Kafka est un événement validé par un modèle Pydantic. Chaque type
porte une version de schéma (`schema_version`). Un message qui ne correspond à aucun
schéma connu, y compris une version inconnue, est isolé dans `sim.dlq` sans arrêter la
simulation. Changer la forme d'un payload impose d'incrémenter sa version.

Le temps simulé se compte en **minutes entières depuis le jour 1 à 00:00** (un jour =
1 440 minutes). Le JSON Schema de chaque type est dans [`schemas/`](schemas/).

## Enveloppe commune

Champs présents dans tout événement. « Obligatoire » s'entend pour un message reçu : à la
construction, `event_id` et `wall_time_emitted` sont remplis automatiquement.

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `event_id` | string (uuid) | oui |  | Identifiant unique. |
| `schema_version` | string | oui |  | Version du schéma de ce type d'événement. |
| `run_id` | string | oui | longueur ≥ 1, longueur ≤ 64 | Run qui a produit l'événement. |
| `type` | string | oui |  | Type d'événement, qui détermine la forme du payload. |
| `sim_time_occurred` | integer | oui | ≥ 0 | Moment simulé où le fait se produit. |
| `sim_time_processed` | integer | oui | ≥ 0 | Moment simulé où l'émetteur a fini de le traiter (≥ sim_time_occurred). |
| `wall_time_emitted` | string (date-time) | oui |  | Horodatage réel d'émission, en UTC. |
| `source_agent_id` | string | oui | longueur ≥ 1, longueur ≤ 64, motif `^[a-z0-9_]+$` | Émetteur : agent ou composant. |
| `zone` | `pharmacie` \| `plage` \| `piscine` \| `reseaux_sociaux` \| `centre_commercial` \| `terrain_sport` \| `domicile` ou null | non |  | Zone concernée, si elle existe. |
| `caused_by` | liste de string (uuid) | non |  | Événements à l'origine de celui-ci (traçabilité, F6). |

## Types d'événements

| Type | Topic | Version | Description |
| --- | --- | --- | --- |
| [`horloge.pas`](#horlogepas) | `sim.world.events` | 1.0 | Début d'un pas de simulation (un jour). Synchronise les consommateurs. |
| [`horloge.fin_jour`](#horlogefin_jour) | `sim.world.events` | 1.0 | Fin d'un pas de simulation : tous les faits du jour sont publiés. Les indicateurs du jour sont calculés à sa réception (étape 14). |
| [`monde.meteo`](#mondemeteo) | `sim.world.events` | 1.0 | Météo du jour, produite par l'agent à règles météo. |
| [`monde.stock`](#mondestock) | `sim.world.events` | 1.0 | Niveau de stock d'un produit dans une zone de vente, après changement. |
| [`monde.perturbation`](#mondeperturbation) | `sim.world.events` | 1.1 | Perturbation effective dans le monde, conséquence d'une injection (D3). |
| [`agent.message`](#agentmessage) | `sim.agent.messages` | 1.0 | Message en langage naturel émis par un agent (A2). |
| [`agent.decision`](#agentdecision) | `sim.agent.decisions` | 1.3 | Décision d'un agent : action, sens attribué au produit, justification (D2). |
| [`agent.deplacement`](#agentdeplacement) | `sim.agent.decisions` | 1.0 | Déplacement d'un agent d'une zone à une autre (affiché dans Godot). |
| [`indicateur.valeur`](#indicateurvaleur) | `sim.indicators` | 1.1 | Valeur d'un indicateur calculée pendant le run ; caused_by liste ses sources. |
| [`controle.injection`](#controleinjection) | `sim.control` | 1.1 | Demande d'injection d'une perturbation depuis l'interface (D3). |
| [`controle.commande`](#controlecommande) | `sim.control` | 1.0 | Commande de pilotage du run depuis l'interface. |
| [`incident`](#incident) | `sim.incidents` | 1.0 | Incident d'agent ou de cohérence temporelle, tracé et compté (A4, C6). |
| [`dlq.rejet`](#dlqrejet) | `sim.dlq` | 1.0 | Message reçu non conforme à un schéma, isolé sans arrêter la simulation (F4). |

## Topic `sim.world.events`

### horloge.pas

Début d'un pas de simulation (un jour). Synchronise les consommateurs. Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `jour` | integer | oui |  |  |

### horloge.fin_jour

Fin d'un pas de simulation : tous les faits du jour sont publiés. Les indicateurs du jour sont calculés à sa réception (étape 14). Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `jour` | integer | oui |  |  |

### monde.meteo

Météo du jour, produite par l'agent à règles météo. Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `indice_uv` | integer | oui | ≥ 0, ≤ 15 |  |
| `temperature_c` | number | oui | ≥ -10, ≤ 50 |  |
| `conditions` | `ensoleille` \| `nuageux` \| `pluie` \| `canicule` | oui |  |  |

### monde.stock

Niveau de stock d'un produit dans une zone de vente, après changement. Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `produit` | `marque` \| `concurrent` | oui |  |  |
| `quantite` | integer | oui | ≥ 0 |  |

### monde.perturbation

Perturbation effective dans le monde, conséquence d'une injection (D3). Version 1.1.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `nature` | `rupture_stock` \| `polemique` \| `vague_chaleur` \| `promotion_concurrente` \| `article_presse` \| `publication_virale` \| `orage` | oui |  |  |
| `description` | string | oui | longueur ≥ 1, longueur ≤ 500 |  |

## Topic `sim.agent.messages`

### agent.message

Message en langage naturel émis par un agent (A2). Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `canal` | `direct` \| `zone` \| `reseaux` | oui |  |  |
| `destinataires` | liste de string | non |  |  |
| `contenu` | string | oui | longueur ≥ 1, longueur ≤ 500 |  |
| `livraison` | integer | oui | ≥ 0 | Moment simulé à partir duquel le message est lisible (délai de livraison). |

## Topic `sim.agent.decisions`

### agent.decision

Décision d'un agent : action, sens attribué au produit, justification (D2). Version 1.3.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `action` | `acheter` \| `appliquer` \| `recommander` \| `critiquer` \| `en_parler` \| `ignorer` \| `abandonner` | oui |  |  |
| `sens` | `efficace` \| `tendance` \| `banal` \| `depasse` | oui |  |  |
| `justification` | string | oui | longueur ≥ 1, longueur ≤ 500 |  |
| `porteur` | boolean | oui |  | L'agent utilise le produit à l'issue de cette décision. |
| `moteur` | `llm` \| `regle` \| `repli` | oui |  |  |
| `palier` | `grand` \| `moyen` \| `petit` ou null | non |  |  |
| `appel_llm` | LlmCall ou null | non |  |  |
| `motif` | `premiere_decision` \| `message` \| `perturbation` \| `exposition` \| `meteo` \| `lassitude` \| `flacon_vide` ou null | non |  | Déclencheur de la décision (version 1.1 et suivantes). |
| `sources` | liste de string | non |  | Passages du corpus et souvenirs montrés à l'agent (version 1.2, F1, F2). |

### agent.deplacement

Déplacement d'un agent d'une zone à une autre (affiché dans Godot). Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `depuis` | `pharmacie` \| `plage` \| `piscine` \| `reseaux_sociaux` \| `centre_commercial` \| `terrain_sport` \| `domicile` | oui |  |  |
| `vers` | `pharmacie` \| `plage` \| `piscine` \| `reseaux_sociaux` \| `centre_commercial` \| `terrain_sport` \| `domicile` | oui |  |  |

## Topic `sim.indicators`

### indicateur.valeur

Valeur d'un indicateur calculée pendant le run ; caused_by liste ses sources. Version 1.1.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `nom` | string | oui | longueur ≥ 1, longueur ≤ 64, motif `^[a-z0-9_]+$` |  |
| `valeur` | number | oui |  |  |
| `groupe` | string ou null | non | longueur ≤ 64 | Groupe concerné (role:<rôle>, zone:<zone>), ou None si global. |
| `fenetre_debut` | integer | oui | ≥ 0 | Minutes simulées depuis le jour 1 à 00:00. |
| `fenetre_fin` | integer | oui | ≥ 0 | Minutes simulées depuis le jour 1 à 00:00. |
| `effectif` | integer ou null | non | ≥ 0 | Nombre d'agents ou de décisions comptés (version 1.1). |
| `repartition` | object ou null | non |  | Comptes par catégorie : sens, ou nature de dérive (version 1.1). |

## Topic `sim.control`

### controle.injection

Demande d'injection d'une perturbation depuis l'interface (D3). Version 1.1.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `nature` | `rupture_stock` \| `polemique` \| `vague_chaleur` \| `promotion_concurrente` \| `article_presse` \| `publication_virale` \| `orage` | oui |  |  |
| `description` | string | oui | longueur ≥ 1, longueur ≤ 500 |  |

### controle.commande

Commande de pilotage du run depuis l'interface. Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `commande` | `pause` \| `reprise` \| `arret` | oui |  |  |

## Topic `sim.incidents`

### incident

Incident d'agent ou de cohérence temporelle, tracé et compté (A4, C6). Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `nature` | `sortie_invalide` \| `hors_role` \| `sans_reponse` \| `repli` \| `violation_temporelle` | oui |  |  |
| `agent_id` | string ou null | non | longueur ≥ 1, longueur ≤ 64, motif `^[a-z0-9_]+$` |  |
| `detail` | string | oui | longueur ≥ 1, longueur ≤ 2000 |  |
| `tentative` | integer | non | ≥ 0 |  |

## Topic `sim.dlq`

### dlq.rejet

Message reçu non conforme à un schéma, isolé sans arrêter la simulation (F4). Version 1.0.

Payload :

| Champ | Type | Obligatoire | Contraintes | Description |
| --- | --- | --- | --- | --- |
| `topic_origine` | string | oui |  |  |
| `brut` | string | oui | longueur ≤ 10000 |  |
| `erreur` | string | oui | longueur ≥ 1 |  |
