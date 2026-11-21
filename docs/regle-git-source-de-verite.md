# Règle — le travail vit dans Git, l'application est jetable

Décidé avec Gael le 21 novembre 2026 : « l'objectif c'est bien que si demain on
casse l'application, on reconnecte le git et rien n'est perdu sur le travail. »

## La règle

Tout ce qui constitue **le travail d'un espace** est versionné dans le dépôt de
l'espace et passe par brouillon puis publication :

- sources (manifestes), objets `raw/`, assets curés, revues, curation ;
- gouvernance (tags gouvernés, certifications, classifications) ;
- l'usage des plugins par les sources ;
- plus tard, l'ontologie ;
- les **décisions** prises à la publication (impacts marqués « vérifié »,
  avertissements acceptés), portées par le message du commit.

La base de données ne garde que :

| Catégorie | Exemples |
|---|---|
| Identité et droits | utilisateurs, profils, équipes, connecteurs OIDC |
| Configuration technique | secrets, connexions de forge et de base, identifiants personnels, plugins natifs et leurs paramètres |
| Brouillons | modifications non publiées — **perdues si l'application est perdue** (accepté) |
| Ce qui se recalcule | index (`scan_files`), caches, journal de validation |

## Comment l'appliquer

- **Avant d'ajouter une table**, se demander si son contenu est du travail. Si
  la réponse est oui, il va dans le dépôt de l'espace, pas en base.
- Une contrainte qu'on aurait posée en base (unicité, référence) devient une
  **règle de validation** exécutée sur le brouillon. Ses erreurs bloquent la
  publication.
- Une information qu'on voudrait historiser (qui a fait quoi, quand) est déjà
  dans l'historique Git. Ne pas la dupliquer en base.

Mise en œuvre : epic docflow « Brouillon personnel et publication sous son
identité — le travail vit dans Git » (workspace `ossie-studio`).
