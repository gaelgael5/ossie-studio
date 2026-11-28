# Session du 28 novembre 2026 — Lier une propriété d'un type de cartographie à un référentiel (ticket 43478978)

Ticket docflow **43478978** (workspace `ossie-studio`, bloc `backlog`), après 6d36073b
(stratégie de suppression à l'import), à la demande de Gael.

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Pas de PR sans sa demande.

## Décisions de Gael

1. Le lien se déclare dans un fichier séparé, `<slug>.extend.yaml`, à côté du type.
2. Format propre (pas d'annotation `x-…` dans le JSON Schema du type).
3. Les référentiels portent des **tags libres** (entreprise, contrainte…) ; un lien vise
   n'importe quel référentiel, filtré par tag à l'écran.
4. À l'import, une valeur absente du référentiel lié **tague** la ligne ; la personne
   peut la refuser.
5. Pas de propriété qui pointe vers un autre objet : une relation le fait déjà.
6. À l'écran et pour l'agent, une valeur absente est refusée ; un objet déjà enregistré
   dont la valeur a disparu est signalé sur sa fiche.

## Ce qui change

- **Référentiels** : `x-tags` dans `modele.schema.yaml` (contrôlé par `check_model` :
  `tags_invalid`, `tags_duplicate`) ; bandeau « Tags » dans l'onglet Modèle ; tags et
  filtre par tag dans la liste ; `tags` dans la liste de l'API de lecture et de
  `list_documents(referentiel_modele)`.
- **Cartographie** : `cartographie/referentiel_links.py` (format, contrôles, index des
  clés, état d'une valeur) ; `Cartographie.links` et `Cartographie.referentiels` lus par
  `read_cartographie` ; `save_links` ; `save_object` refuse une valeur absente nouvelle ;
  `save_type` fait suivre un renommage et refuse un retrait (`property_linked`) ;
  `delete_type` retire le fichier d'extension.
- **Routes** : `GET/PUT …/cartographie/types/{slug}/referentiels`,
  `GET …/types/{slug}/referentiels/valeurs` (`api/cartographie_referentiels.py`) ;
  `referentiel_links` dans la fiche d'un objet.
- **Import** (module commun) : `tags` de la cible, `Transformed.tags`,
  `refused_indices`, `report(…, refused)`, `imported_items(…, refused)` ; route
  `…/import` avec `reviewed` et `refuse` (409 `import_review_required`) ; journal :
  `tagged`, `refused`.
- **Agent** : refus `property_referentiel_unknown` expliqué (avec `list_documents`) ;
  `read_schema` annote la propriété (`x-referentiel`) ; standard `carto-objet`.
- **Écran** : bloc « Liens vers les référentiels » (onglet Propriétés du type) ; sur la
  fiche, liste des valeurs du référentiel, état et lien « Voir dans le référentiel »
  (`?valeur=` pré-remplit la recherche des valeurs) ; à l'import, revue des lignes
  taguées avec « Refuser ».

Conventions : `docs/conventions-cartographie.md` (Liens vers les référentiels),
`docs/conventions-imports.md` (Lignes taguées).
