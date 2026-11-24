# Paquets vendus

## jsonjoy-builder-1.0.4-89529b6-react18.tgz

Éditeur visuel de JSON Schema, licence MIT. Repris de l'application Miles
(`portail/frontend/vendor/jsonjoy-builder-1.0.4-89529b6.tgz`), avec son éditeur de
modèle (`SchemaEditor`), pour l'onglet Modèle des référentiels (ticket docflow
829a86a7 : « reprendre ce qu'on a fait pour Miles »).

- **Pourquoi pas npm** (comme Miles) : la version publiée (1.0.4, 25/06/2026) ne gère
  pas les `$ref` ; le support a été fusionné sur GitHub le 02/09/2026 sans nouvelle
  version publiée.
- **Provenance** : https://github.com/lovasoa/jsonjoy-builder, commit
  `89529b6f1d69a77861137c9e2c931af79c26b0d3` (2 septembre 2026), le même fichier que Miles,
  **corrigé pour React 18** (décision de Gael, 24/11/2026, option « B ») :
  - Miles est en React 19, ossie-studio en React 18.3. La bibliothèque rend deux
    contextes directement (`<Context value>`, écriture de React 19) : avec React 18,
    l'éditeur plante à l'ouverture.
  - Correction : ces deux rendus passent par `Context.Provider`
    (`dist/i18n/schema-builder-config.js`, `dist/registry/SchemaBuilderRegistryContext.js`) ;
    `package.json` du paquet : version `1.0.4-89529b6.react18`, dépendances paires
    `react` / `react-dom` en `>=18.3.0`. Rien d'autre ne change.
  - Vérifié par `src/referentiels/jsonjoy.test.tsx` (rendu avec React 18).
- **À faire** : quand le frontend passera en React 19 (ticket docflow « Passer le
  frontend d'ossie-studio en React 19 »), reprendre le fichier de Miles tel quel et
  supprimer cette copie corrigée ; puis repasser sur la version npm dès qu'une version
  ≥ 1.0.5 publie le support des `$ref` (en même temps que Miles).
