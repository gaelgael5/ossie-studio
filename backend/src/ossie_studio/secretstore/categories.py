from __future__ import annotations

# Registre applicatif des catégories de secrets : extensible sans migration
# de la table — on ajoute une entrée ici, le schéma ne bouge pas.
SECRET_CATEGORIES: dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "azure_devops": "Azure DevOps",
    "oidc": "OIDC",
    "postgres": "PostgreSQL",
    "sqlserver": "SQL Server",
    "snowflake": "Snowflake",
}

# Forme de saisie de la valeur, par catégorie. « masked » : un champ texte
# masqué (jeton, mot de passe, client secret…). « keyfile » : un fichier de clé
# privée et une phrase de passe facultative (Snowflake), ouverte à la création —
# c'est la clé ouverte qui est stockée, la phrase de passe n'est pas conservée.
SECRET_CATEGORY_INPUTS: dict[str, str] = {slug: "masked" for slug in SECRET_CATEGORIES}
SECRET_CATEGORY_INPUTS["snowflake"] = "keyfile"


def is_known_category(slug: str) -> bool:
    return slug in SECRET_CATEGORIES
