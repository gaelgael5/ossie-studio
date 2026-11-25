from __future__ import annotations

# ruff: noqa: S106  (secret_kind / secret_category : des libellés, pas des secrets)
import asyncio
from typing import Any

from ossie_studio.databases.base import (
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

# Longueur « sans limite » de VARCHAR dans Snowflake : pas de maxLength alors.
_SF_MAX_TEXT = 16777216
_SF_KIND = {
    "BASE TABLE": "table",
    "TABLE": "table",
    "VIEW": "view",
    "MATERIALIZED VIEW": "materialized-view",
    "DYNAMIC TABLE": "dynamic-table",
}


def _sf_db_type(
    data_type: str, char_len: int | None, precision: int | None, scale: int | None
) -> str:
    dt = data_type.upper()
    if dt in ("TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER"):
        return f"VARCHAR({char_len})" if char_len and char_len != _SF_MAX_TEXT else "VARCHAR"
    if dt in ("NUMBER", "DECIMAL", "NUMERIC"):
        return f"NUMBER({precision or 38},{scale or 0})"
    return dt


def _sf_type_fragment(
    data_type: str, char_len: int | None, precision: int | None, scale: int | None
) -> dict[str, Any]:
    """Fragment JSON Schema d'une colonne Snowflake. Le type d'origine est gardé
    en x-db-type."""
    dt = data_type.upper()
    frag: dict[str, Any]
    if dt in ("TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER"):
        frag = {"type": "string"}
        if char_len and char_len != _SF_MAX_TEXT:
            frag["maxLength"] = char_len
    elif dt in ("NUMBER", "DECIMAL", "NUMERIC"):
        frag = {"type": "integer"} if (scale or 0) == 0 else {"type": "number"}
    elif dt in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT"):
        frag = {"type": "integer"}
    elif dt in ("FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"):
        frag = {"type": "number"}
    elif dt == "BOOLEAN":
        frag = {"type": "boolean"}
    elif dt == "DATE":
        frag = {"type": "string", "format": "date"}
    elif dt in ("TIMESTAMP", "DATETIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
        frag = {"type": "string", "format": "date-time"}
    elif dt == "TIME":
        frag = {"type": "string", "format": "time"}
    elif dt in ("VARIANT", "OBJECT"):
        frag = {"type": "object", "x-semi-structured": True}
    elif dt == "ARRAY":
        frag = {"type": "array"}  # Snowflake : tableau sans structure d'items.
    elif dt == "BINARY":
        frag = {"type": "string"}
    else:
        frag = {}
    frag["x-db-type"] = _sf_db_type(data_type, char_len, precision, scale)
    return frag


TagMap = dict[str, str]


def _tags_by_target(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, ...], TagMap], dict[tuple[str, str, str], TagMap]]:
    """Range les lignes de TAG_REFERENCES par cible (6a8c1828). Objets : clé
    () pour la base, (schéma,) pour un schéma, (schéma, objet) pour une table ou
    une vue ; colonnes : (schéma, objet, colonne). Le tag est nommé qualifié
    (BASE.SCHEMA.TAG) : deux tags homonymes de schémas différents ne se
    confondent pas, et les tags système (SNOWFLAKE.CORE.*) restent lisibles."""
    objects: dict[tuple[str, ...], TagMap] = {}
    cols: dict[tuple[str, str, str], TagMap] = {}
    for r in rows:
        tag = ".".join(str(r.get(k) or "") for k in ("tag_database", "tag_schema", "tag_name"))
        value = str(r.get("tag_value") or "")
        domain = str(r.get("domain") or "").upper()
        schema, name = str(r.get("object_schema") or ""), str(r.get("object_name") or "")
        if domain == "COLUMN":
            cols.setdefault((schema, name, str(r.get("column_name") or "")), {})[tag] = value
        elif domain == "DATABASE":
            objects.setdefault((), {})[tag] = value
        elif domain == "SCHEMA":
            # Pour un tag de schéma, Snowflake met le schéma dans OBJECT_NAME.
            objects.setdefault((name or schema,), {})[tag] = value
        elif domain == "TABLE":  # tables et vues
            objects.setdefault((schema, name), {})[tag] = value
    return objects, cols


