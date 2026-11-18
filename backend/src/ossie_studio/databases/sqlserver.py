from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import asyncio
from typing import Any

from ossie_studio.databases.base import (
    VERIFY_TIMEOUT_SECONDS,
    DatabaseEngine,
    DbEngineError,
    DbEngineFormSpec,
    DbFormField,
    DbIdentity,
    DbTable,
)

_TLS_MODES = ("require", "optional", "disable")


def _driver() -> Any:
    # Pilote absent de cet environnement : la connexion n'est pas éprouvable ici.
    # La logique ci-dessous devient active dès que pyodbc et une instance sont
    # disponibles (cf. ticket : « testé contre une vraie instance en conteneur »).
    try:
        import pyodbc  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - pilote non installé
        raise DbEngineError("db_driver_unavailable") from exc
    return pyodbc


def _conn_str(params: dict[str, str], password: str) -> str:
    encrypt = {"require": "yes", "disable": "no"}.get(params.get("tls") or "require", "yes")
    trust = "yes" if params.get("trust_cert") == "yes" else "no"
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={params.get('host', '')},{params.get('port') or 1433};"
        f"DATABASE={params.get('database', '')};"
        f"UID={params.get('username', '')};PWD={password};"
        f"Encrypt={encrypt};TrustServerCertificate={trust};"
    )


class SqlServerEngine(DatabaseEngine):
    """SQL Server via ODBC (pyodbc). Connexion en lecture seule, lecture du
    catalogue uniquement. Non éprouvable dans cet environnement (pilote absent)."""

    async def verify(self, params: dict[str, str], secret_value: str) -> DbIdentity:
        def _work() -> DbIdentity:  # pragma: no cover - nécessite le pilote
            pyodbc = _driver()
            try:
                cn = pyodbc.connect(
                    _conn_str(params, secret_value),
                    timeout=int(VERIFY_TIMEOUT_SECONDS),
                    readonly=True,
                )
            except pyodbc.Error as exc:
                raise _map_error(exc) from exc
            try:
                cur = cn.cursor()
                version = str(cur.execute("SELECT @@VERSION").fetchval() or "")
                user = str(cur.execute("SELECT SUSER_SNAME()").fetchval() or "")
            finally:
                cn.close()
            first_line = version.splitlines()[0] if version else ""
            return DbIdentity(username=user, server_version=first_line)

        return await asyncio.to_thread(_work)

    async def list_structure(self, params: dict[str, str], secret_value: str) -> list[DbTable]:
        def _work() -> list[DbTable]:  # pragma: no cover - nécessite le pilote
            pyodbc = _driver()
            cn = pyodbc.connect(_conn_str(params, secret_value), readonly=True)
            try:
                cur = cn.cursor()
                rows = cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                ).fetchall()
            finally:
                cn.close()
            return [DbTable(schema=str(r[0]), name=str(r[1])) for r in rows]

        return await asyncio.to_thread(_work)

    def form_spec(self) -> DbEngineFormSpec:
        return DbEngineFormSpec(
            engine="sqlserver",
            secret_category="sqlserver",
            secret_kind="masked",
            target_field="host",
            fields=(
                DbFormField("host", "text"),
                DbFormField("port", "number", default="1433"),
                DbFormField("database", "text"),
                DbFormField("username", "text"),
                DbFormField("tls", "select", default="require", required=False, options=_TLS_MODES),
                DbFormField(
                    "trust_cert", "select", default="no", required=False, options=("no", "yes")
                ),
            ),
        )


def _map_error(exc: Exception) -> DbEngineError:  # pragma: no cover - nécessite le pilote
    message = str(exc).lower()
    if "login failed" in message or "password" in message:
        return DbEngineError("db_auth_failed")
    if "cannot open database" in message:
        return DbEngineError("db_not_found")
    return DbEngineError("db_host_unreachable")
