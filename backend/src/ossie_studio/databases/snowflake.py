from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import asyncio
from typing import Any

from ossie_studio.databases.base import (
    CatalogRead,
    DatabaseEngine,
    DbEngineError,
    DbEngineFormSpec,
    DbFormField,
    DbIdentity,
    DbTable,
)


def _driver() -> Any:
    # Pilote absent de cet environnement : la connexion n'est pas éprouvable ici.
    # La logique devient active dès que snowflake-connector-python est installé.
    try:
        import snowflake.connector  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - pilote non installé
        raise DbEngineError("db_driver_unavailable") from exc
    return snowflake.connector


def _private_key_der(opened_pem: str) -> bytes:  # pragma: no cover - nécessite le pilote
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(opened_pem.encode(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class SnowflakeEngine(DatabaseEngine):
    """Snowflake par paire de clés (le mot de passe seul n'est pas offert : la
    paire de clés est la méthode durable). Lecture de métadonnées uniquement.
    Non éprouvable dans cet environnement (pilote absent)."""

    def _connect(self, params: dict[str, str], secret_value: str) -> Any:  # pragma: no cover
        connector = _driver()
        try:
            return connector.connect(
                account=params.get("account", ""),
                user=params.get("username", ""),
                role=params.get("role") or None,
                warehouse=params.get("warehouse") or None,
                database=params.get("database") or None,
                private_key=_private_key_der(secret_value),
                login_timeout=10,
            )
        except Exception as exc:
            raise _map_error(connector, exc) from exc

    async def verify(self, params: dict[str, str], secret_value: str) -> DbIdentity:
        def _work() -> DbIdentity:  # pragma: no cover - nécessite le pilote
            cn = self._connect(params, secret_value)
            try:
                cur = cn.cursor()
                cur.execute("SELECT CURRENT_VERSION(), CURRENT_USER()")
                version, user = cur.fetchone()
            finally:
                cn.close()
            return DbIdentity(username=str(user), server_version=str(version))

        return await asyncio.to_thread(_work)

    async def list_structure(self, params: dict[str, str], secret_value: str) -> list[DbTable]:
        def _work() -> list[DbTable]:  # pragma: no cover - nécessite le pilote
            cn = self._connect(params, secret_value)
            try:
                cur = cn.cursor()
                cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                )
                rows = cur.fetchall()
            finally:
                cn.close()
            return [DbTable(schema=str(r[0]), name=str(r[1])) for r in rows]

        return await asyncio.to_thread(_work)

    async def read_catalog(
        self,
        params: dict[str, str],
        secret_value: str,
        *,
        schemas: list[str] | None,
        exclusions: list[str] | None,
    ) -> CatalogRead:
        # L'extraction de catalogue Snowflake fait l'objet de son propre ticket
        # (catalogue simulé en test, épreuve réelle sur un compte fourni par Gael).
        raise DbEngineError("db_driver_unavailable")

    def form_spec(self) -> DbEngineFormSpec:
        return DbEngineFormSpec(
            engine="snowflake",
            secret_category="snowflake",
            secret_kind="keyfile",
            target_field="account",
            fields=(
                DbFormField("account", "text"),
                DbFormField("username", "text"),
                DbFormField("role", "text", required=False),
                DbFormField("warehouse", "text", required=False),
                DbFormField("database", "text", required=False),
            ),
        )


def _map_error(connector: Any, exc: Exception) -> DbEngineError:  # pragma: no cover
    message = str(exc).lower()
    if "incorrect username or password" in message or "jwt" in message or "key" in message:
        return DbEngineError("db_key_rejected")
    if "role" in message and "does not exist" in message:
        return DbEngineError("db_role_not_found")
    if "warehouse" in message and "does not exist" in message:
        return DbEngineError("db_warehouse_not_found")
    return DbEngineError("db_host_unreachable")
