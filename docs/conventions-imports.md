# Conventions — imports de fichiers (référentiels et cartographie)

Module commun : `backend/src/ossie_studio/imports/` (déclaration, lecture, JSLT,
essai, compte rendu, journal), routes communes `api/import_routes.py`. Une **cible**
apporte ce qui dépend du domaine : un référentiel (`referentiels/import_target.py`),
un type de cartographie (`cartographie/import_target.py`). À l'écran seulement : une
clé d'API n'importe rien ; tout s'écrit dans le brouillon de la personne.

## Les fichiers d'un import

Dans le dossier d'imports de la cible (`referentiels/<nom>/imports/`,
`cartographie/imports/<slug du type>/`) :

| Fichier | Contenu |
|---|---|
| `<slug>.yaml` | la déclaration : libellé, format (csv, json, xlsx), lecture (séparateur, encodage, en-tête, feuille), correspondances colonne → propriété, **stratégie de suppression** (`deletion`) |
| `<slug>.jslt` | le template JSLT : une ligne du fichier → un élément |
| `<slug>.exemple.<format>` | le fichier d'exemple |
| `<slug>.provenance.yaml` | tenu par l'import : `created`, les clés des éléments qu'il a créés |

## Fusion seulement, et stratégie de suppression (ticket 6d36073b)

Gael : « la cartographie comme les référentiels doivent être en fusion seulement : je
corrige ou remplace si l'élément est différent, et j'ajoute si manquant ».

Un import corrige les éléments existants qui diffèrent (comparés par leur clé), ajoute
ceux qui manquent, laisse inchangés les identiques. Les éléments **absents du
fichier** suivent la stratégie choisie dans « Fichier d'exemple » :

| `deletion` | Choix à l'écran | Effet |
|---|---|---|
| `keep` (défaut) | On garde les éléments existants qui ne sont pas dans la liste | aucun absent n'est supprimé |
| `created` | On supprime les éléments absents que cet import a créés | seuls les absents dont la clé est dans la provenance de cet import |
| `all` | On supprime tous les éléments absents | tous les absents |

L'ancien mode « remplacement » (`mode: replace` dans une déclaration) se lit comme
`all`. L'essai suit la stratégie demandée, sinon celle enregistrée.

**Provenance** : après chaque import réussi, `created` = les clés qu'il avait créées et
qui existent encore, plus celles qu'il vient de créer. Un élément corrigé (existant
avant) n'y entre pas ; un élément supprimé à la main en sort. Les documents des
éléments ne sont pas touchés.

**Encore utilisé** : un absent à supprimer qu'un élément qui **reste** utilise (état
final) est gardé, et le reste de l'import s'applique (succès, « N éléments gardés car
utilisés »). Pour la cartographie : une relation qui reste vise l'objet, en source ou en
cible (la règle de l'écran) ; une relation supprimée dans le même import ne retient
rien, la présence dans un modèle non plus. Pour un référentiel : aucune règle
d'utilisation aujourd'hui. Les suppressions passent par le service
(`cartographie.space.delete_objects`, `referentiels.repo.write_referentiel`).

## Lignes taguées (ticket 43478978)

Gael : « taguer la ligne et laisser l'utilisateur la refuser ». Une cible peut taguer
un élément valide sans le refuser (`tags`) ; aujourd'hui, la cartographie tague une
ligne dont une propriété liée à un référentiel a une valeur absente de ce référentiel
(index des clés lu une fois par import).

- **Essai** : le compte rendu donne les lignes taguées (`tagged`, `tagged_lines` :
  place dans le fichier, clé, nouveau / modifié / inchangé, et chaque tag).
- **Import** : s'il y a des lignes taguées, le premier envoi n'écrit rien et ne
  journalise rien (409 `import_review_required`, compte rendu joint). L'écran les
  montre avec une case « Refuser » ; la personne renvoie le même fichier avec
  `reviewed` et les lignes refusées (`refuse`, « ligne 3 »).
- Une ligne **refusée** n'est pas écrite (l'élément existant de même clé reste tel
  quel) et n'est **pas absente** du fichier : la stratégie de suppression ne la touche
  pas. Une ligne taguée non refusée est écrite ; l'objet est signalé sur sa fiche.
- Compte rendu et journal : `tagged` et `refused` en plus des autres nombres.

## Compte rendu, journal et page des éléments gardés

- Compte rendu (essai et import) : nouveaux, modifiés, inchangés, en erreur, supprimés
  (`deleted`), absents gardés (`kept`, avec la raison `strategy_keep`,
  `not_created`, `in_use` et les utilisations), dont gardés car utilisés
  (`kept_in_use`).
- Journal (`audit_log`, types `referentiel_import` et `carto_import`) : auteur,
  fichier, stratégie, nombres. Les éléments gardés car utilisés d'une exécution sont
  rattachés à sa ligne (table `import_kept`, migration 0053).
- Page « Éléments gardés car utilisés » d'une exécution :
  `…/imports/<import>/executions/<id>` sous l'écran de la cible (API :
  `GET …/imports/{slug}/executions/{id}`). On y arrive depuis le compte rendu et depuis
  la page Historique.
