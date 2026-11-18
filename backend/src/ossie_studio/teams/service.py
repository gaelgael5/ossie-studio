"""Référentiel des équipes et attribution d'équipe à la connexion OIDC.

**Rappel de la décision de Gael : l'équipe sert à l'AFFICHAGE seulement.** Elle ne
donne ni ne retire aucun droit et ne rattache rien. Rien ici ne lit ni n'écrit une
capacité : ce module ne touche jamais `profile_id` ni `capabilities`.
"""

from __future__ import annotations

import re
import uuid

import asyncpg
import structlog

from ossie_studio.models import Team
from ossie_studio.oidc.roles import memberships_from_claims
from ossie_studio.users.service import UserNotFoundError, fold

log = structlog.get_logger(__name__)


class TeamNotFoundError(Exception):
    """Équipe cible absente du référentiel."""


def slugify(name: str) -> str:
    """Slug d'affichage (replié, tirets). Non unique : l'identité d'une équipe est
    son **nom** (valeur exacte du claim), pas son slug."""
    base = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")
    return base


async def db_set_team_claim(
    conn: asyncpg.Connection, connector_id: uuid.UUID, team_claim: str
) -> None:
    await conn.execute(
        "UPDATE oidc_connectors SET team_claim = $2 WHERE id = $1", connector_id, team_claim
    )


async def db_list_teams(conn: asyncpg.Connection) -> list[Team]:
    rows = await conn.fetch("SELECT * FROM teams ORDER BY name")
    return [Team.from_row(row) for row in rows]


async def db_list_teams_for_connector(
    conn: asyncpg.Connection, connector_id: uuid.UUID
) -> list[Team]:
    """Équipes venues de CE connecteur (origin 'oidc', rattachées au connecteur)."""
    rows = await conn.fetch(
        "SELECT * FROM teams WHERE source_connector_id = $1 ORDER BY name", connector_id
    )
    return [Team.from_row(row) for row in rows]


async def db_get_or_create_team_by_name(
    conn: asyncpg.Connection, name: str, *, origin: str, source_connector_id: uuid.UUID | None
) -> uuid.UUID:
    """Trouve l'équipe par son **nom exact** (casse et espaces), ou la crée.

    `ON CONFLICT (name) DO UPDATE` est un no-op qui permet au RETURNING de rendre
    l'id d'une équipe déjà connue sans toucher son origine ni son connecteur.
    """
    team_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO teams (name, slug, origin, source_connector_id) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        name,
        slugify(name),
        origin,
        source_connector_id,
    )
    return team_id


async def db_create_team_manual(conn: asyncpg.Connection, name: str) -> Team:
    """Crée une équipe à la main (origine « créée à la main »). Idempotent sur le
    nom exact : si le nom existe déjà (même venu de l'OIDC), on rend l'équipe
    existante — elle est dans le référentiel, c'est ce qu'on veut."""
    team_id = await db_get_or_create_team_by_name(
        conn, name, origin="manual", source_connector_id=None
    )
    row = await conn.fetchrow("SELECT * FROM teams WHERE id = $1", team_id)
    assert row is not None
    return Team.from_row(row)


async def db_assign_team(
    conn: asyncpg.Connection, user_id: uuid.UUID, team_id: uuid.UUID
) -> uuid.UUID | None:
    """Assigne une équipe à la main (SURCHARGE) : marque `team_pinned`, pour que le
    claim ne la remplace plus. Rend l'ancienne équipe. Une personne n'a qu'une
    équipe : assigner remplace la précédente. Affichage seulement : aucun droit
    n'est touché (ce service n'écrit jamais profile_id ni capabilities)."""
    async with conn.transaction():
        current = await conn.fetchrow("SELECT team_id FROM users WHERE id = $1", user_id)
        if current is None:
            raise UserNotFoundError
        if await conn.fetchval("SELECT 1 FROM teams WHERE id = $1", team_id) is None:
            raise TeamNotFoundError
        old_team_id: uuid.UUID | None = current["team_id"]
        await conn.execute(
            "UPDATE users SET team_id = $2, team_pinned = true WHERE id = $1", user_id, team_id
        )
    return old_team_id


async def db_remove_team_pin(conn: asyncpg.Connection, user_id: uuid.UUID) -> bool:
    """Retire la marque : à la connexion suivante, le claim reprend la main. On
    laisse l'équipe actuelle en place jusque-là."""
    result: str = await conn.execute(
        "UPDATE users SET team_pinned = false WHERE id = $1", user_id
    )
    return result != "UPDATE 0"


async def apply_team_mapping(
    conn: asyncpg.Connection,
    connector_id: uuid.UUID,
    team_claim: str,
    claims: object,
    user_id: uuid.UUID,
) -> None:
    """À la connexion : le claim désigne l'équipe de l'utilisateur.

    - Une personne appartient à **une seule** équipe : si le claim porte plusieurs
      valeurs, la **première** l'emporte (le cas est journalisé).
    - Une valeur jamais vue **crée** l'équipe (origine OIDC), rattachée au connecteur.
    - Claim absent, vide ou de forme inattendue : l'équipe actuelle est **conservée**.
    - Une équipe **fixée à la main** (`team_pinned`) n'est jamais remplacée.
    """
    memberships = memberships_from_claims(claims, team_claim)
    if not memberships:
        return  # claim absent / vide / forme inattendue → équipe conservée
    value = memberships[0]
    if len(memberships) > 1:
        log.info(
            "oidc_team_claim_multiple_values",
            user_id=str(user_id),
            kept=value,
            count=len(memberships),
        )
    row = await conn.fetchrow("SELECT team_id, team_pinned FROM users WHERE id = $1", user_id)
    if row is None or row["team_pinned"]:
        return
    target = await db_get_or_create_team_by_name(
        conn, value, origin="oidc", source_connector_id=connector_id
    )
    if target == row["team_id"]:
        return
    await conn.execute("UPDATE users SET team_id = $2 WHERE id = $1", user_id, target)
    log.info(
        "oidc_team_applied",
        user_id=str(user_id),
        old_team=str(row["team_id"]) if row["team_id"] else None,
        new_team=str(target),
        new_team_name=value,
    )
