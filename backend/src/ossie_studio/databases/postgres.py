from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import re
from typing import Any

import asyncpg

from ossie_studio.databases.base import (
    VERIFY_TIMEOUT_SECONDS,
    CatalogColumn,
    CatalogForeignKey,
    CatalogObject,
    CatalogRead,
    DatabaseEngine,
    DbEngineError,
    DbEngineFormSpec,
    DbFormField,
    DbIdentity,
    DbTable,
)

_TLS_MODES = ("prefer", "require", "disable")

# Schémas système toujours écartés.
_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")
_RELKIND = {"r": "table", "p": "table", "v": "view", "m": "materialized-view"}

# (clef primaire, clefs d'unicité, clefs étrangères) d'un objet.
_KeySet = tuple[tuple[str, ...], tuple[tuple[str, ...], ...], tuple[CatalogForeignKey, ...]]

# Correspondance type PostgreSQL (nom de base) → fragment JSON Schema. Le type
# d'origine est toujours ajouté en x-db-type par ailleurs.
_SCALAR_TYPES: dict[str, dict[str, Any]] = {
    "text": {"type": "string"},
    "varchar": {"type": "string"},
    "bpchar": {"type": "string"},
    "char": {"type": "string"},
    "name": {"type": "string"},
    "citext": {"type": "string"},
    "int2": {"type": "integer"},
    "int4": {"type": "integer", "format": "int32"},
    "int8": {"type": "integer", "format": "int64"},
    "numeric": {"type": "number"},
    "float4": {"type": "number", "format": "float"},
    "float8": {"type": "number", "format": "double"},
    "bool": {"type": "boolean"},
    "uuid": {"type": "string", "format": "uuid"},
    "date": {"type": "string", "format": "date"},
    "timestamp": {"type": "string", "format": "date-time"},
    "timestamptz": {"type": "string", "format": "date-time"},
    "time": {"type": "string", "format": "time"},
    "timetz": {"type": "string", "format": "time"},
    "json": {"type": "object"},
    "jsonb": {"type": "object"},
}


def _ch(value: object) -> str:
    """Normalise une colonne PostgreSQL de type `"char"` : asyncpg la rend en
    `bytes` (p. ex. b'e'), on veut la comparer comme texte."""
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return str(value) if value is not None else ""


def _parse_length(fmt: str) -> int | None:
    match = re.search(r"\((\d+)\)\s*$", fmt)
    return int(match.group(1)) if match else None


def _scalar_fragment(typname: str | None) -> dict[str, Any]:
    return dict(_SCALAR_TYPES.get(typname or "", {}))


