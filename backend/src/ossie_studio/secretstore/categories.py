from __future__ import annotations

# Registre applicatif des catégories de secrets : extensible sans migration
# de la table — on ajoute une entrée ici, le schéma ne bouge pas.
SECRET_CATEGORIES: dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "azure_devops": "Azure DevOps",
}


def is_known_category(slug: str) -> bool:
    return slug in SECRET_CATEGORIES
