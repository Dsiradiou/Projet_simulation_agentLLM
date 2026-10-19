# Corpus fictif du monde Vaelys

Documents que les agents LLM retrouvent avant de décider (RAG, exigence F1). Rédigés à l'étape 12
par Claude, **à relire et valider par le binôme** avant les campagnes d'expériences.

## Règles d'écriture

1. **Monde fictif** : aucune marque, aucun produit, aucun lieu, aucun média réels. Les noms du
   monde s'écrivent avec des gabarits : `{marque}`, `{ingredient}`, `{concurrent}`. Ils sont
   remplacés à la lecture par `configs/*.yaml` (Vaelys, nacrelle, Zelvane), jamais écrits en dur.
2. **Aucune allégation médicale ou de santé** : le produit est strictement cosmétique (texture,
   fini, tenue à l'eau, prix, usage). Un test refuse les mots « cancer », « maladie »,
   « dermatologique », « soigne », « guérit », « médical ».
3. **Faits seulement pour les documents datés** (décision du binôme, 18 oct.) : un document peut
   annoncer un lancement, une promotion, un nouveau format, ou donner un avis sur le produit. Il ne
   dit jamais que Vaelys « devient la mode » ou « passe de mode » : ce jugement doit émerger des
   agents, sinon le corpus écrirait la courbe à leur place.
4. **Court** : 40 à 80 mots par document, un seul sujet.

## Format

Un fichier Markdown par document dans `documents/`, avec un en-tête :

```markdown
---
id: promo-concurrent          # identifiant unique, cité dans les sources des décisions
type: concurrent              # fiche_produit, communique, avis_client, article, conseil, note_interne, concurrent
titre: "Promotion {concurrent} au centre commercial"
jour: 21                      # jour simulé de publication (0 = avant l'été) : invisible avant (C6)
visibilite: tous              # ou un rôle : pharmacien, vendeur, responsable_marque...
zone: centre_commercial       # facultatif
auteur: "{concurrent}"
---

Du jour 21 au jour 27, {concurrent} est vendu 8 euros le flacon de 100 ml au centre commercial.
```

## Contenu actuel (37 documents)

| Type | Nombre | Exemples |
| --- | --- | --- |
| Fiches produit | 5 | fiche Vaelys, échelle Halo (et différence avec l'indice UV), nacrelle, prix, questions fréquentes |
| Communiqués de la marque | 7 | lancement (J1), échantillons (J1), fin des échantillons (J14), réponse sur la texture (J25), format familial (J30), animation plage (J35), fin de saison (J75) |
| Avis clients | 12 | texture, brillance, tenue à la baignade, sport, prix, format familial (J3 à J55) |
| Articles | 4 | crèmes de l'été, modes passées, interview de la marque (J7), enquête sur la nacrelle (J14) |
| Conseils | 2 | mode d'emploi, conseils de la pharmacie |
| Concurrent | 3 | fiche Zelvane, promotion (J21), nouvelle formule (J45) |
| Notes internes | 4 | guide officine (pharmaciens), notes aux vendeurs, plan de lancement (responsable de marque) |

## Relecture

- Modifier ou ajouter un fichier dans `documents/`, puis lancer les tests
  (`.venv\Scripts\python -m pytest tests/test_rag.py`) : ils vérifient le format, les gabarits et
  l'absence d'allégation de santé.
- L'index ChromaDB se reconstruit seul au prochain run quand le corpus change.
- Le jeu de référence de l'évaluation est dans `evaluation/reference.yaml` : ajouter une question
  quand on ajoute un document, et relancer `python -m rag.evaluation --modeles bge-m3`.
