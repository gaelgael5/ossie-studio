from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import asyncpg

from ossie_studio.databases.base import (
    VERIFY_TIMEOUT_SECONDS,
    DatabaseEngine,
    DbEngineError,
    DbEngineFormSpec,
    DbFormField,
    DbIdentity,
    DbTable,
)

_TLS_MODES = ("prefer", "require", "disable")


class PostgresEngine(DatabaseEngine):
    """PostgreSQL via asyncpg. Session ouverte en lecture seule
    (`default_transaction_read_only`), aucune requête hors catalogue."""

    async def _connect(self, params: dict[str, str], password: str) -> asyncpg.Connection:
        tls = params.get("tls") or "prefer"
        ssl: object = {"disable": False, "require": "require"}.get(tls, "prefer")
        try:
            return await asyncpg.connect(
                host=params.get("host") or "",
                port=int(params.get("port") or 5432),
                database=params.get("database") or "",
                user=params.get("username") or "",
                password=password,
                ssl=ssl,
                timeout=VERIFY_TIMEOUT_SECONDS,
                server_settings={"default_transaction_read_only": "on"},
            )
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError) as e:
            raise DbEngineError("db_auth_failed") from e
        except asyncpg.InvalidCatalogNameError as e:
            raise DbEngineError("db_not_found") from e
        except (OSError, ConnectionError, TimeoutError, asyncpg.PostgresError) as e:
            raise DbEngineError("db_host_unreachable") from e

    async def verify(self, params: dict[str, str], secret_value: str) -> DbIdentity:
        conn = await self._connect(params, secret_value)
        try:
            version = await conn.fetchval("SELECT version()")
            user = await conn.fetchval("SELECT current_user")
        finally:
            await conn.close()
        return DbIdentity(username=str(user), server_version=str(version))

    async def list_structure(self, params: dict[str, str], secret_value: str) -> list[DbTable]:
        conn = await self._connect(params, secret_value)
        try:
            rows = await conn.fetch(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
        finally:
            await conn.close()
        return [DbTable(schema=r["table_schema"], name=r["table_name"]) for r in rows]

    def form_spec(self) -> DbEngineFormSpec:
        return DbEngineFormSpec(
            engine="postgres",
            secret_category="postgres",
            secret_kind="masked",
            target_field="host",
            fields=(
                DbFormField("host", "text"),
                DbFormField("port", "number", default="5432"),
                DbFormField("database", "text"),
                DbFormField("username", "text"),
                DbFormField("tls", "select", default="prefer", required=False, options=_TLS_MODES),
            ),
        )
