# Session du 25 novembre 2026 — Epic « Cartographie — types et objets » (développement)

Epic docflow **65d94dd0** (workspace `ossie-studio`, bloc `backlog`) : un espace
« Cartographie » où l'on crée des types d'objets reposant sur ArchiMate 3.2, puis
des objets et des relations à la main, en YAML dans l'espace Git. Suivi par l'epic
« Cartographie — modèles et vues » (4976292f), qui dépend du composant
`urban-archimate` (dépôt à part).
Branche : **`feature/cartographie-types-objets`**, partie de `dev` (`de68632`).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (25 novembre)

- **Clé d'un objet** unique **tous types confondus** (« 1. oui tout type confondu ») ; le slug d'un type figé.
- **Règles de relation** : **Ossie est la source unique** (« 2. oui c'est ossie ») ; `urban-archimate` les reçoit d'Ossie.
- **Menu** : « Cartographie » devient une entrée du menu principal, avec des onglets (Types, Objets, puis Modèles) (« 3. oui »).
- Plan de l'enabler f8167ae1 validé (« oui go ») : le format du catalogue est posé ici avec les seuls types utiles aux tests ; l'enabler 0e088eaa le complète.

## Tickets

| Ticket | Statut |
|---|---|
| f8167ae1 — Format des types et des objets dans l'espace Git (enabler) | En review |
| 0e088eaa — Types ArchiMate 3.2 en version de base, règles de relation (enabler) | À faire |
| e7521e80 — Espace « Cartographie » : types en vignettes, filtre, ajouter un type | Prêt pour dev |
| 5c39d4df — Types : personnaliser un type | Prêt pour dev |
| 6f71bcba — Objets : créer, modifier, supprimer à la main | Prêt pour dev |
| b58a0a13 — Relations : relier deux objets (fiche d'une relation) | Prêt pour dev |
| 41bf680e — Primitives MCP de lecture des types et des objets | Prêt pour dev |
| 38c9d26c — Clôture (avec Gael, jamais seul) | — |

## Notes techniques (format, f8167ae1)

- Module `backend/src/ossie_studio/cartographie/` : `catalog.py` (+ `archimate-3.2.yaml`, amorce : quelques éléments métier et applicatifs, Jonction, Accès, Association, Influence, Affectation, Composition, Flux), `model.py` (format d'un type et d'un objet : `CartoType`, `CartoObject`, `check_type`, `check_object`, `properties_schema`, chemins), `space.py` (`read_cartographie` avec signalements, `save_type`, `save_object`, `delete_object`), `history.py` (`file_dates` depuis `WorkdirManager.path_log`), `service.py` (`CartographieTarget`, écriture dans le brouillon), `errors.py` (`CartographieError`, `Problem` des référentiels).
- Réutilisé des référentiels : le contrôle d'une propriété (`referentiels.model.check_property`, rendu public) et l'écrivain YAML déterministe (`dump_model`).
- Propriétés d'un objet validées par JSON Schema 2020-12 (attributs du type ArchiMate + propriétés ajoutées, `additionalProperties: false`).
- Un objet utilisé par une relation ne peut pas être supprimé (`object_in_use`), règle de l'epic.
- Conventions : `docs/conventions-cartographie.md`.
- Tests : `backend/tests/test_cartographie_format.py` (critères 1 à 11, suppression refusée, dates Git, écriture réelle dans le brouillon).
