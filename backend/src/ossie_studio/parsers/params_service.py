from __future__ import annotations

import asyncpg


async def db_get_plugin_params(conn: asyncpg.Connection) -> dict[str, dict[str, str]]:
    """Toutes les valeurs de paramètres rangées, regroupées par plugin :
    {plugin_id: {slug: valeur}}."""
    rows = await conn.fetch("SELECT plugin_id, slug, value FROM plugin_parameters")
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row["plugin_id"], {})[row["slug"]] = row["value"]
    return out


async def db_set_plugin_param(
    conn: asyncpg.Connection, *, plugin_id: str, slug: str, value: str
) -> None:
    await conn.execute(
        "INSERT INTO plugin_parameters (plugin_id, slug, value) VALUES ($1, $2, $3) "
        "ON CONFLICT (plugin_id, slug) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        plugin_id,
        slug,
        value,
    )