def _effective_tags(objects: dict[tuple[str, ...], TagMap], schema: str, name: str) -> TagMap:
    """Tags d'un objet, hérités compris, comme Snowflake les applique : base,
    puis schéma, puis l'objet ; le tag posé au plus près gagne."""
    merged: TagMap = {}
    for key in ((), (schema,), (schema, name)):
        merged.update(objects.get(key, {}))
    return dict(sorted(merged.items()))


def build_catalog(
    *,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    primary_keys: list[dict[str, Any]] | None = None,
    unique_keys: list[dict[str, Any]] | None = None,
    foreign_keys: list[dict[str, Any]] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
    tags: list[dict[str, Any]] | None = None,
    schemas: list[str] | None = None,
    exclusions: list[str] | None = None,
    queries: int = 0,
    errors: list[tuple[str, str]] | None = None,
) -> CatalogRead:
    """Construit le catalogue normalisé depuis des lignes de catalogue Snowflake
    (INFORMATION_SCHEMA + SHOW …). **Pur** : testable sans instance."""
    include = set(schemas) if schemas else None
    exclude = set(exclusions or ())

    def kept(schema: str) -> bool:
        return (include is None or schema in include) and schema not in exclude

    object_tags, column_tags = _tags_by_target(tags or [])

    cols_by_obj: dict[tuple[str, str], list[CatalogColumn]] = {}
    for c in columns:
        schema, table = str(c["schema"]), str(c["table"])
        if not kept(schema):
            continue
        fragment = _sf_type_fragment(
            str(c["data_type"]), c.get("char_len"), c.get("precision"), c.get("scale")
        )
        col_tags = column_tags.get((schema, table, str(c["name"])))
        if col_tags:
            fragment["x-db-tags"] = dict(sorted(col_tags.items()))
        cols_by_obj.setdefault((schema, table), []).append(
            CatalogColumn(
                name=str(c["name"]),
                schema=fragment,
                required=str(c.get("is_nullable", "YES")).upper() == "NO",
                position=int(c.get("position", 0)),
                default=c.get("default"),
                description=str(c.get("comment") or ""),
            )
        )

    pk = grouped_keys([r for r in (primary_keys or []) if kept(str(r["schema"]))], "constraint")
    uq = grouped_keys([r for r in (unique_keys or []) if kept(str(r["schema"]))], "constraint")
    fks_by_obj = fk_groups(
        [r for r in (foreign_keys or []) if kept(str(r["schema"]))], enforced=False
    )
    deps_by_obj: dict[tuple[str, str], list[str]] = {}
    for d in dependencies or []:
        if kept(str(d["schema"])):
            deps_by_obj.setdefault((str(d["schema"]), str(d["name"])), []).append(
                f"{d['ref_schema']}.{d['ref_name']}"
            )

    objects: list[CatalogObject] = []
    for t in tables:
        schema, name = str(t["schema"]), str(t["name"])
        if not kept(schema):
            continue
        key = (schema, name)
        columns_sorted = sorted(cols_by_obj.get(key, []), key=lambda c: c.position)
        primary = tuple(pk.get(key, [[]])[0]) if key in pk else ()
        uniques = tuple(tuple(u) for u in uq.get(key, []))
        fks = tuple(fks_by_obj.get(key, []))
        extra: dict[str, Any] = {}
        if str(t.get("is_transient", "")).upper() == "YES":
            extra["x-table-type"] = "transient"
        effective = _effective_tags(object_tags, schema, name)
        if effective:
            extra["x-db-tags"] = effective
        # Clefs déclarées mais non appliquées dans Snowflake.
        if primary or uniques or fks:
            extra["x-enforced"] = False
        objects.append(
            CatalogObject(
                schema=schema,
                name=name,
                kind=_SF_KIND.get(str(t.get("table_type", "")).upper(), "table"),
                columns=tuple(columns_sorted),
                description=str(t.get("comment") or ""),
                primary_key=primary,
                unique_keys=uniques,
                foreign_keys=fks,
                depends_on=tuple(deps_by_obj.get(key, [])),
                extra=extra,
            )
        )
    return CatalogRead(objects=objects, errors=list(errors or []), queries=queries)


