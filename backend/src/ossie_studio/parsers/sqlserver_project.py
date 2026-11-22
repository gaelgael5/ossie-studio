"""Parseur `sqlserver-projet` : lit les fichiers `.sql` d'un projet de base de
données SQL Server (ceux qui servent à construire le DACPAC), **pas** le `.dacpac`
compilé. Produit un schéma par table et par vue (même forme que l'extracteur de
catalogue SQL Server), et un schéma à part par colonne JSON décrite par un
fichier `*.schema.json` lié par convention de nommage.

Analyse T-SQL par **sqlglot** (dialecte tsql, épinglé — sa version entre dans la
version du parseur). Jamais de DacFx/ScriptDom ni de dépendance .NET ; jamais
d'exécution. Incrémentalité : le parseur tient son propre index et reconstruit
tout le projet dès qu'un de ses fichiers change (regroupement d'objets sur
plusieurs fichiers mal servi par le moteur générique).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib.metadata import version as _pkg_version
from typing import Any

import sqlglot
import yaml
from defusedxml import ElementTree as DefusedET  # type: ignore[import-untyped]
from sqlglot import exp

from ossie_studio.databases.sqlserver_types import type_fragment
from ossie_studio.parsers.base import (
    CIBLE_RAW,
    STATUT_ERREUR,
    STATUT_IGNORE,
    STATUT_INCHANGE,
    STATUT_TRAITE,
    OutputAction,
    PluginContext,
    PluginError,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.parsers.conventions import dump_schema

_PARSER_ID = "sqlserver-projet"
_GLOBS = ["**/*.sql", "**/*.sqlproj", "**/*.schema.json"]
_INDEX_NAME = f"manifest-{_PARSER_ID}.yaml"
_SLUG = re.compile(r"[^a-zA-Z0-9]+")

# `[schéma.]table.colonne.schema.json` (insensible à la casse). Un simple
# `nom.schema.json` (parseur de démo) ne correspond pas : il faut table.colonne.
_SCHEMA_JSON_RE = re.compile(
    r"^(?:(?P<schema>[^.]+)\.)?(?P<table>[^.]+)\.(?P<col>[^.]+)\.schema\.json$", re.IGNORECASE
)
_GO_RE = re.compile(r"^\s*GO\s*(--.*)?$", re.IGNORECASE)

# Ordres connus mais sans intérêt pour les schémas (ignorés sans signalement).
_IGNORABLE = tuple(
    t
    for t in (
        getattr(exp, name, None)
        for name in (
            "Grant", "Insert", "Delete", "Update", "Merge", "Drop", "Set", "Use", "Comment",
        )
    )
    if t is not None
)


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


def _strip_ident(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "[\"" and value[-1] in "]\"":
        return value[1:-1]
    return value


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Analyse T-SQL -----------------------------------------------------------


def split_batches(text: str) -> list[tuple[str, int]]:
    """Découpe un fichier `.sql` sur les séparateurs `GO` (sqlglot ne les lit pas).
    Rend (texte du lot, ligne de début 1-indexée)."""
    batches: list[tuple[str, int]] = []
    current: list[str] = []
    start = 1
    for i, line in enumerate(text.splitlines(), start=1):
        if _GO_RE.match(line):
            if current:
                batches.append(("\n".join(current), start))
            current = []
            start = i + 1
        else:
            if not current:
                start = i
            current.append(line)
    if current:
        batches.append(("\n".join(current), start))
    return batches


def parse_type(raw: str) -> tuple[str, int | None, int | None, int | None]:
    """(base, max_len, precision, scale) depuis un type tel qu'écrit."""
    match = re.match(r"\s*\[?(?P<base>\w+)\]?\s*(?:\((?P<args>[^)]*)\))?", raw)
    if match is None:
        return raw.strip().lower(), None, None, None
    base = match.group("base").lower()
    args = (match.group("args") or "").strip()
    if not args:
        return base, None, None, None
    parts = [p.strip() for p in args.split(",")]
    if base in ("decimal", "numeric"):
        precision = int(parts[0]) if parts[0].isdigit() else None
        scale = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return base, None, precision, scale
    if parts[0].lower() == "max":
        return base, -1, None, None
    return base, (int(parts[0]) if parts[0].isdigit() else None), None, None


