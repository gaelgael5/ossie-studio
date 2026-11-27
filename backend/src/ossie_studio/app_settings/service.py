"""Paramètres généraux de l'application, réglables sur la page « Paramètres »
(epic « Primitives MCP — écriture commune par genre », ticket ae8fc4b6).

Gael : « il faut prévoir un paramètre dans la config, et accessible dans une page ».
Chaque paramètre a une **valeur par défaut** dans la configuration du serveur
(`config.py`, variable d'environnement) ; une valeur posée sur la page est gardée en
base (`app_settings`) et **prime** sur la configuration. Elle est relue à chaque
usage : un changement vaut dès la requête suivante, sans redémarrage. Revenir à la
valeur par défaut supprime la ligne.

Premier paramètre : la taille maximale d'un document écrit par un agent
(`document_max_bytes`, 200 Ko par défaut — décision de Gael), lue par
`write_documents`, `patch_document`, `list_write_kinds` et `describe_standard`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ossie_studio.config import Settings

DOCUMENT_MAX_BYTES = "document_max_bytes"


@dataclass(frozen=True)
class SettingDef:
    """Un paramètre : sa clé, ses bornes (entier) et sa valeur par défaut, lue dans
    la configuration."""

    key: str
    minimum: int
    maximum: int
    default: Callable[[Settings], int]


DEFINITIONS: dict[str, SettingDef] = {
    DOCUMENT_MAX_BYTES: SettingDef(
        DOCUMENT_MAX_BYTES,
        # Au moins 1 Ko (en deçà, aucune écriture ne passerait), au plus 10 Mo (au-delà,
        # un agent pourrait charger le serveur : c'est ce que la limite empêche).
        minimum=1024,
        maximum=10 * 1024 * 1024,
        default=lambda settings: settings.document_max_bytes,
    ),
}


class SettingError(Exception):
    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SettingValue:
    key: str
    value: int
    default: int
    minimum: int
    maximum: int
    # « page » : posée sur la page ; « config » : la valeur par défaut du serveur.
    source: str
    updated_at: datetime | None = None
    updated_by: str | None = None


def definition(key: str) -> SettingDef:
    found = DEFINITIONS.get(key)
    if found is None:
        raise SettingError("setting_not_found", key=key)
    return found


def check_value(found: SettingDef, value: Any) -> int:
    """Un entier entre les bornes du paramètre ; sinon refus nommé."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingError(
            "setting_value_invalid", key=found.key, minimum=found.minimum, maximum=found.maximum
        )
    if not found.minimum <= value <= found.maximum:
        raise SettingError(
            "setting_value_invalid", key=found.key, minimum=found.minimum, maximum=found.maximum
        )
    return value


async def _stored(conn: asyncpg.Connection, key: str) -> asyncpg.Record | None:
    record: asyncpg.Record | None = await conn.fetchrow(
        "SELECT s.value, s.updated_at, u.email AS updated_by "
        "FROM app_settings s LEFT JOIN users u ON u.id = s.updated_by WHERE s.key = $1",
        key,
    )
    return record


async def current(conn: asyncpg.Connection, settings: Settings, key: str) -> SettingValue:
    """La valeur en vigueur : celle de la page si elle est posée (et valide), sinon
    celle de la configuration."""
    found = definition(key)
    default = found.default(settings)
    row = await _stored(conn, key)
    if row is not None:
        try:
            value = check_value(found, row["value"])
        except SettingError:
            value = None  # une valeur devenue hors bornes : la configuration reprend
        if value is not None:
            return SettingValue(
                key, value, default, found.minimum, found.maximum, "page",
                row["updated_at"], row["updated_by"],
            )  # fmt: skip
    return SettingValue(key, default, default, found.minimum, found.maximum, "config")


async def value_of(conn: asyncpg.Connection, settings: Settings, key: str) -> int:
    return (await current(conn, settings, key)).value


async def document_max_bytes(conn: asyncpg.Connection, settings: Settings) -> int:
    """La taille maximale d'un document écrit par un agent, en vigueur."""
    return await value_of(conn, settings, DOCUMENT_MAX_BYTES)


async def list_settings(conn: asyncpg.Connection, settings: Settings) -> list[SettingValue]:
    return [await current(conn, settings, key) for key in DEFINITIONS]


async def db_set(conn: asyncpg.Connection, key: str, value: int, user_id: uuid.UUID | None) -> None:
    await conn.execute(
        "INSERT INTO app_settings (key, value, updated_at, updated_by) "
        "VALUES ($1, $2::jsonb, now(), $3) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), "
        "updated_by = EXCLUDED.updated_by",
        key,
        value,
        user_id,
    )


async def db_reset(conn: asyncpg.Connection, key: str) -> bool:
    result: str = await conn.execute("DELETE FROM app_settings WHERE key = $1", key)
    return result != "DELETE 0"
