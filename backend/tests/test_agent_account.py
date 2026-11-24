"""Compte de l'agent de test posé par `dev-deploy.sh --compte-test`."""

from __future__ import annotations

import asyncpg
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.devtools.agent_account import AGENT_EMAIL, ensure_agent_account
from tests.conftest import bootstrap_admin, db_fetchval, login

PASSWORD = "mot-de-passe-agent-tres-long-0123456789"


async def test_cree_puis_remet_le_compte_admin_et_trace(client: AsyncClient, app: FastAPI) -> None:
    await bootstrap_admin(client)
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        assert await ensure_agent_account(conn, PASSWORD) is True
        # Relancé : même compte, rien de dupliqué.
        assert await ensure_agent_account(conn, PASSWORD) is False
    assert await db_fetchval(app, "SELECT count(*) FROM users WHERE email = $1", AGENT_EMAIL) == 1
    is_admin = await db_fetchval(
        app,
        "SELECT p.is_admin FROM users u JOIN profiles p ON p.id = u.profile_id WHERE u.email = $1",
        AGENT_EMAIL,
    )
    assert is_admin is True
    assert (
        await db_fetchval(
            app, "SELECT count(*) FROM audit_log WHERE object_label = $1", AGENT_EMAIL
        )
        == 2
    )
    await client.post("/api/logout")
    await login(client, AGENT_EMAIL, PASSWORD)
    assert (await client.get("/api/session")).json()["user"]["email"] == AGENT_EMAIL
