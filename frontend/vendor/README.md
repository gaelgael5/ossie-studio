# Paquets vendus

## jsonjoy-builder-1.0.4-89529b6.tgz

Éditeur visuel de JSON Schema, licence MIT. Repris de l'application Miles
(`portail/frontend/vendor/`), avec son éditeur de modèle (`SchemaEditor`), pour
l'onglet Modèle des référentiels (ticket docflow 829a86a7 : « reprendre ce qu'on
a fait pour Miles »).

- **Pourquoi pas npm** : la version publiée (1.0.4, 25/06/2026) ne gère pas les `$ref` ;
  le support a été fusionné sur GitHub le 02/09/2026 sans nouvelle version publiée
  (choix de Gael pour Miles, 23/10/2026). Même fichier ici, pour un seul éditeur.
- **Provenance** : https://github.com/lovasoa/jsonjoy-builder, commit
  `89529b6f1d69a77861137c9e2c931af79c26b0d3` (2 septembre 2026).
- **Construction** : `git checkout 89529b6 && npm ci && npm run build && npm test`,
  puis `npm pack`.
- **À faire** : repasser sur la version npm dès qu'une version ≥ 1.0.5 publie le support
  des `$ref`, et supprimer ce fichier (en même temps que Miles).
