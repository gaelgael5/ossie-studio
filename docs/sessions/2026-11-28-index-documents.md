# Session du 28 novembre 2026 — Index des documents (enabler 15863091)

Premier ticket de l'epic docflow « Index des documents et validation avant
publication » (bc3ece1c), démarré à la demande de Gael (« tu trouves un nouvel epic
pas commencé et tu le démarres »).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Pas de PR sans sa demande.

## Ce qui est fait

- Migration `0054_document_index.sql` : contenu (par espace, chemin et empreinte),
  vues (branche publiée, brouillon d'une personne), fichiers d'une vue avec leur état.
- `doc_index/extract.py` : reconnaissance chemin → type de document et identité pour
  les types du registre, nom, champs, clés et références sortantes.
- `doc_index/store.py`, `doc_index/service.py` : contenu indexé une fois, comparaison
  disque / index, enregistrement, reconstruction.
- Crochet unique dans `DraftWorkdir._store` ; `DraftWorkdir.compare_index`.
- Tests : `tests/test_doc_index.py` (extraction, et les 5 critères du ticket).

Conventions : `docs/conventions-index-documents.md`.

## Choix

- Le **chemin** fait partie de la clé du contenu (le type et l'identité se lisent dans
  le chemin) ; « deux brouillons qui ont le même contenu pour un fichier partagent une
  seule entrée » reste vrai.
- Les références visent une identité ou un chemin (`path:`), pour couvrir les modèles.
- Une vue de brouillon repart de la vue publiée quand elle est comparée sur la même
  tête : pas de lecture du dépôt pendant un enregistrement.