def _type_fragment(row: asyncpg.Record, enums: dict[int, list[str]]) -> dict[str, Any]:
    """Fragment JSON Schema d'une colonne, selon son type PostgreSQL. Le type
    d'origine (tel que `format_type` l'écrit) est toujours gardé en x-db-type."""
    fmt = row["fmt"]
    frag: dict[str, Any]
    if _ch(row["typtype"]) == "e":  # type énuméré
        frag = {"enum": list(enums.get(row["typoid"], []))}
    elif _ch(row["typcat"]) == "A" and row["elem_oid"]:  # tableau
        if _ch(row["elem_typtype"]) == "e":
            item: dict[str, Any] = {"enum": list(enums.get(row["elem_oid"], []))}
        else:
            item = _scalar_fragment(row["elem_typname"])
        frag = {"type": "array", "items": item}
    else:
        frag = _scalar_fragment(row["typname"])
        if row["typname"] in ("varchar", "bpchar", "char"):
            length = _parse_length(fmt)
            if length is not None:
                frag["maxLength"] = length
    frag["x-db-type"] = fmt
    return frag


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

    async def read_catalog(
        self,
        params: dict[str, str],
        secret_value: str,
        *,
        schemas: list[str] | None,
        exclusions: list[str] | None,
    ) -> CatalogRead:
        conn = await self._connect(params, secret_value)
        read = CatalogRead()
        try:
            targets = await self._target_schemas(conn, schemas, exclusions, read)
            if not targets:
                return read
            objects = await conn.fetch(
                "SELECT c.oid, n.nspname AS schema, c.relname AS name, c.relkind, "
                "obj_description(c.oid) AS comment "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = ANY('{r,p,v,m}') AND n.nspname = ANY($1::text[]) "
                "ORDER BY n.nspname, c.relname",
                targets,
            )
            read.queries += 1
            if not objects:
                return read
            oids = [r["oid"] for r in objects]
            columns = await self._columns(conn, oids, read)
            keys = await self._keys(conn, oids, read)
            deps = await self._view_deps(conn, oids, read)
            for row in objects:
                oid = row["oid"]
                pk, uniques, fks = keys.get(oid, ((), (), ()))
                read.objects.append(
                    CatalogObject(
                        schema=row["schema"],
                        name=row["name"],
                        kind=_RELKIND.get(_ch(row["relkind"]), "table"),
                        columns=tuple(columns.get(oid, ())),
                        description=row["comment"] or "",
                        primary_key=pk,
                        unique_keys=uniques,
                        foreign_keys=fks,
                        depends_on=tuple(deps.get(oid, ())),
                    )
                )
        finally:
            await conn.close()
        return read

    async def _target_schemas(
        self,
        conn: asyncpg.Connection,
        schemas: list[str] | None,
        exclusions: list[str] | None,
        read: CatalogRead,
    ) -> list[str]:
        rows = await conn.fetch(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname <> ALL($1::text[]) AND nspname NOT LIKE 'pg_%' "
            "ORDER BY nspname",
            list(_SYSTEM_SCHEMAS),
        )
        read.queries += 1
        available = [r["nspname"] for r in rows]
        excluded = set(exclusions or ())
        if schemas:
            wanted = [s for s in schemas if s not in excluded]
            return [s for s in wanted if s in available]
        return [s for s in available if s not in excluded]

    async def _columns(
        self, conn: asyncpg.Connection, oids: list[int], read: CatalogRead
    ) -> dict[int, list[CatalogColumn]]:
        rows = await conn.fetch(
            "SELECT a.attrelid AS oid, a.attname AS name, a.attnum AS position, "
            "a.attnotnull AS notnull, "
            "pg_get_expr(ad.adbin, ad.adrelid) AS default_expr, "
            "col_description(a.attrelid, a.attnum) AS comment, "
            "format_type(a.atttypid, a.atttypmod) AS fmt, "
            "t.typtype AS typtype, t.typname AS typname, t.typcategory AS typcat, "
            "t.oid AS typoid, elem.typname AS elem_typname, elem.typtype AS elem_typtype, "
            "elem.oid AS elem_oid "
            "FROM pg_attribute a "
            "JOIN pg_type t ON t.oid = a.atttypid "
            "LEFT JOIN pg_type elem ON elem.oid = t.typelem AND t.typcategory = 'A' "
            "LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum "
            "WHERE a.attrelid = ANY($1::oid[]) AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY a.attrelid, a.attnum",
            oids,
        )
        read.queries += 1
        enums = await self._enum_values(conn, read)
        out: dict[int, list[CatalogColumn]] = {}
        for r in rows:
            fragment = _type_fragment(r, enums)
            out.setdefault(r["oid"], []).append(
                CatalogColumn(
                    name=r["name"],
                    schema=fragment,
                    required=bool(r["notnull"]),
                    position=int(r["position"]),
                    default=r["default_expr"],
                    description=r["comment"] or "",
                )
            )
        return out

    async def _enum_values(
        self, conn: asyncpg.Connection, read: CatalogRead
    ) -> dict[int, list[str]]:
        rows = await conn.fetch(
            "SELECT enumtypid, enumlabel FROM pg_enum ORDER BY enumtypid, enumsortorder"
        )
        read.queries += 1
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["enumtypid"], []).append(r["enumlabel"])
        return out

    async def _keys(
        self, conn: asyncpg.Connection, oids: list[int], read: CatalogRead
    ) -> dict[int, _KeySet]:
        rows = await conn.fetch(
            "SELECT con.conrelid AS oid, con.contype, con.conkey, con.confkey, "
            "rn.nspname AS ref_schema, rc.relname AS ref_name, con.confrelid AS ref_oid "
            "FROM pg_constraint con "
            "LEFT JOIN pg_class rc ON rc.oid = con.confrelid "
            "LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace "
            "WHERE con.conrelid = ANY($1::oid[]) AND con.contype = ANY('{p,u,f}') "
            "ORDER BY con.conname",
            oids,
        )
        read.queries += 1
        ref_oids = {r["ref_oid"] for r in rows if r["ref_oid"]}
        attnames = await self._attnames(conn, list(set(oids) | ref_oids), read)
        result: dict[int, _KeySet] = {}
        for r in rows:
            oid = r["oid"]
            pk, uniques, fks = result.get(oid, ((), (), ()))
            cols = tuple(attnames.get((oid, n), str(n)) for n in r["conkey"])
            contype = _ch(r["contype"])
            if contype == "p":
                pk = cols
            elif contype == "u":
                uniques = (*uniques, cols)
            elif contype == "f":
                ref_cols = tuple(
                    attnames.get((r["ref_oid"], n), str(n)) for n in (r["confkey"] or [])
                )
                fks = (
                    *fks,
                    CatalogForeignKey(
                        columns=cols,
                        ref_schema=r["ref_schema"] or "",
                        ref_object=r["ref_name"] or "",
                        ref_columns=ref_cols,
                    ),
                )
            result[oid] = (pk, uniques, fks)
        return result

    async def _attnames(
        self, conn: asyncpg.Connection, oids: list[int], read: CatalogRead
    ) -> dict[tuple[int, int], str]:
        if not oids:
            return {}
        rows = await conn.fetch(
            "SELECT attrelid AS oid, attnum, attname FROM pg_attribute "
            "WHERE attrelid = ANY($1::oid[]) AND attnum > 0 AND NOT attisdropped",
            oids,
        )
        read.queries += 1
        return {(r["oid"], r["attnum"]): r["attname"] for r in rows}

    async def _view_deps(
        self, conn: asyncpg.Connection, oids: list[int], read: CatalogRead
    ) -> dict[int, list[str]]:
        rows = await conn.fetch(
            "SELECT DISTINCT v.oid AS view_oid, sn.nspname AS dep_schema, sc.relname AS dep_name "
            "FROM pg_rewrite r "
            "JOIN pg_depend d ON d.objid = r.oid AND d.deptype = 'n' "
            "JOIN pg_class sc ON sc.oid = d.refobjid AND sc.relkind = ANY('{r,p,v,m}') "
            "JOIN pg_namespace sn ON sn.oid = sc.relnamespace "
            "JOIN pg_class v ON v.oid = r.ev_class "
            "WHERE v.oid = ANY($1::oid[]) AND d.refobjid <> v.oid "
            "ORDER BY v.oid, sn.nspname, sc.relname",
            oids,
        )
        read.queries += 1
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["view_oid"], []).append(f"{r['dep_schema']}.{r['dep_name']}")
        return out

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