def _raw_type(batch: str, col_name: str) -> str:
    """Type **tel qu'écrit** d'une colonne, extrait du texte (sqlglot réécrit
    certains types : decimal→numeric, real→float, ntext→varchar(max))."""
    pattern = re.compile(
        r"(?:\[\s*" + re.escape(col_name) + r"\s*\]|\b" + re.escape(col_name) + r"\b)"
        r"\s+(\w+(?:\s*\([^)]*\))?)",
        re.IGNORECASE,
    )
    match = pattern.search(batch)
    return match.group(1).strip() if match else ""


@dataclass
class _Obj:
    schema: str
    name: str
    kind: str  # table | view
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    required: set[str] = field(default_factory=set)
    primary_key: list[str] = field(default_factory=list)
    unique_keys: list[list[str]] = field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    col_desc: dict[str, str] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def add_col(self, name: str, fragment: dict[str, Any]) -> None:
        if name not in self.columns:
            self.order.append(name)
        self.columns[name] = fragment


def _obj_key(schema: str, name: str) -> tuple[str, str]:
    return (schema.lower(), name.lower())


def _table_ident(node: exp.Expression) -> tuple[str, str]:
    table = node.this if isinstance(node, exp.Schema) else node
    if isinstance(table, exp.Table):
        return (table.db or "dbo") or "dbo", table.name
    return "dbo", node.name


def _column_names(node: exp.Expression) -> list[str]:
    text = node.sql(dialect="tsql")
    match = re.search(r"\(([^)]*)\)", text)
    if match is None:
        return []
    return [_strip_ident(p) for p in match.group(1).split(",") if p.strip()]


def _handle_table(
    create: exp.Create, batch: str, file: str, line: int, objs: dict[tuple[str, str], _Obj]
) -> None:
    schema_node = create.this
    schema, name = _table_ident(schema_node)
    key = _obj_key(schema, name)
    obj = objs.setdefault(key, _Obj(schema=schema, name=name, kind="table"))
    obj.kind = "table"
    obj.sources.append({"file": file, "line": line})
    definitions = schema_node.expressions if isinstance(schema_node, exp.Schema) else []
    for item in definitions:
        if isinstance(item, exp.ColumnDef):
            _handle_column(item, batch, obj)
        elif isinstance(item, exp.Constraint):
            _handle_table_constraint(item, obj)
        elif isinstance(item, exp.PrimaryKey):
            obj.primary_key = _column_names(item)
        elif isinstance(item, exp.ForeignKey):
            _add_fk(item, obj)


def _handle_column(cd: exp.ColumnDef, batch: str, obj: _Obj) -> None:
    name = cd.name
    nullable = True
    default = None
    identity = False
    computed = None
    for c in cd.constraints:
        kind = c.kind
        if isinstance(kind, exp.NotNullColumnConstraint):
            nullable = bool(kind.args.get("allow_null"))
        elif isinstance(kind, exp.GeneratedAsIdentityColumnConstraint):
            identity = True
        elif isinstance(kind, exp.ComputedColumnConstraint):
            computed = kind.this.sql(dialect="tsql") if kind.this else ""
        elif isinstance(kind, exp.DefaultColumnConstraint):
            default = kind.this.sql(dialect="tsql") if kind.this else None
        elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
            obj.primary_key = [name]
        elif isinstance(kind, exp.UniqueColumnConstraint):
            obj.unique_keys.append([name])
        elif isinstance(kind, exp.Reference):
            _add_column_fk(name, kind, obj)
    if computed is not None:
        fragment: dict[str, Any] = {"x-computed": computed}
    else:
        raw = _raw_type(batch, name) or (cd.kind.sql(dialect="tsql").lower() if cd.kind else "")
        base, max_len, precision, scale = parse_type(raw)
        fragment = type_fragment(base, max_len, precision, scale, raw=raw or None)
        if identity:
            fragment["x-identity"] = True
        if not nullable:
            obj.required.add(name)
    if default is not None:
        fragment["x-default"] = default
    obj.add_col(name, fragment)


