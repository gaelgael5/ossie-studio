from __future__ import annotations

import json

import asyncpg


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb ↔ dict/list transparents : on lit et écrit du JSON natif, pas du texte.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_connection)
