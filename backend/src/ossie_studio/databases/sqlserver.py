from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import asyncio
from typing import Any

from ossie_studio.databases.base import (
    VERIFY_TIMEOUT_SECONDS,
    CatalogColumn,
    CatalogObject,
    CatalogRead,
    DatabaseEngine,
    DbEngineError,
    DbEngineFormSpec,
    DbFormField,
    DbIdentity,
    DbTable,
)
from ossie_studio.databases.catalog_build import fk_groups, grouped_keys

_TLS_MODES = ("require", "optional", "disable")

_STRING_TYPES = ("nvarchar", "varchar", "char", "nchar", "text", "ntext")


def _ss_db_type(
    data_type: str, max_len: int | None, precision: int | None, scale: int | None
) -> str:
    dt = data_type.lower()
    if dt in _STRING_TYPES:
        if max_len == -1:
            return f"{dt}(max)"
        return f"{dt}({max_len})" if max_len else dt
    if dt in ("decimal", "numeric"):
        return f"{dt}({precision or 18},{scale or 0})"
    return dt


def _ss_type_fragment(
    data_type: str, max_len: int | None, precision: int | None, scale: int | None
) -> dict[str, Any]:
    """Fragment JSON Schema d'une colonne SQL Server. Type d'origine en x-db-type."""
    dt = data_type.lower()
    frag: dict[str, Any]
    if dt in _STRING_TYPES:
        frag = {"type": "string"}
        if max_len is not None and max_len > 0:
            frag["maxLength"] = max_len
    elif dt == "int":
        frag = {"type": "integer", "format": "int32"}
    elif dt == "bigint":
        frag = {"type": "integer", "format": "int64"}
    elif dt in ("smallint", "tinyint"):
        frag = {"type": "integer"}
    elif dt in ("decimal", "numeric"):
        frag = {"type": "integer"} if (scale or 0) == 0 else {"type": "number"}
    elif dt in ("money", "smallmoney", "float", "real"):
        frag = {"type": "number"}
    elif dt == "bit":
        frag = {"type": "boolean"}
    elif dt == "uniqueidentifier":
        frag = {"type": "string", "format": "uuid"}
    elif dt == "date":
        frag = {"type": "string", "format": "date"}
    elif dt in ("datetime", "datetime2", "smalldatetime", "datetimeoffset"):
        frag = {"type": "string", "format": "date-time"}
    elif dt == "time":
        frag = {"type": "string", "format": "time"}
    elif dt in ("binary", "varbinary", "image"):
        frag = {"type": "string", "contentEncoding": "base64"}
    elif dt == "xml":
        frag = {"type": "string"}
    else:
        frag = {}
    frag["x-db-type"] = _ss_db_type(data_type, max_len, precision, scale)
    return frag


def _required(nullable: Any) -> bool:
    if isinstance(nullable, str):
        return nullable.upper() == "NO"
    return not bool(nullable)