def _handle_table_constraint(cons: exp.Constraint, obj: _Obj) -> None:
    for part in cons.expressions:
        if isinstance(part, exp.PrimaryKey):
            obj.primary_key = _column_names(part)
        elif isinstance(part, exp.ForeignKey):
            _add_fk(part, obj)
        elif isinstance(part, exp.UniqueColumnConstraint):
            obj.unique_keys.append(_column_names(part))


def _add_fk(fk: exp.ForeignKey, obj: _Obj) -> None:
    cols = _column_names(fk)
    ref = fk.args.get("reference")
    ref_schema, ref_object, ref_cols = _reference_target(ref)
    obj.foreign_keys.append(
        {"columns": cols, "ref_schema": ref_schema, "ref_object": ref_object,
         "ref_columns": ref_cols, "enforced": True}
    )


def _add_column_fk(col: str, ref: exp.Reference, obj: _Obj) -> None:
    ref_schema, ref_object, ref_cols = _reference_target(ref)
    obj.foreign_keys.append(
        {"columns": [col], "ref_schema": ref_schema, "ref_object": ref_object,
         "ref_columns": ref_cols or [col], "enforced": True}
    )


def _reference_target(ref: exp.Expression | None) -> tuple[str, str, list[str]]:
    if ref is None:
        return "dbo", "", []
    table = ref.find(exp.Table)
    ref_schema = (table.db or "dbo") if table else "dbo"
    ref_table = table.name if table else ""
    ref_cols = [_strip_ident(c) for c in _column_names(ref)]
    return ref_schema or "dbo", ref_table, ref_cols


def _handle_view(
    create: exp.Create, file: str, line: int, objs: dict[tuple[str, str], _Obj]
) -> None:
    schema, name = _table_ident(create.this)
    key = _obj_key(schema, name)
    obj = objs.setdefault(key, _Obj(schema=schema, name=name, kind="view"))
    obj.kind = "view"
    obj.sources.append({"file": file, "line": line})
    select = create.expression
    if not isinstance(select, exp.Select):
        return
    aliases = _from_aliases(select)
    for proj in select.expressions:
        out_name = proj.alias_or_name
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, exp.Column):
            src_table = aliases.get((inner.table or "").lower())
            if src_table is None and len(aliases) == 1:
                src_table = next(iter(aliases.values()))
            if src_table is not None:
                derived = f"{src_table[0]}.{src_table[1]}.{inner.name}"
            else:
                derived = f"{inner.table}.{inner.name}" if inner.table else inner.name
            obj.add_col(out_name, {"x-derived-from": derived})
        else:
            obj.add_col(out_name, {"x-expression": inner.sql(dialect="tsql")})


def _from_aliases(select: exp.Select) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    sources: list[exp.Expression] = []
    from_ = select.args.get("from_") or select.find(exp.From)
    if isinstance(from_, exp.From):
        sources.append(from_.this)
    for join in select.args.get("joins") or []:
        sources.append(join.this)
    for src in sources:
        if isinstance(src, exp.Table):
            schema = (src.db or "dbo") or "dbo"
            alias = (src.alias or src.name)
            aliases[alias.lower()] = (schema, src.name)
    return aliases


def _handle_alter(
    alter: exp.Alter, file: str, line: int, objs: dict[tuple[str, str], _Obj]
) -> None:
    table = alter.this
    if not isinstance(table, exp.Table):
        return
    schema = (table.db or "dbo") or "dbo"
    key = _obj_key(schema, table.name)
    obj = objs.setdefault(key, _Obj(schema=schema, name=table.name, kind="table"))
    obj.sources.append({"file": file, "line": line})
    for action in alter.args.get("actions") or []:
        constraints = action.args.get("expressions") if hasattr(action, "args") else None
        for cons in constraints or []:
            if isinstance(cons, exp.Constraint):
                _handle_table_constraint(cons, obj)
            elif isinstance(cons, exp.PrimaryKey):
                obj.primary_key = _column_names(cons)
            elif isinstance(cons, exp.ForeignKey):
                _add_fk(cons, obj)