def _driver() -> Any:
    # snowflake-connector-python est une dépendance du projet ; sans lui (image
    # allégée), la connexion n'est pas éprouvable et le dit.
    try:
        import snowflake.connector
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


def is_private_key(secret_value: str) -> bool:
    """Le secret stocké est-il une clé PEM ouverte ? Sinon c'est un mot de passe
    ou un jeton d'accès programmatique (dc22b381) — un PEM ne peut pas être pris
    pour un mot de passe : son en-tête est normalisé."""
    return secret_value.lstrip().startswith("-----BEGIN")


def auth_kwargs(secret_value: str) -> dict[str, Any]:
    """Arguments d'authentification du pilote selon la forme du secret. Le jeton
    d'accès programmatique passe par `password`, comme le documente Snowflake."""
    if is_private_key(secret_value):
        return {"private_key": _private_key_der(secret_value)}
    return {"password": secret_value}


class SnowflakeEngine(DatabaseEngine):
    """Snowflake par paire de clés (méthode durable, recommandée) ou, faute de
    clé, par mot de passe / jeton d'accès programmatique (dc22b381 : on doit
    pouvoir éprouver une connexion sans clé privée). Lecture de métadonnées
    uniquement. Non éprouvable dans cet environnement (pilote absent)."""

    def _connect(self, params: dict[str, str], secret_value: str) -> Any:  # pragma: no cover
        connector = _driver()
        try:
            return connector.connect(
                account=params.get("account", ""),
                user=params.get("username", ""),
                role=params.get("role") or None,
                warehouse=params.get("warehouse") or None,
                database=params.get("database") or None,
                login_timeout=10,
                **auth_kwargs(secret_value),
            )
        except Exception as exc:
            raise _map_error(connector, exc, keypair=is_private_key(secret_value)) from exc

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
            except Exception as exc:
                raise _query_error(exc) from exc
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
            cn = self._connect(params, secret_value)
            errors: list[tuple[str, str]] = []
            queries = 0
            try:
                cur = cn.cursor()

                def q(sql: str) -> list[dict[str, Any]]:
                    nonlocal queries
                    queries += 1
                    cur.execute(sql)
                    names = [str(d[0]).lower() for d in cur.description]
                    return [dict(zip(names, row, strict=False)) for row in cur.fetchall()]

                def safe(sql: str, note: str) -> list[dict[str, Any]]:
                    try:
                        return q(sql)
                    except Exception:
                        # Droits insuffisants (dépendances, clefs) : on signale, on continue.
                        errors.append((note, "insufficient_privilege"))
                        return []

                tables = [
                    {
                        "schema": r["table_schema"],
                        "name": r["table_name"],
                        "table_type": r.get("table_type"),
                        "comment": r.get("comment"),
                        "is_transient": r.get("is_transient"),
                    }
                    for r in q(
                        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, COMMENT, IS_TRANSIENT "
                        "FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA'"
                    )
                ]
                columns = [
                    {
                        "schema": r["table_schema"],
                        "table": r["table_name"],
                        "name": r["column_name"],
                        "position": r.get("ordinal_position", 0),
                        "is_nullable": r.get("is_nullable", "YES"),
                        "default": r.get("column_default"),
                        "data_type": r.get("data_type", ""),
                        "char_len": r.get("character_maximum_length"),
                        "precision": r.get("numeric_precision"),
                        "scale": r.get("numeric_scale"),
                        "comment": r.get("comment"),
                    }
                    for r in q(
                        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, "
                        "IS_NULLABLE, COLUMN_DEFAULT, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                        "NUMERIC_PRECISION, NUMERIC_SCALE, COMMENT FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA'"
                    )
                ]
                pks = [
                    {"schema": r.get("schema_name"), "table": r.get("table_name"),
                     "column": r.get("column_name"), "seq": r.get("key_sequence", 0),
                     "constraint": r.get("constraint_name")}
                    for r in safe("SHOW PRIMARY KEYS IN DATABASE", "primary_keys")
                ]
                uqs = [
                    {"schema": r.get("schema_name"), "table": r.get("table_name"),
                     "column": r.get("column_name"), "seq": r.get("key_sequence", 0),
                     "constraint": r.get("constraint_name")}
                    for r in safe("SHOW UNIQUE KEYS IN DATABASE", "unique_keys")
                ]
                fks = [
                    {"schema": r.get("fk_schema_name"), "table": r.get("fk_table_name"),
                     "column": r.get("fk_column_name"), "ref_schema": r.get("pk_schema_name"),
                     "ref_table": r.get("pk_table_name"), "ref_column": r.get("pk_column_name"),
                     "seq": r.get("key_sequence", 0), "fk_name": r.get("fk_name")}
                    for r in safe("SHOW IMPORTED KEYS IN DATABASE", "foreign_keys")
                ]
                deps = [
                    {"schema": r.get("referencing_schema_name"),
                     "name": r.get("referencing_object_name"),
                     "ref_schema": r.get("referenced_schema_name"),
                     "ref_name": r.get("referenced_object_name")}
                    for r in safe(
                        "SELECT referencing_schema_name, referencing_object_name, "
                        "referenced_schema_name, referenced_object_name "
                        "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                        "WHERE referencing_database = CURRENT_DATABASE()",
                        "dependencies",
                    )
                ]
                # Tags posés sur la base, ses schémas, ses objets et ses colonnes
                # (6a8c1828). ACCOUNT_USAGE : une requête pour toute la base, au
                # prix d'un retard possible de 2 h ; sans droit, on continue.
                tags = safe(
                    "SELECT tag_database, tag_schema, tag_name, tag_value, domain, "
                    "object_schema, object_name, column_name "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES "
                    "WHERE object_database = CURRENT_DATABASE() AND object_deleted IS NULL "
                    "AND domain IN ('DATABASE', 'SCHEMA', 'TABLE', 'COLUMN')",
                    "tags",
                )
            except DbEngineError:
                raise
            except Exception as exc:
                # Tables et colonnes sont indispensables (les autres lectures
                # passent par `safe`) : leur échec arrête la collecte, avec un
                # code traduit plutôt qu'une erreur 500 (89752930).
                raise _query_error(exc) from exc
            finally:
                cn.close()
            return build_catalog(
                tables=tables, columns=columns, primary_keys=pks, unique_keys=uqs,
                foreign_keys=fks, dependencies=deps, tags=tags, schemas=schemas,
                exclusions=exclusions,
                queries=queries, errors=errors,
            )

        return await asyncio.to_thread(_work)

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


