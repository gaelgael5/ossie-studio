"""0032 : les slugs repris sans translittération sont corrigés, les autres intacts."""

from __future__ import annotations

from pathlib import Path

import asyncpg
from fastapi import FastAPI

from tests.conftest import db_fetchval

SQL = (
    Path(__file__).parents[1] / "src/ossie_studio/db/migrations/0032_slug_accents.sql"
).read_text()


async def _run(app: FastAPI, sql: str) -> None:
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def test_slug_accentue_corrige_slug_choisi_garde(app: FastAPI) -> None:
    # « Sans accès » est semé par l'application : on lui remet le slug fautif de 0031.
    await _run(app, "UPDATE profiles SET slug = 'sans-acc-s' WHERE name = 'Sans accès'")
    await _run(
        app,
        "INSERT INTO profiles (name, description, capabilities, slug) "
        "VALUES ('Équipe', '', '{}', 'mon-choix')",
    )
    await _run(app, SQL)
    assert (
        await db_fetchval(app, "SELECT slug FROM profiles WHERE name = 'Sans accès'")
        == "sans-acces"
    )
    assert await db_fetchval(app, "SELECT slug FROM profiles WHERE name = 'Équipe'") == "mon-choix"