def _handle_extended_property(
    exe: exp.Execute, file: str, line: int, objs: dict[tuple[str, str], _Obj]
) -> None:
    proc = exe.this.sql(dialect="tsql").lower() if exe.this else ""
    if "sp_addextendedproperty" not in proc:
        return
    args: dict[str, str] = {}
    for a in exe.expressions:
        if isinstance(a, exp.EQ) and isinstance(a.this, exp.Parameter):
            args[a.this.name.lower()] = _literal(a.expression)
    if args.get("name", "").lower() != "ms_description":
        return
    value = args.get("value", "")
    level1type = args.get("level1type", "").lower()
    if level1type not in ("table", "view"):
        return
    schema = args.get("level0name") or "dbo"
    table = args.get("level1name", "")
    key = _obj_key(schema, table)
    obj = objs.get(key)
    if obj is None:
        return
    obj.sources.append({"file": file, "line": line})
    if args.get("level2type", "").lower() == "column" and args.get("level2name"):
        obj.col_desc[args["level2name"]] = value
    else:
        obj.description = value


def _literal(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    return node.name if node.name else _strip_ident(node.sql(dialect="tsql"))


# --- .sqlproj ----------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sqlproj_includes(xml_text: str, proj_dir: str, all_sql: set[str]) -> set[str]:
    """Ensemble des `.sql` (chemins POSIX relatifs à la source) retenus par un
    `.sqlproj`. Format SDK : tous les `.sql` du projet sauf Build Remove / None ;
    ancien format : seuls les Build Include."""
    root = DefusedET.fromstring(xml_text)
    sdk = bool(root.get("Sdk"))

    def rel(path: str) -> str:
        path = path.replace("\\", "/").lstrip("./")
        return f"{proj_dir}/{path}" if proj_dir else path

    build_include: set[str] = set()
    removed: set[str] = set()
    for item in root.iter():
        tag = _local_name(item.tag)
        inc = item.get("Include")
        rem = item.get("Remove")
        if tag == "Build" and inc:
            build_include.add(rel(inc))
        elif tag == "Build" and rem:
            removed.add(rel(rem))
        elif tag == "None" and inc:
            removed.add(rel(inc))
    project_sql = {
        p for p in all_sql if not proj_dir or p == proj_dir or p.startswith(f"{proj_dir}/")
    }
    if sdk:
        return {p for p in project_sql if p not in removed}
    return {p for p in build_include if p in all_sql}


# --- JSON Schema : conversion vers 2020-12 -----------------------------------


def convert_json_schema(data: Any) -> tuple[Any, list[str]]:
    """Convertit un JSON Schema (draft-07 / 2019-09) vers 2020-12 pour les
    mots-clefs courants. Rend (schéma converti, signaux). `$ref` interne conservée ;
    `$ref` externe → x-unresolved-type, signalée."""
    signals: list[str] = []

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "definitions":
                out["$defs"] = walk(value)
            elif key == "items" and isinstance(value, list):
                out["prefixItems"] = [walk(x) for x in value]
            elif key == "$ref" and isinstance(value, str):
                if value.startswith("#"):
                    # Référence interne conservée, repointée vers $defs.
                    out["$ref"] = value.replace("/definitions/", "/$defs/")
                else:
                    out["x-unresolved-type"] = value
                    signals.append("external_ref")
            else:
                out[key] = walk(value)
        return out

    return walk(data), signals


def _schema_json_target(filename: str) -> tuple[str, str, str] | None:
    match = _SCHEMA_JSON_RE.match(filename)
    if match is None:
        return None
    return (match.group("schema") or "dbo", match.group("table"), match.group("col"))


# --- Parseur -----------------------------------------------------------------


@dataclass
class _Plan:
    writes: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    errors: list[PluginError] = field(default_factory=list)


class SqlServerProjectParser:
    """Parseur de projet SQL Server (fichiers `.sql`). Tient son propre index et
    reconstruit tout le projet dès qu'un de ses fichiers change."""

    id = _PARSER_ID
    cible = CIBLE_RAW
    label = "Projet SQL Server"
    # 1.1 : `x-source` porte `file` / `line` (au lieu de `fichier` / `ligne`) ; le
    # changement de version force la régénération des schémas déjà collectés.
    version = f"1.1-sqlglot{_pkg_version('sqlglot')}"

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path)

    def run(self, context: PluginContext) -> PluginResult:
        contents = context.contents
        sql_files = {p: c for p, c in contents.items() if p.endswith(".sql")}
        proj_files = {p: c for p, c in contents.items() if p.endswith(".sqlproj")}
        schema_jsons = {
            p: c for p, c in contents.items()
            if p.endswith(".schema.json") and _schema_json_target(p.rsplit("/", 1)[-1])
        }
        # Sans projet SQL, le parseur ne revendique rien (cf. parseur de démo).
        if not sql_files and not proj_files:
            return PluginResult()

        owned = {**sql_files, **proj_files, **schema_jsons}
        checks = {p: _checksum(c) for p, c in owned.items()}
        previous = self._read_index(context)
        unchanged = (
            not context.force
            and previous.get("parser", previous.get("parseur")) == self.version
            and previous.get("files", previous.get("fichiers")) == checks
        )
        if unchanged:
            return PluginResult(
                statuses={p: STATUT_INCHANGE for p in owned},
                unchanged=len(owned),
                retained=len(owned),
            )

        plan = self._rebuild(sql_files, proj_files, schema_jsons, checks)
        existing = {p: c for p, c in context.raw.list_outputs().items() if _is_schema(p)}
        # Repli sur `sorties` (index version 1, clés françaises) : les anciennes
        # sorties sont bien retirées à la première collecte après la mise à jour.
        old_outputs = set(previous.get("outputs", previous.get("sorties")) or [])
        writes = [
            OutputAction(path=dest, content=content)
            for dest, content in plan.writes.items()
            if existing.get(dest) != content
        ]
        deletes = [d for d in old_outputs if d not in plan.writes]
        index = {
            "version": 2,  # 2 : clés en anglais (la version 1 était en français)
            "parser": self.version,
            "files": checks,
            "outputs": sorted(plan.writes),
        }
        writes.append(OutputAction(path=_INDEX_NAME, content=yaml.safe_dump(index, sort_keys=True)))
        added = sum(1 for w in writes if w.path != _INDEX_NAME and w.path not in existing)
        modified = sum(
            1 for w in writes if w.path != _INDEX_NAME and w.path in existing
        )
        return PluginResult(
            writes=writes,
            deletes=deletes,
            statuses=plan.statuses,
            errors=plan.errors,
            added=added,
            modified=modified,
            deleted=len(deletes),
            read=len(owned),
            retained=len(owned),
        )

    def _read_index(self, context: PluginContext) -> dict[str, Any]:
        text = context.raw.read_index(_INDEX_NAME)
        if not text:
            return {}
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}

    def _rebuild(
        self,
        sql_files: dict[str, bytes],
        proj_files: dict[str, bytes],
        schema_jsons: dict[str, bytes],
        checks: dict[str, str],
    ) -> _Plan:
        plan = _Plan()
        all_sql = set(sql_files)
        included = self._included_sql(proj_files, all_sql, plan)

        from ossie_studio.sources.sql_extract import build_object_schema

        objs: dict[tuple[str, str], _Obj] = {}
        for path in sorted(included):
            text = sql_files[path].decode("utf-8", "replace")
            self._parse_file(path, text, objs, plan)

        # Association colonnes JSON ↔ fichiers *.schema.json (signalée une seule fois).
        matches = self._match_json_columns(schema_jsons, objs, plan)

        # Schémas de table / vue.
        for key, obj in sorted(objs.items()):
            json_cols = {col: cid for col, (cid, _src) in matches.get(key, {}).items()}
            data = _normalized(obj, json_cols)
            schema = build_object_schema(data)
            plan.writes[f"{_slug(f'{obj.schema}.{obj.name}')}.json"] = dump_schema(schema)

        # Schémas de contenu JSON (un par colonne JSON reliée).
        for key, cols in sorted(matches.items()):
            obj = objs[key]
            for real_col, (content_id, src_path) in sorted(cols.items()):
                self._emit_content_schema(obj, real_col, content_id, src_path, schema_jsons, plan)
        return plan

    def _emit_content_schema(
        self,
        obj: _Obj,
        real_col: str,
        content_id: str,
        src_path: str,
        schema_jsons: dict[str, bytes],
        plan: _Plan,
    ) -> None:
        try:
            data = json.loads(schema_jsons[src_path])
        except json.JSONDecodeError as exc:
            plan.errors.append(
                PluginError(source=src_path, cause="json_schema_invalid", detail=str(exc))
            )
            return
        if not isinstance(data, dict):
            plan.errors.append(
                PluginError(
                    source=src_path, cause="json_schema_invalid", detail="root is not an object"
                )
            )
            return
        converted, _signals = convert_json_schema(data)
        converted["$id"] = content_id
        converted["x-kind"] = "json-content"
        converted["x-json-column"] = f"{obj.schema}.{obj.name}.{real_col}"
        converted["x-source"] = [{"file": src_path}]
        plan.statuses[src_path] = STATUT_TRAITE
        plan.writes[content_id] = dump_schema(converted)

    def _included_sql(
        self, proj_files: dict[str, bytes], all_sql: set[str], plan: _Plan
    ) -> set[str]:
        if not proj_files:
            plan.errors.append(PluginError(source="(projet)", cause="no_sqlproj"))
            return set(all_sql)
        included: set[str] = set()
        for proj_path, raw in proj_files.items():
            proj_dir = proj_path.rsplit("/", 1)[0] if "/" in proj_path else ""
            try:
                included |= sqlproj_includes(raw.decode("utf-8", "replace"), proj_dir, all_sql)
            except Exception as exc:
                plan.errors.append(
                    PluginError(source=proj_path, cause="sqlproj_invalid", detail=str(exc)[:300])
                )
            plan.statuses[proj_path] = STATUT_TRAITE
        return included

    def _parse_file(
        self, path: str, text: str, objs: dict[tuple[str, str], _Obj], plan: _Plan
    ) -> None:
        handled = False
        for batch, line in split_batches(text):
            try:
                expressions = sqlglot.parse(batch, dialect="tsql")
            except Exception as exc:
                plan.statuses[path] = STATUT_ERREUR
                # sqlglot numérote les lignes depuis le début du lot : on donne la
                # ligne du lot dans le fichier pour que l'utilisateur s'y retrouve.
                plan.errors.append(
                    PluginError(
                        source=path,
                        cause="sql_unparseable",
                        detail=f"batch at line {line}: {(str(exc).splitlines() or [''])[0][:300]}",
                    )
                )
                return
            for expr in expressions:
                if not isinstance(expr, exp.Expression):
                    continue
                handled = self._dispatch(expr, batch, path, line, objs, plan) or handled
        if path not in plan.statuses:
            plan.statuses[path] = STATUT_TRAITE if handled else STATUT_IGNORE

    def _dispatch(
        self,
        expr: exp.Expression,
        batch: str,
        path: str,
        line: int,
        objs: dict[tuple[str, str], _Obj],
        plan: _Plan,
    ) -> bool:
        if isinstance(expr, exp.Create):
            kind = (expr.kind or "").upper()
            if kind == "TABLE":
                _handle_table(expr, batch, path, line, objs)
                return True
            if kind == "VIEW":
                _handle_view(expr, path, line, objs)
                return True
            return False  # schéma, procédure, fonction… : ignoré
        if isinstance(expr, exp.Alter):
            _handle_alter(expr, path, line, objs)
            return True
        if isinstance(expr, exp.Execute):
            _handle_extended_property(expr, path, line, objs)
            return True
        if isinstance(expr, _IGNORABLE):
            return False  # grant, insert, set, use… : connu, ignoré
        # Ordre non reconnu (ni table/vue/contrainte/propriété, ni DDL connu).
        try:
            statement = expr.sql(dialect="tsql").splitlines()[0][:120]
        except Exception:  # régénération impossible : le type d'ordre suffit
            statement = type(expr).__name__
        plan.errors.append(
            PluginError(source=path, cause="sql_unrecognized", detail=f"line {line}: {statement}")
        )
        return False

    def _match_json_columns(
        self,
        schema_jsons: dict[str, bytes],
        objs: dict[tuple[str, str], _Obj],
        plan: _Plan,
    ) -> dict[tuple[str, str], dict[str, tuple[str, str]]]:
        """Associe chaque `*.schema.json` valide à {objet: {colonne: ($id, fichier)}}.
        Signale orphelins, doublons et colonnes non-chaîne (une seule fois)."""
        by_target: dict[tuple[str, str, str], list[str]] = {}
        for path in schema_jsons:
            target = _schema_json_target(path.rsplit("/", 1)[-1])
            if target is None:
                continue
            schema, table, col = target
            by_target.setdefault((schema.lower(), table.lower(), col.lower()), []).append(path)

        result: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
        for (schema, table, col), paths in sorted(by_target.items()):
            if len(paths) > 1:
                for p in sorted(paths):
                    plan.errors.append(PluginError(source=p, cause="json_schema_duplicate"))
                continue
            path = paths[0]
            key = (schema, table)
            obj = objs.get(key)
            real_col = _find_col(obj, col) if obj else None
            if obj is None or real_col is None:
                plan.errors.append(PluginError(source=path, cause="json_schema_orphan"))
                continue
            if obj.columns.get(real_col, {}).get("type") != "string":
                plan.errors.append(PluginError(source=path, cause="json_schema_not_string"))
                continue
            content_id = f"{_slug(f'{obj.schema}.{obj.name}.{real_col}')}.json"
            result.setdefault(key, {})[real_col] = (content_id, path)
        return result


