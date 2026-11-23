# Conventions des schémas générés

Ce document fixe **ce que produit** tout parseur de code et tout extracteur SQL,
pour que les schémas d'un fichier C# et d'une table Snowflake se lisent, se
comparent et se relient de la même façon. Un **validateur partagé**
(`ossie_studio.parsers.conventions.validate_schema`) l'applique à chaque schéma
produit : un schéma non conforme fait échouer la lecture du fichier concerné (il
est signalé dans le compte rendu, jamais écrit).

## Forme générale

- **JSON Schema 2020-12**, **un fichier par objet** (classe, record, struct,
  enum, interface, table, vue), **écrit en YAML** : `<clé>.schema.yaml` dans
  `raw/` (et l'asset de même nom dans `assets/`). Le suffixe double évite la
  collision avec les index YAML de `raw/` (`manifest.yaml`, `docs-index.yaml`).
  Écriture déterministe : clés triées, blocs, textes multilignes en `|`.
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

```yaml
$id: app.statut.schema.yaml
enum:
- ouvert
- fermé
title: Statut
x-kind: enum
x-language: csharp
x-visibility: public
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

```yaml
$id: app.commande-web.schema.yaml
allOf:
- $ref: app.commande.schema.yaml
properties:
  origine:
    type: string
    x-visibility: public
title: CommandeWeb
x-language: csharp
x-visibility: public
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
`x-namespace` (espace de noms d'origine : namespace C#, paquet Java/Kotlin,
module TypeScript ou Python ; absent pour une table ou un contrat),
`x-language` ou `x-engine`, et
`x-annotations` (attributs, décorateurs, annotations : `[Table]`, `@Entity`,
`@Column`, `Field(...)`…). Ces annotations serviront à relier une classe à sa
table.

## Enrichissement humain (assets) : `<clé>.extend.yaml`

Le travail humain d'un asset curé **n'est pas écrit dans son schéma** : il vit
dans le fichier voisin `sources/<slug>/assets/<clé>.extend.yaml` (ticket
7a284e55). Raison (Gael, 23/11/2026) : « garder les standards non altérés » — le
`<clé>.schema.yaml` reste un JSON Schema relisible par n'importe quel outil, et le
rafraîchissement depuis le brut ne touche jamais au `.extend.yaml`.

```yaml
object:
  businessName: Commande client   # nom métier
  description: |                  # description métier (≠ `description` du code)
    …
  concept: Vente                  # concept métier rattaché (texte libre)
  tags: [rgpd]                    # étiquettes (liste)
fields:
  montant:
    businessName: Montant TTC
```

- liste **fermée** de clés ; une entrée vide n'est pas écrite, un fichier sans
  entrée est supprimé ; écriture déterministe (clés triées) ;
- la classification et la certification s'y ajouteront sous `object` /
  `fields.<nom>` ;
- un asset écrit avant ce ticket peut encore porter `x-businessName`,
  `x-description`, `x-concept`, `x-tags` dans son schéma : ces clés sont lues, puis
  déplacées dans `.extend.yaml` à la prochaine écriture de l'asset.

Dans le schéma de l'asset, toute clé (standard, ou `x-` venue du parseur comme
`x-source`, `x-annotations`) est en lecture seule côté curation. `x-origin` donne
le chemin du schéma brut d'origine.

## Exception : `raw/docs/`

Le validateur des conventions **ne s'applique pas** à `sources/<slug>/raw/docs/`
ni à `docs-index.yaml` : les documents (Markdown…) sont recopiés tels quels et ne
sont pas des schémas.