def _query_error(exc: Exception) -> DbEngineError:
    """Échec d'une requête après une connexion réussie. Snowflake ouvre la
    session même si la base demandée n'existe pas ou n'est pas accessible : c'est
    la première requête qui échoue (090105 « no current database », 002003
    « does not exist or not authorized »)."""
    message = str(exc).lower()
    if "current database" in message or "does not exist or not authorized" in message:
        return DbEngineError("db_not_found")
    return DbEngineError("db_query_failed")


def _map_error(connector: Any, exc: Exception, *, keypair: bool = True) -> DbEngineError:
    message = str(exc).lower()
    if "login timed out" in message:
        # Le serveur répond mais ne conclut pas : typiquement la MFA d'un compte
        # humain, qui attend la validation d'une notification. « Hôte
        # injoignable » ferait chercher du côté du réseau, à tort.
        return DbEngineError("db_login_timeout")
    if "incorrect username or password" in message or "jwt" in message or "key" in message:
        # Le même refus se lit « clé refusée » ou « identifiants refusés » selon
        # la méthode employée : un mot de passe refusé n'a rien à voir avec une clé.
        return DbEngineError("db_key_rejected" if keypair else "db_auth_failed")
    if "role" in message and "does not exist" in message:
        return DbEngineError("db_role_not_found")
    if "warehouse" in message and "does not exist" in message:
        return DbEngineError("db_warehouse_not_found")
    return DbEngineError("db_host_unreachable")
