"""Compte local de l'agent de test, posé par `dev-deploy.sh --compte-test`.

Gael (24/11/2026) a choisi que l'agent teste les pages avec le navigateur
headless de la machine de test sous un compte qui lui est propre, créé par le
script de déploiement (jamais à la main) : `agent-test@ossie.local`, profil
administrateur. Le mot de passe est généré sur la machine et lu sur l'entrée
standard — il ne passe ni en argument (visible dans `ps`) ni dans un journal.

Idempotent : relancé, il remet le même compte au même mot de passe. Chaque
passage laisse une ligne dans le journal d'audit (acteur « système »).

Usage (dans le conteneur backend) :
    printf '%s' "$MOT_DE_PASSE" | python -m ossie_studio.devtools.agent_account
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg

from ossie_studio.audit.service import CREATED, UPDATED, AuditEntry, record
from ossie_studio.config import Settings
from ossie_studio.db import create_pool
from ossie_studio.models import ROLE_ADMIN
from ossie_studio.security import hash_password
from ossie_studio.users.service import search_key

AGENT_EMAIL = "agent-test@ossie.local"
AGENT_NAME = "Agent de test"
MIN_LENGTH = 24


async def ensure_agent_account(conn: asyncpg.Connection, password: str) -> bool:
    """Crée ou remet le compte ; True s'il vient d'être créé."""
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, password_hash, role, profile_id, profile_pinned,
                               display_name, search_key)
            VALUES ($1, $2, $3, (SELECT id FROM profiles WHERE is_admin), true, $4, $5)
            ON CONFLICT (email) DO UPDATE
               SET password_hash = EXCLUDED.password_hash,
                   role = EXCLUDED.role,
                   profile_id = EXCLUDED.profile_id,
                   profile_pinned = true
            RETURNING id, (xmax = 0) AS inserted
            """,
            AGENT_EMAIL,
            hash_password(password),
            ROLE_ADMIN,
            AGENT_NAME,
            search_key(AGENT_NAME, AGENT_EMAIL),
        )
        assert row is not None
        inserted = bool(row["inserted"])
        await record(
            conn,
            None,
            AuditEntry(
                CREATED if inserted else UPDATED,
                "user",
                str(row["id"]),
                AGENT_EMAIL,
                {"source": "dev-deploy --compte-test"},
            ),
        )
    return inserted


async def _main() -> int:
    password = sys.stdin.read().strip()
    if len(password) < MIN_LENGTH:
        print("mot de passe absent ou trop court sur l'entrée standard", file=sys.stderr)
        return 2
    # Même initialisation de connexion que l'application (codec jsonb du
    # journal d'audit) : une connexion nue refuse le détail en dict.
    pool = await create_pool(Settings().asyncpg_dsn())  # type: ignore[call-arg]
    try:
        async with pool.acquire() as conn:
            created = await ensure_agent_account(conn, password)
    finally:
        await pool.close()
    print(f"compte {AGENT_EMAIL} {'créé' if created else 'remis à jour'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
