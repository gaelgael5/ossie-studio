# Conventions des schémas générés

Ce document fixe **ce que produit** tout parseur de code et tout extracteur SQL,
pour que les schémas d'un fichier C# et d'une table Snowflake se lisent, se
comparent et se relient de la même façon. Un **validateur partagé**
(`ossie_studio.parsers.conventions.validate_schema`) l'applique à chaque schéma
produit : un schéma non conforme fait échouer la lecture du fichier concerné (il
est signalé dans le compte rendu, jamais écrit).

## Forme générale

- **JSON Schema 2020-12**, **un fichier par objet** (classe, record, struct,
  enum, interface, table, vue).
- **Identité stable** : `$id` dérivé de la source et du nom qualifié de l'objet
  (espace de noms / module + nom ; schéma + table). Le nom de fichier en dérive.
  Jamais de dépendance à l'ordre de lecture.
- `title` = nom de l'objet ; `description` = commentaire de documentation (doc
  comment, docstring, commentaire de colonne) quand il existe, au niveau de
  l'objet et de chaque champ.
- **Toute clef non standard commence par `x-`** (métadonnées d'origine) ; toute
  clef de noyau commence par `$`. Une clef non standard sans `x-` est refusée.
- **Sortie déterministe** : clefs triées, propriétés dans un ordre stable, aucune
  donnée volatile, fin de ligne (`\n`) et encodage (UTF-8) fixés. Deux lectures
  du même original produisent les mêmes octets (`dump_schema`).

## Champs et types

- **Propriétés** : une par champ ou propriété lu, avec un type JSON Schema.
  Chaque parseur publie sa **table de correspondance** des types de son langage
  vers JSON Schema (`type`, `format`).
- **Obligatoire / facultatif** : non nullable → dans `required` ; nullable → hors
  de `required`.
- **Collections** → `array` + `items`. **Dictionnaires** → `object` +
  `additionalProperties`.
- **Énumérations** → `enum` avec les valeurs.

Exemple (énumération) :

```json
{ "$id": "app.statut.json", "title": "Statut", "enum": ["ouvert", "fermé"],
  "x-language": "csharp", "x-kind": "enum", "x-visibility": "public" }
```

## Références et héritage

- **Référence à un autre objet** → `$ref` vers le `$id` de l'autre schéma.
- **Clef étrangère SQL** → `x-references` sur la colonne (la relation vit sur le
  champ et disparaît avec lui).
- **Héritage** → `allOf` + `$ref` vers le parent, **sans aplatir** les propriétés
  héritées : un schéma ne dépend ainsi que de son propre fichier, ce qui garde la
  collecte incrémentale exacte.
- **Type non résolu** (bibliothèque externe, type absent de la source) →
  `x-unresolved-type` avec le nom tel qu'écrit, sans `$ref` cassé.

Exemple (héritage) :

```json
{ "$id": "app.commande-web.json", "title": "CommandeWeb", "x-language": "csharp",
  "x-visibility": "public", "allOf": [{ "$ref": "app.commande.json" }],
  "properties": { "origine": { "type": "string", "x-visibility": "public" } } }
```

## Visibilité (code uniquement)

`x-visibility` est **obligatoire** sur tout objet issu d'un parseur de **code**
(repéré par la présence de `x-language`) et sur chacun de ses champs, avec une
valeur normalisée parmi `public`, `internal`, `protected`, `private`.
`x-visibility-source` garde le mot-clef d'origine tel qu'écrit
(`protected internal`, `package-private`…). La visibilité est **absente** pour les
tables SQL et les schémas de contrats.

Les parseurs **lisent tous les types**, quelle que soit leur visibilité : on veut
voir un objet `internal` ou `private`, pas le perdre.

| Langage | Mot-clef d'origine | `x-visibility` |
|---|---|---|
| C# | `public` | `public` |
| C# | `internal`, `file`, absence de mot-clef sur un type de premier niveau | `internal` |
| C# | `protected`, `protected internal`, `private protected` | `protected` |
| C# | `private`, absence de mot-clef sur un membre | `private` |
| Java | `public` / `protected` / `private` | même valeur |
| Java | absence de mot-clef (paquet) | `internal` (`x-visibility-source: package-private`) |
| Kotlin | `public` ou absence / `internal` / `protected` / `private` | même valeur (`public` par défaut) |
| TypeScript | type exporté du module | `public` |
| TypeScript | type non exporté | `internal` |
| TypeScript | membre `public` / `protected` / `private` ou `#nom` | même valeur (`private` pour `#nom`) |
| Python | nom sans tiret bas | `public` |
| Python | nom commençant par `_` ou `__` | `private` (`x-visibility-source: convention`) |

## Métadonnées d'origine

Sous des clefs `x-` : `x-source` (fichier, ligne ou objet de catalogue),
`x-kind` (class, record, enum, table, view, materialized-view, json-content…),
`x-language` ou `x-engine`, et
`x-annotations` (attributs, décorateurs, annotations : `[Table]`, `@Entity`,
`@Column`, `Field(...)`…). Ces annotations serviront à relier une classe à sa
table.

## Exception : `raw/docs/`

Le validateur des conventions **ne s'applique pas** à `sources/<slug>/raw/docs/`
ni à `docs-index.yaml` : les documents (Markdown…) sont recopiés tels quels et ne
sont pas des schémas.
