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
from ossie_studio.users.service import fold

log = structlog.get_logger(__name__)


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
