"""Qui utilise un secret.

Un secret peut être désigné par plusieurs objets (connexions aux forges et aux
bases aujourd'hui, connecteurs OIDC demain). On tient un **registre** des tables
qui portent un slug de secret : un ticket qui ajoute un usage ajoute une entrée
ici, jamais une requête en dur ailleurs. La table des secrets ne connaît donc
pas ses usagers, mais sait les compter et les lister.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class SecretUsage:
    table: str
    slug_column: str
    kind: str
    label_column: str


# Tables qui désignent un secret par son slug. Noms de code (jamais de saisie
# utilisateur) : l'interpolation dans la requête est donc sûre.
SECRET_USAGES: tuple[SecretUsage, ...] = (
    SecretUsage("connections", "secret_slug", "connection", "label"),
    SecretUsage("oidc_connectors", "client_secret_slug", "oidc_connector", "label"),
    SecretUsage("db_connections", "secret_slug", "db_connection", "label"),
)

# État que prend un usager éprouvé quand la valeur de son secret change : le
# nouveau secret n'a jamais été testé, afficher « valide » serait mentir.
NEVER_TESTED = "never_tested"

# Usagers qui portent un état de test (last_status / last_checked_at /
# last_error_code). Les connecteurs OIDC n'en ont pas.
TESTED_USAGES: tuple[str, ...] = ("connections", "db_connections")


async def db_reset_status_for_secret(conn: asyncpg.Connection, secret_slug: str) -> int:
    """Remet à « jamais testé » tout usager éprouvé qui désigne ce secret ; rend
    le nombre de lignes touchées. À appeler dans la transaction qui change la
    valeur, pour qu'aucun lecteur ne voie la nouvelle valeur avec l'ancien état."""
    touched = 0
    for table in TESTED_USAGES:
        result: str = await conn.execute(
            f"UPDATE {table} SET last_status = $2, last_checked_at = NULL, "  # noqa: S608
            "last_error_code = NULL WHERE secret_slug = $1",
            secret_slug,
            NEVER_TESTED,
        )
        touched += int(result.split()[-1])
    return touched


async def db_count_secret_usages(
    conn: asyncpg.Connection,
    slug: str,
    *,
    exclude: tuple[str, object] | None = None,
) -> int:
    """Nombre d'objets qui désignent ce secret. `exclude` = (table, id) écarte un
    objet précis (pour la suppression demandée depuis cet objet)."""
    total = 0
    for usage in SECRET_USAGES:
        query = f"SELECT count(*) FROM {usage.table} WHERE {usage.slug_column} = $1"  # noqa: S608
        args: list[object] = [slug]
        if exclude is not None and exclude[0] == usage.table:
            query += " AND id <> $2"
            args.append(exclude[1])
        total += int(await conn.fetchval(query, *args) or 0)
    return total


async def db_list_secret_usages(conn: asyncpg.Connection, slug: str) -> list[dict[str, str]]:
    """Liste des objets qui désignent ce secret : {kind, label}."""
    usages: list[dict[str, str]] = []
    for usage in SECRET_USAGES:
        rows = await conn.fetch(
            f"SELECT {usage.label_column} AS label FROM {usage.table} "  # noqa: S608
            f"WHERE {usage.slug_column} = $1",
            slug,
        )
        usages.extend({"kind": usage.kind, "label": row["label"]} for row in rows)
    return usages


async def db_delete_secret_if_unused(conn: asyncpg.Connection, slug: str) -> str:
    """Supprime le secret s'il n'est utilisé par rien : « deleted », « in_use » ou
    « not_found ». Vérification et suppression dans la même transaction ; la
    contrainte de clé étrangère (ON DELETE RESTRICT) est le garde-fou ultime
    contre un usage apparu entre-temps."""
    async with conn.transaction():
        if await db_count_secret_usages(conn, slug) > 0:
            return "in_use"
        try:
            result: str = await conn.execute("DELETE FROM secrets WHERE slug = $1", slug)
        except asyncpg.ForeignKeyViolationError:
            return "in_use"
        return "deleted" if result != "DELETE 0" else "not_found"
