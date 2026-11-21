# Identité visuelle — « Cartographe »

Ce document fixe **le parti pris visuel** d'Ossie Studio et les règles à suivre
pour qu'une nouvelle page lui ressemble. Tout vit dans un seul fichier,
`frontend/src/styles.css` ; ce document dit *pourquoi* et *comment s'en servir*.

## Le parti pris

Ossie Studio construit une couche sémantique : des objets, des relations, une
ontologie. **C'est une carte.** L'interface emprunte donc au plan technique et
au plan de métro, comme le fait déjà le plan de lignes des branches (`RailMap`).

## Les cinq motifs

| Motif | Où | Règle |
|---|---|---|
| **La ligne et ses stations** | nav, onglets, pictogramme de la marque | L'entrée active porte un anneau posé sur son trait. Un seul élément actif par ligne. |
| **Le lettrage en chasse fixe** | fil d'Ariane, étiquettes de panneau, en-têtes de tableau, libellés de champ, tags, cartouches de contexte | Réservé aux **étiquettes courtes** et aux **identifiants** (tag, slug, référence). Jamais pour une phrase. |
| **La trame de points** | fond de page | Uniquement sur le fond. Un panneau est toujours plein (blanc). |
| **Angles vifs, traits nets** | panneaux, boutons, champs, badges | Rayons de 3 à 4 px, bordure d'un pixel. **Pas d'ombre**, sauf ce qui flotte (menus). |
| **Une couleur de ligne** | boutons principaux, liens, sélection | Sarcelle sur fond clair (`--primary`), turquoise sur fond sombre (`--nav-accent`). C'est la même ligne. |

## Couleurs

Toutes les couleurs sont des **tokens** déclarés dans `:root` de `styles.css`.

- Une page n'écrit **aucune couleur d'interface en dur** : elle utilise un token
  (`var(--primary)`, `var(--muted)`, `var(--danger-fg)`…).
- Seules les **couleurs de données** s'écrivent en dur ou viennent de la base :
  couleur d'un niveau de classification, médailles Or / Argent / Bronze, palette
  des lignes de `RailMap`.
- Couleurs d'état : la teinte vive pour les bordures et aplats (`--danger`),
  `-fg` pour du **texte** (`--danger-fg`, `--success`, `--warn-fg`), `-bg` pour
  un fond teinté.
- Les noms `--ok`, `--ko`, `--ok-bg`, `--ko-bg`, `--warn`, `--muted-bg` sont des
  **alias historiques** : ne pas les employer dans du code nouveau.

## Polices

IBM Plex Sans (interface) et IBM Plex Mono (chasse fixe), **embarquées** avec
l'application par les paquets `@fontsource/*` : aucun appel externe. Les
graisses chargées sont listées en tête de `styles.css` (Sans 400/500/600/700 et
400 italique, Mono 400/500/600). Employer une autre graisse demande d'ajouter son `@import`,
sinon le navigateur la simule et le rendu se dégrade.

## Écrire une nouvelle page

Page de référence à imiter : `frontend/src/pages/ForgesPage.tsx`.

1. **En-tête** : le composant `PageHeader` (fil d'Ariane, titre, sous-titre,
   actions). Les pages Gouvernance passent par `GovernanceFrame`.
2. **Aucun élément brut** : un bouton, un champ, un tableau ou un titre porte
   toujours une classe du socle.

   | Besoin | Classes |
   |---|---|
   | Section d'une page | `page-section`, `section-head`, `section-title` |
   | Panneau | `panel`, `panel-label`, `panel-head`, `panel-title`, `panel-form` |
   | Bouton | `btn`, `btn-primary`, `btn-danger`, `btn-sm`, `btn-block`, `btn-link` |
   | Champ | `field` + `field-label` + `input` / `select` / `textarea`, `field-hint`, `field-inline`, `fieldset`, `check` |
   | Tableau | `panel table-wrap` autour de `table` ; cellule d'actions : `actions` |
   | Barre de filtres | `toolbar` |
   | Menu flottant | `menu-anchor`, `menu-popover`, `menu-item` |
   | Mise en page | `stack`, `stack-tight`, `row`, `push-right`, `measure`, `list-plain` |
   | États | `empty-note`, `info-note`, `banner`, `text-ok`, `text-danger`, `text-warn`, `muted`, `small` |
   | Étiquettes | `badge`, `badge-warn`, `badge-muted`, `pill`, `tag-chip` |

   Les cases à cocher, boutons radio et sélecteurs de fichier restent natifs
   (sans classe `input`).
3. Pas de `style={{…}}` pour une couleur, une bordure, un rayon ou une police.
   Le style inline reste acceptable pour une valeur pilotée par la donnée
   (`background: level.color`) ou un ajustement de mise en page ponctuel.
4. Un besoin que le socle ne couvre pas : ajouter une classe à `styles.css`,
   pas un style inline dans la page.
5. La marque passe par le composant `Brand`, jamais par un texte écrit à la main.
6. **Libellé et slug** : voir la règle ci-dessous — toujours le composant
   `LabelSlugFields`, jamais deux champs écrits à la main.

## Règle — saisir un libellé et un slug

Partout où l'on crée un objet qui porte un libellé et un slug (secret,
connexion de forge, connexion de base, source…) :

1. **Le libellé d'abord.** Le champ libellé précède le champ slug ; rien ne
   s'intercale entre eux, de sorte que **Tab** mène du libellé au slug.
2. **Le slug se déduit du libellé à chaque frappe**, en ne gardant que les
   caractères autorisés : minuscules sans accent, chiffres, tirets simples,
   64 caractères au plus (« Clé GitHub (prod) » → `cle-github-prod`).
3. **L'utilisateur peut corriger le slug.** Sa correction est nettoyée à la
   frappe (majuscules, accents et caractères interdits convertis) puis gardée :
   modifier ensuite le libellé ne l'écrase plus. Vidé et quitté, le champ slug
   se remet à suivre le libellé.
4. **En édition, le slug existant est figé** (il est référencé ailleurs) ; seul
   le libellé se modifie.
5. Côté serveur, un slug fourni est **validé** (même motif) et **refusé s'il est
   déjà pris** — jamais suffixé en silence ; absent, le serveur le déduit
   lui-même du libellé.

Mise en œuvre : `useLabelSlug` + `LabelSlugFields`
(`frontend/src/components/LabelSlugFields.tsx`), règle de transformation dans
`frontend/src/slug.ts` (miroir de `backend/src/ossie_studio/slugs.py`).
`frontend/src/labelSlugRule.test.ts` échoue si une page pose son propre champ
de slug.

Hors règle : les identifiants qui ne sont pas un slug dérivé d'un libellé —
identifiant de tag (chemin `gouvernance/…`, sans libellé distinct), nom de
niveau de classification (valeur stockée, accents permis), id de schéma (calculé
par le serveur).

## Garde-fous automatiques

`frontend/src/styles.test.ts` échoue si :

- un token référencé par `var(--x)` n'est pas défini (sinon la valeur de secours
  s'applique en silence et une palette parallèle s'installe) ;
- un couple texte/fond du système passe sous le seuil de contraste WCAG AA
  (4.5 pour le texte, 3 pour l'anneau de focus).

Changer une couleur du socle, c'est donc relancer `npm test`.
