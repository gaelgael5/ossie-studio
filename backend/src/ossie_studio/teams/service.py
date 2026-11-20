"""Référentiel des équipes et rattachements personne↔équipe.

**Appartenance multiple** (décision de Gael, 20 novembre 2026) : une personne peut
appartenir à plusieurs équipes ; les rattachements vivent dans `user_teams`, chacun
avec son **origine** (`oidc` ou `manual`). Un rattachement manuel ne doit jamais
être retiré par une synchronisation OIDC.

Ce module gère le **rattachement** et sa synchro OIDC ; il ne lit ni n'écrit aucune
capacité (`profile_id`, `capabilities`). L'effet des équipes sur les droits — une
équipe qui porte un profil — est un ticket suivant et vit ailleurs (contrôle
d'accès), pas ici.
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


async def db_add_user_team(
    conn: asyncpg.Connection, user_id: uuid.UUID, team_id: uuid.UUID
) -> None:
    """Ajoute un rattachement **manuel** d'une personne à une équipe (surcharge :
    un rattachement manuel n'est jamais retiré par la synchro OIDC). Idempotent ; un
    rattachement déjà présent (même venu de l'OIDC) devient manuel. Lève
    UserNotFoundError / TeamNotFoundError si la personne ou l'équipe n'existe pas."""
    async with conn.transaction():
        if await conn.fetchval("SELECT 1 FROM users WHERE id = $1", user_id) is None:
            raise UserNotFoundError
        if await conn.fetchval("SELECT 1 FROM teams WHERE id = $1", team_id) is None:
            raise TeamNotFoundError
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual') "
            "ON CONFLICT (user_id, team_id) DO UPDATE SET origin = 'manual'",
            user_id,
            team_id,
        )


async def db_remove_user_team(
    conn: asyncpg.Connection, user_id: uuid.UUID, team_id: uuid.UUID
) -> bool:
    """Retire un rattachement d'une personne à une équipe (décision humaine, quelle
    que soit son origine). Rend True si un rattachement a été retiré, False sinon."""
    result: str = await conn.execute(
        "DELETE FROM user_teams WHERE user_id = $1 AND team_id = $2", user_id, team_id
    )
    return result != "DELETE 0"


async def db_teams_for_users(
    conn: asyncpg.Connection, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """Rattachements de plusieurs personnes : `{user_id: [{id, name, origin}]}`,
    trié par nom d'équipe. `origin` est l'origine du **rattachement** (`oidc` |
    `manual`), à ne pas confondre avec l'origine de l'équipe elle-même."""
    out: dict[uuid.UUID, list[dict[str, str]]] = {}
    if not user_ids:
        return out
    rows = await conn.fetch(
        "SELECT ut.user_id, t.id, t.name, ut.origin "
        "FROM user_teams ut JOIN teams t ON t.id = ut.team_id "
        "WHERE ut.user_id = ANY($1::uuid[]) ORDER BY t.name",
        list(user_ids),
    )
    for row in rows:
        out.setdefault(row["user_id"], []).append(
            {"id": str(row["id"]), "name": row["name"], "origin": row["origin"]}
        )
    return out


async def apply_team_mapping(
    conn: asyncpg.Connection,
    connector_id: uuid.UUID,
    team_claim: str,
    claims: object,
    user_id: uuid.UUID,
) -> None:
    """À la connexion, le claim désigne l'équipe OIDC de la personne.

    Ce ticket garde le comportement d'aujourd'hui pour l'OIDC — **une seule**
    équipe OIDC (la **première** valeur si le claim en porte plusieurs ; le cas est
    journalisé) ; la synchro multi-valuée est un ticket suivant. Les rattachements
    vivent désormais dans `user_teams`.

    - Une personne qui a **au moins un** rattachement **manuel** est considérée comme
      surchargée à la main : l'OIDC ne change rien (préserve l'ancien « épinglé »).
    - Une valeur jamais vue **crée** l'équipe (origine OIDC), rattachée au connecteur.
    - Claim absent, vide ou de forme inattendue : rattachements **conservés**.
    """
    memberships = memberships_from_claims(claims, team_claim)
    if not memberships:
        return  # claim absent / vide / forme inattendue → rattachements conservés
    value = memberships[0]
    if len(memberships) > 1:
        log.info(
            "oidc_team_claim_multiple_values",
            user_id=str(user_id),
            kept=value,
            count=len(memberships),
        )
    if await conn.fetchval("SELECT 1 FROM users WHERE id = $1", user_id) is None:
        return
    # Surcharge manuelle : l'OIDC n'intervient pas (préserve « fixé à la main »).
    if await conn.fetchval(
        "SELECT 1 FROM user_teams WHERE user_id = $1 AND origin = 'manual' LIMIT 1", user_id
    ):
        return
    target = await db_get_or_create_team_by_name(
        conn, value, origin="oidc", source_connector_id=connector_id
    )
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'oidc') "
            "ON CONFLICT (user_id, team_id) DO NOTHING",
            user_id,
            target,
        )
        # Une seule équipe OIDC (comportement actuel) : on retire les autres OIDC.
        await conn.execute(
            "DELETE FROM user_teams WHERE user_id = $1 AND origin = 'oidc' AND team_id <> $2",
            user_id,
            target,
        )
    log.info(
        "oidc_team_applied", user_id=str(user_id), new_team=str(target), new_team_name=value
    )