def _find_col(obj: _Obj | None, col_lower: str) -> str | None:
    if obj is None:
        return None
    for name in obj.order:
        if name.lower() == col_lower:
            return name
    return None


def _is_schema(path: str) -> bool:
    return path.endswith(".json") and "/" not in path


def _normalized(obj: _Obj, json_cols: dict[str, str]) -> dict[str, Any]:
    columns: list[dict[str, Any]] = []
    for i, name in enumerate(obj.order, start=1):
        fragment = dict(obj.columns[name])
        if name in json_cols:
            fragment["contentMediaType"] = "application/json"
            fragment["contentSchema"] = {"$ref": json_cols[name]}
        columns.append(
            {
                "name": name,
                "schema": fragment,
                "required": name in obj.required,
                "position": i,
                "default": None,
                "description": obj.col_desc.get(name, ""),
            }
        )
    seen: set[tuple[str, int]] = set()
    sources: list[dict[str, Any]] = []
    for s in sorted(obj.sources, key=lambda s: (s["file"], s.get("line", 0))):
        marker = (s["file"], s.get("line", 0))
        if marker not in seen:
            seen.add(marker)
            sources.append(s)
    extra: dict[str, Any] = {"x-engine": "sqlserver", "x-source": sources}
    return {
        "schema": obj.schema,
        "name": obj.name,
        "kind": obj.kind,
        "description": obj.description,
        "columns": columns,
        "primary_key": obj.primary_key,
        "unique_keys": obj.unique_keys,
        "foreign_keys": obj.foreign_keys,
        "extra": extra,
    }
