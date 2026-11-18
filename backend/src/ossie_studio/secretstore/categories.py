from __future__ import annotations

# Registre applicatif des catégories de secrets : extensible sans migration
# de la table — on ajoute une entrée ici, le schéma ne bouge pas.
SECRET_CATEGORIES: dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "azure_devops": "Azure DevOps",
    "oidc": "OIDC",
}
# gitlab et azure_devops servent aussi de catégories de secrets pour les
# connexions aux forges (github existait déjà).

# Forme de saisie de la valeur, par catégorie. « masked » : un champ texte
# masqué (jeton, client secret…). Les formes particulières (p. ex. un fichier de
# clef privée pour Snowflake) seront déclarées ici par le ticket qui les ajoute,
# sans toucher au service des secrets.
SECRET_CATEGORY_INPUTS: dict[str, str] = {slug: "masked" for slug in SECRET_CATEGORIES}


def is_known_category(slug: str) -> bool:
    return slug in SECRET_CATEGORIES