def build_catalog(
    *,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    primary_keys: list[dict[str, Any]] | None = None,
    unique_keys: list[dict[str, Any]] | None = None,
    foreign_keys: list[dict[str, Any]] | None = None,
    schemas: list[str] | None = None,
    exclusions: list[str] | None = None,
    queries: int = 0,
    errors: list[tuple[str, str]] | None = None,
) -> CatalogRead:
    """Construit le catalogue normalisé depuis des lignes de catalogue SQL Server
    (sys.* / INFORMATION_SCHEMA). **Pur** : testable sans instance."""
    include = set(schemas) if schemas else None
    exclude = set(exclusions or ())

    def kept(schema: str) -> bool:
        return (include is None or schema in include) and schema not in exclude

    cols_by_obj: dict[tuple[str, str], list[CatalogColumn]] = {}
    for c in columns:
        schema, table = str(c["schema"]), str(c["table"])
        if not kept(schema):
            continue
        fragment = _ss_type_fragment(
            str(c["data_type"]), c.get("max_len"), c.get("precision"), c.get("scale")
        )
        if c.get("computed"):
            fragment["x-computed"] = str(c["computed"])
        if c.get("identity"):
            fragment["x-identity"] = True
        cols_by_obj.setdefault((schema, table), []).append(
            CatalogColumn(
                name=str(c["name"]),
                schema=fragment,
                required=_required(c.get("is_nullable", "YES")),
                position=int(c.get("position", 0)),
                default=c.get("default"),
                description=str(c.get("comment") or ""),
            )
        )

    pk = grouped_keys([r for r in (primary_keys or []) if kept(str(r["schema"]))], "constraint")
    uq = grouped_keys([r for r in (unique_keys or []) if kept(str(r["schema"]))], "constraint")
    fks_by_obj = fk_groups(
        [r for r in (foreign_keys or []) if kept(str(r["schema"]))], enforced=True
    )

    objects: list[CatalogObject] = []
    for t in tables:
        schema, name = str(t["schema"]), str(t["name"])
        if not kept(schema):
            continue
        key = (schema, name)
        columns_sorted = sorted(cols_by_obj.get(key, []), key=lambda c: c.position)
        extra: dict[str, Any] = {}
        kind = str(t.get("kind", "table"))
        if kind == "view" and t.get("indexed"):
            extra["x-indexed"] = True
        objects.append(
            CatalogObject(
                schema=schema,
                name=name,
                kind=kind,
                columns=tuple(columns_sorted),
                description=str(t.get("comment") or ""),
                primary_key=tuple(pk.get(key, [[]])[0]) if key in pk else (),
                unique_keys=tuple(tuple(u) for u in uq.get(key, [])),
                foreign_keys=tuple(fks_by_obj.get(key, [])),
                extra=extra,
            )
        )
    return CatalogRead(objects=objects, errors=list(errors or []), queries=queries)


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

    async def read_catalog(
        self,
        params: dict[str, str],
        secret_value: str,
        *,
        schemas: list[str] | None,
        exclusions: list[str] | None,
    ) -> CatalogRead:
        def _work() -> CatalogRead:  # pragma: no cover - nécessite le pilote et une instance
            pyodbc = _driver()
            cn = pyodbc.connect(_conn_str(params, secret_value), readonly=True)
            queries = 0
            try:
                cur = cn.cursor()

                def q(sql: str) -> list[dict[str, Any]]:
                    nonlocal queries
                    queries += 1
                    cur.execute(sql)
                    names = [str(d[0]).lower() for d in cur.description]
                    return [dict(zip(names, row, strict=False)) for row in cur.fetchall()]

                tables = [
                    {"schema": r["schema_name"], "name": r["name"], "kind": r["kind"],
                     "comment": r.get("comment"), "indexed": bool(r.get("indexed"))}
                    for r in q(
                        "SELECT s.name AS schema_name, o.name AS name, "
                        "CASE WHEN o.type='V' THEN 'view' ELSE 'table' END AS kind, "
                        "CAST(ep.value AS nvarchar(max)) AS comment, "
                        "CASE WHEN o.type='V' AND EXISTS (SELECT 1 FROM sys.indexes i "
                        "WHERE i.object_id=o.object_id AND i.index_id>0) "
                        "THEN 1 ELSE 0 END AS indexed "
                        "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                        "LEFT JOIN sys.extended_properties ep ON ep.major_id=o.object_id "
                        "AND ep.minor_id=0 AND ep.class=1 AND ep.name='MS_Description' "
                        "WHERE o.type IN ('U','V')"
                    )
                ]
                columns = [
                    {"schema": r["schema_name"], "table": r["table_name"], "name": r["column_name"],
                     "position": r.get("pos", 0), "is_nullable": r.get("is_nullable"),
                     "default": r.get("default_def"), "data_type": r.get("data_type", ""),
                     "max_len": r.get("char_len"), "precision": r.get("precision"),
                     "scale": r.get("scale"), "comment": r.get("comment"),
                     "computed": r.get("computed"), "identity": bool(r.get("is_identity"))}
                    for r in q(
                        "SELECT s.name AS schema_name, o.name AS table_name, "
                        "c.name AS column_name, "
                        "c.column_id AS pos, c.is_nullable, dc.definition AS default_def, "
                        "ty.name AS data_type, "
                        "CASE WHEN ty.name IN ('nvarchar','nchar') AND c.max_length<>-1 "
                        "THEN c.max_length/2 ELSE c.max_length END AS char_len, "
                        "c.precision AS precision, c.scale AS scale, "
                        "CAST(ep.value AS nvarchar(max)) AS comment, "
                        "cc.definition AS computed, c.is_identity "
                        "FROM sys.columns c "
                        "JOIN sys.objects o ON o.object_id=c.object_id "
                        "JOIN sys.schemas s ON s.schema_id=o.schema_id "
                        "JOIN sys.types ty ON ty.user_type_id=c.user_type_id "
                        "LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id "
                        "LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id "
                        "AND cc.column_id=c.column_id "
                        "LEFT JOIN sys.extended_properties ep ON ep.major_id=c.object_id "
                        "AND ep.minor_id=c.column_id AND ep.class=1 AND ep.name='MS_Description' "
                        "WHERE o.type IN ('U','V')"
                    )
                ]
                key_rows = q(
                    "SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, "
                    "ic.key_ordinal AS seq, kc.name AS constraint_name, kc.type AS ctype "
                    "FROM sys.key_constraints kc "
                    "JOIN sys.tables t ON t.object_id=kc.parent_object_id "
                    "JOIN sys.schemas s ON s.schema_id=t.schema_id "
                    "JOIN sys.index_columns ic ON ic.object_id=kc.parent_object_id "
                    "AND ic.index_id=kc.unique_index_id "
                    "JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id "
                    "WHERE kc.type IN ('PK','UQ')"
                )
                fks = [
                    {"schema": r["schema_name"], "table": r["table_name"],
                     "column": r["column_name"],
                     "ref_schema": r["ref_schema"], "ref_table": r["ref_table"],
                     "ref_column": r["ref_column"], "seq": r.get("seq", 0), "fk_name": r["fk_name"]}
                    for r in q(
                        "SELECT s.name AS schema_name, t.name AS table_name, "
                        "cpa.name AS column_name, rs.name AS ref_schema, rt.name AS ref_table, "
                        "cref.name AS ref_column, fkc.constraint_column_id AS seq, "
                        "fk.name AS fk_name "
                        "FROM sys.foreign_keys fk "
                        "JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id "
                        "JOIN sys.tables t ON t.object_id=fk.parent_object_id "
                        "JOIN sys.schemas s ON s.schema_id=t.schema_id "
                        "JOIN sys.columns cpa ON cpa.object_id=fkc.parent_object_id "
                        "AND cpa.column_id=fkc.parent_column_id "
                        "JOIN sys.tables rt ON rt.object_id=fk.referenced_object_id "
                        "JOIN sys.schemas rs ON rs.schema_id=rt.schema_id "
                        "JOIN sys.columns cref ON cref.object_id=fkc.referenced_object_id "
                        "AND cref.column_id=fkc.referenced_column_id"
                    )
                ]
            finally:
                cn.close()

            def _norm(r: dict[str, Any]) -> dict[str, Any]:
                return {"schema": r["schema_name"], "table": r["table_name"],
                        "column": r["column_name"], "seq": r.get("seq", 0),
                        "constraint": r.get("constraint_name")}

            pks = [_norm(r) for r in key_rows if str(r.get("ctype", "")).strip() == "PK"]
            uqs = [_norm(r) for r in key_rows if str(r.get("ctype", "")).strip() == "UQ"]
            return build_catalog(
                tables=tables, columns=columns, primary_keys=pks, unique_keys=uqs,
                foreign_keys=fks, schemas=schemas, exclusions=exclusions, queries=queries,
            )

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
