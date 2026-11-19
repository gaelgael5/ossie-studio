"""Parseur `typescript` : lit les fichiers `.ts` et `.tsx` (hors `.d.ts` par
défaut) et produit un schéma par **interface**, **alias de type objet**
(`type X = { … }`), **classe** et **enum**.

Analyse **syntaxique** (tree-sitter), jamais d'exécution du code lu. Un type venu
d'un autre fichier ou d'un paquet (`node_modules`) devient `x-unresolved-type` ;
la résolution croisée viendra du service de recherche de schéma commun. Les
références au sein du même groupe de fichiers sont résolues en `$ref` ; un alias
de type non objet du même groupe est résolu et inliné.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Node

from ossie_studio.parsers.base import (
    CIBLE_RAW,
    ParsedObject,
    ParserError,
    PluginContext,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.parsers.treesitter import grammar_version, node_text
from ossie_studio.parsers.treesitter import parse as ts_parse

_GLOBS = ["**/*.ts", "**/*.tsx"]
_SLUG = re.compile(r"[^a-zA-Z0-9]+")

_PREDEFINED: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "bigint": {"type": "integer"},
    "symbol": {"type": "string"},
    "any": {},
    "unknown": {},
    "object": {"type": "object"},
    "void": {"type": "null"},
    "never": {},
}
# Types nommés avec une correspondance publiée (sinon résolus en $ref ou x-unresolved).
_NAMED_PRIMITIVES: dict[str, dict[str, Any]] = {
    "Date": {"type": "string", "format": "date-time"},
}
_ARRAY_GENERICS = frozenset({"Array", "ReadonlyArray"})
_DICT_GENERICS = frozenset({"Record", "Map", "ReadonlyMap", "WeakMap"})
# Types utilitaires : non résolus, notés tels quels.
_UTILITY_GENERICS = frozenset(
    {"Partial", "Required", "Readonly", "Pick", "Omit", "Exclude", "Extract",
     "NonNullable", "Parameters", "ReturnType", "InstanceType", "Awaited"}
)


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


def _module_of(path: str) -> str:
    rel = path.replace("\\", "/").lstrip("./")
    for ext in (".d.ts", ".tsx", ".ts"):
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    parts = [p for p in rel.split("/") if p]
    if parts and parts[-1] in ("index",):
        parts.pop()
    return ".".join(parts)


# --- Représentation intermédiaire --------------------------------------------


@dataclass
class _TypeRef:
    kind: str  # named | predefined | array | dict | tuple | union | intersection
    #            | enum | object | unresolved | empty | null | undefined
    name: str = ""
    predef: dict[str, Any] = field(default_factory=dict)
    inner: _TypeRef | None = None
    value: _TypeRef | None = None
    options: list[_TypeRef] = field(default_factory=list)
    literals: list[Any] = field(default_factory=list)
    props: list[_Prop] = field(default_factory=list)
    expr: str = ""
    nullable: bool = False
    optional: bool = False


@dataclass
class _Prop:
    name: str
    type_ref: _TypeRef
    optional: bool
    visibility: str
    doc: str
    decorators: list[dict[str, Any]]


@dataclass
class _Decl:
    qualified: str
    name: str
    kind: str  # interface | type | class | enum
    visibility: str
    doc: str
    props: list[_Prop]
    bases: list[str]
    decorators: list[dict[str, Any]]
    enum_values: list[Any] = field(default_factory=list)
    alias_ref: _TypeRef | None = None  # alias de type non objet (résolution inline)


# --- Navigation --------------------------------------------------------------


def _text(src: bytes, node: Node | None) -> str:
    return node_text(src, node) if node is not None else ""


def _jsdoc(src: bytes, node: Node) -> str:
    prev = node.prev_sibling
    # Une déclaration exportée est enveloppée : le JSDoc précède l'`export`.
    if (prev is None or prev.type != "comment") and node.parent is not None:
        if node.parent.type == "export_statement":
            prev = node.parent.prev_sibling
    if prev is not None and prev.type == "comment":
        text = _text(src, prev)
        if text.startswith("/**"):
            body = text[3:-2] if text.endswith("*/") else text[3:]
            lines = []
            for raw in body.splitlines():
                line = raw.strip().lstrip("*").strip()
                if line.startswith("@"):
                    break
                if line:
                    lines.append(line)
            return re.sub(r"\s+", " ", " ".join(lines)).strip()
    return ""


def _decorators(src: bytes, node: Node) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for child in node.named_children:
        if child.type != "decorator":
            continue
        expr = child.named_children[0] if child.named_children else None
        if expr is None:
            continue
        if expr.type == "call_expression":
            name = _text(src, expr.child_by_field_name("function"))
            arg_node = expr.child_by_field_name("arguments")
            args = [_text(src, a) for a in arg_node.named_children] if arg_node else []
        else:
            name = _text(src, expr)
            args = []
        result.append({"nom": name.rsplit(".", 1)[-1], "arguments": args})
    return result


def _has_question(node: Node) -> bool:
    return any(c.type == "?" for c in node.children)


# --- Types -------------------------------------------------------------------


def _literal_of(src: bytes, node: Node | None) -> Any:
    if node is None:
        return ""
    if node.type == "literal_type" and node.named_children:
        return _literal_of(src, node.named_children[0])
    if node.type == "string":
        content = next((c for c in node.named_children if c.type == "string_fragment"), None)
        return _text(src, content) if content is not None else _text(src, node).strip("'\"`")
    if node.type == "number":
        text = _text(src, node)
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    if node.type in ("true", "false"):
        return node.type == "true"
    if node.type == "null":
        return None
    return _text(src, node)


def _type_ref(src: bytes, node: Node | None) -> _TypeRef:
    if node is None:
        return _TypeRef("empty")
    kind = node.type
    if kind == "type_annotation":
        inner = node.named_children[0] if node.named_children else None
        return _type_ref(src, inner)
    if kind == "parenthesized_type":
        inner = node.named_children[0] if node.named_children else None
        return _type_ref(src, inner)
    if kind == "predefined_type":
        name = _text(src, node)
        if name == "null":
            return _TypeRef("null")
        if name == "undefined":
            return _TypeRef("undefined")
        return _TypeRef("predefined", predef=dict(_PREDEFINED.get(name, {})))
    if kind in ("type_identifier", "identifier"):
        name = _text(src, node)
        if name == "undefined":
            return _TypeRef("undefined")
        return _TypeRef("named", name=name)
    if kind == "literal_type":
        child = node.named_children[0] if node.named_children else None
        if child is not None and child.type == "null":
            return _TypeRef("null")
        return _TypeRef("enum", literals=[_literal_of(src, node)])
    if kind == "array_type":
        inner = node.named_children[0] if node.named_children else None
        return _TypeRef("array", inner=_type_ref(src, inner))
    if kind == "tuple_type":
        return _TypeRef("tuple", options=[_type_ref(src, c) for c in node.named_children])
    if kind == "union_type":
        return _union_ref([_type_ref(src, c) for c in node.named_children])
    if kind == "intersection_type":
        return _TypeRef("intersection", options=[_type_ref(src, c) for c in node.named_children])
    if kind == "object_type":
        return _TypeRef("object", props=_object_props(src, node))
    if kind == "generic_type":
        return _generic_ref(src, node)
    return _TypeRef("unresolved", expr=re.sub(r"\s+", " ", _text(src, node)).strip())


def _generic_ref(src: bytes, node: Node) -> _TypeRef:
    name_node = node.child_by_field_name("name")
    base = _text(src, name_node).rsplit(".", 1)[-1]
    args_node = node.child_by_field_name("type_arguments")
    args = [c for c in (args_node.named_children if args_node else [])]
    if base in _ARRAY_GENERICS and args:
        return _TypeRef("array", inner=_type_ref(src, args[0]))
    if base in _DICT_GENERICS and len(args) >= 2:
        return _TypeRef("dict", value=_type_ref(src, args[1]))
    if base in _UTILITY_GENERICS:
        return _TypeRef("unresolved", expr=re.sub(r"\s+", " ", _text(src, node)).strip())
    return _TypeRef("named", name=base)


def _union_ref(operands: list[_TypeRef]) -> _TypeRef:
    nullable = any(o.kind == "null" for o in operands)
    optional = any(o.kind == "undefined" for o in operands)
    rest = [o for o in operands if o.kind not in ("null", "undefined")]
    if rest and all(o.kind == "enum" for o in rest):
        literals: list[Any] = []
        for o in rest:
            literals += o.literals
        return _TypeRef("enum", literals=literals, nullable=nullable, optional=optional)
    if not rest:
        return _TypeRef("null", optional=optional)
    if len(rest) == 1:
        base = rest[0]
        base.nullable = base.nullable or nullable
        base.optional = base.optional or optional
        return base
    return _TypeRef("union", options=rest, nullable=nullable, optional=optional)


def _object_props(src: bytes, body: Node) -> list[_Prop]:
    props: list[_Prop] = []
    for sig in body.named_children:
        if sig.type == "property_signature":
            name = _text(src, sig.child_by_field_name("name"))
            if not name:
                continue
            props.append(
                _Prop(
                    name=name,
                    type_ref=_type_ref(src, sig.child_by_field_name("type")),
                    optional=_has_question(sig),
                    visibility="public",
                    doc=_jsdoc(src, sig),
                    decorators=[],
                )
            )
        elif sig.type == "index_signature":
            props.append(
                _Prop(
                    name="[index]",
                    type_ref=_type_ref(src, sig.child_by_field_name("type")),
                    optional=True,
                    visibility="public",
                    doc="",
                    decorators=[],
                )
            )
    return props


# --- Extraction des déclarations ---------------------------------------------


def _extract_file(src: bytes, root: Node) -> list[_Decl]:
    out: list[_Decl] = []
    _walk(src, root, prefix="", exported=False, out=out)
    return out


def _walk(src: bytes, node: Node, *, prefix: str, exported: bool, out: list[_Decl]) -> None:
    for child in node.named_children:
        if child.type == "export_statement":
            decl = child.child_by_field_name("declaration")
            if decl is not None:
                _walk_decl(src, decl, prefix=prefix, exported=True, out=out)
            continue
        if child.type in ("internal_module", "module"):
            name = _text(src, child.child_by_field_name("name")).strip("'\"")
            body = child.child_by_field_name("body")
            if body is not None:
                deeper = f"{prefix}.{name}" if prefix else name
                _walk(src, body, prefix=deeper, exported=exported, out=out)
            continue
        _walk_decl(src, child, prefix=prefix, exported=exported, out=out)


def _walk_decl(src: bytes, node: Node, *, prefix: str, exported: bool, out: list[_Decl]) -> None:
    if node.type == "interface_declaration":
        out.append(_build_interface(src, node, prefix=prefix, exported=exported))
    elif node.type == "type_alias_declaration":
        decl = _build_type_alias(src, node, prefix=prefix, exported=exported)
        if decl is not None:
            out.append(decl)
    elif node.type == "class_declaration":
        out.append(_build_class(src, node, prefix=prefix, exported=exported))
    elif node.type == "enum_declaration":
        out.append(_build_enum(src, node, prefix=prefix, exported=exported))


def _qualify(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _vis(exported: bool) -> str:
    return "public" if exported else "internal"


def _build_interface(src: bytes, node: Node, *, prefix: str, exported: bool) -> _Decl:
    name = _text(src, node.child_by_field_name("name")) or "?"
    body = node.child_by_field_name("body")
    bases = _heritage_bases(src, node)
    return _Decl(
        qualified=_qualify(prefix, name),
        name=name,
        kind="interface",
        visibility=_vis(exported),
        doc=_jsdoc(src, node),
        props=_object_props(src, body) if body is not None else [],
        bases=bases,
        decorators=[],
    )


def _build_type_alias(src: bytes, node: Node, *, prefix: str, exported: bool) -> _Decl | None:
    name = _text(src, node.child_by_field_name("name")) or "?"
    value = node.child_by_field_name("value")
    if value is not None and value.type == "object_type":
        return _Decl(
            qualified=_qualify(prefix, name),
            name=name,
            kind="type",
            visibility=_vis(exported),
            doc=_jsdoc(src, node),
            props=_object_props(src, value),
            bases=[],
            decorators=[],
        )
    # Alias de type non objet : pas de schéma propre, mais résolvable (inline).
    return _Decl(
        qualified=_qualify(prefix, name),
        name=name,
        kind="type",
        visibility=_vis(exported),
        doc="",
        props=[],
        bases=[],
        decorators=[],
        alias_ref=_type_ref(src, value),
    )


def _build_enum(src: bytes, node: Node, *, prefix: str, exported: bool) -> _Decl:
    name = _text(src, node.child_by_field_name("name")) or "?"
    body = node.child_by_field_name("body")
    values: list[Any] = []
    counter = 0
    if body is not None:
        for member in body.named_children:
            if member.type == "property_identifier":
                values.append(counter)
                counter += 1
            elif member.type == "enum_assignment":
                val = _literal_of(src, member.child_by_field_name("value"))
                values.append(val)
                if isinstance(val, int):
                    counter = val + 1
    return _Decl(
        qualified=_qualify(prefix, name),
        name=name,
        kind="enum",
        visibility=_vis(exported),
        doc=_jsdoc(src, node),
        props=[],
        bases=[],
        decorators=[],
        enum_values=values,
    )


def _build_class(src: bytes, node: Node, *, prefix: str, exported: bool) -> _Decl:
    name = _text(src, node.child_by_field_name("name")) or "?"
    body = node.child_by_field_name("body")
    props: list[_Prop] = []
    if body is not None:
        for member in body.named_children:
            if member.type == "public_field_definition":
                props.append(_field_prop(src, member))
            elif member.type == "method_definition":
                mname = _text(src, member.child_by_field_name("name"))
                if mname == "constructor":
                    props += _constructor_props(src, member)
    return _Decl(
        qualified=_qualify(prefix, name),
        name=name,
        kind="class",
        visibility=_vis(exported),
        doc=_jsdoc(src, node),
        props=props,
        bases=_heritage_bases(src, node),
        decorators=_decorators(src, node),
    )


def _field_prop(src: bytes, node: Node) -> _Prop:
    accessibility = next(
        (c for c in node.children if c.type == "accessibility_modifier"), None
    )
    visibility = _text(src, accessibility) if accessibility is not None else "public"
    return _Prop(
        name=_text(src, node.child_by_field_name("name")),
        type_ref=_type_ref(src, node.child_by_field_name("type")),
        optional=_has_question(node),
        visibility=visibility if visibility in ("public", "private", "protected") else "public",
        doc=_jsdoc(src, node),
        decorators=_decorators(src, node),
    )


def _constructor_props(src: bytes, node: Node) -> list[_Prop]:
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[_Prop] = []
    for param in params.named_children:
        if param.type != "required_parameter" and param.type != "optional_parameter":
            continue
        accessibility = next(
            (c for c in param.children if c.type == "accessibility_modifier"), None
        )
        readonly = any(c.type == "readonly" or _text(src, c) == "readonly" for c in param.children)
        if accessibility is None and not readonly:
            continue  # paramètre simple, pas une propriété de classe
        pattern = param.child_by_field_name("pattern")
        vis = _text(src, accessibility) if accessibility is not None else "public"
        out.append(
            _Prop(
                name=_text(src, pattern),
                type_ref=_type_ref(src, param.child_by_field_name("type")),
                optional=param.type == "optional_parameter" or _has_question(param),
                visibility=vis if vis in ("public", "private", "protected") else "public",
                doc="",
                decorators=[],
            )
        )
    return out


def _heritage_bases(src: bytes, node: Node) -> list[str]:
    bases: list[str] = []
    for child in node.named_children:
        if child.type == "class_heritage":
            for clause in child.named_children:
                bases += _clause_bases(src, clause)
        elif child.type in ("extends_type_clause", "implements_clause", "extends_clause"):
            bases += _clause_bases(src, child)
    return bases


def _clause_bases(src: bytes, clause: Node) -> list[str]:
    bases: list[str] = []
    for c in clause.named_children:
        if c.type in ("type_identifier", "identifier"):
            bases.append(_text(src, c))
        elif c.type == "generic_type":
            bases.append(_text(src, c.child_by_field_name("name")).rsplit(".", 1)[-1])
    return bases


# --- Construction du schéma --------------------------------------------------


@dataclass
class _Resolver:
    known: dict[str, str]
    aliases: dict[str, _TypeRef]


def _ref_schema(
    ref: _TypeRef, res: _Resolver, *, depth: int = 0
) -> tuple[dict[str, Any], bool, bool]:
    """(schéma, nullable, optional). Les marqueurs `null`/`undefined` d'une union
    sont portés par `ref.nullable`/`ref.optional` et répercutés ici."""
    schema, nullable, optional = _core_schema(ref, res, depth=depth)
    return schema, nullable or ref.nullable, optional or ref.optional


def _core_schema(ref: _TypeRef, res: _Resolver, *, depth: int) -> tuple[dict[str, Any], bool, bool]:
    if depth > 12:
        return {}, False, False
    if ref.kind in ("empty", "null", "undefined"):
        return {}, ref.kind == "null", ref.kind == "undefined"
    if ref.kind == "predefined":
        return dict(ref.predef), False, False
    if ref.kind == "array":
        inner, _, _ = _ref_schema(ref.inner or _TypeRef("empty"), res, depth=depth + 1)
        return {"type": "array", "items": inner}, False, False
    if ref.kind == "dict":
        value, _, _ = _ref_schema(ref.value or _TypeRef("empty"), res, depth=depth + 1)
        return {"type": "object", "additionalProperties": value}, False, False
    if ref.kind == "tuple":
        items = [_ref_schema(o, res, depth=depth + 1)[0] for o in ref.options]
        return {"type": "array", "prefixItems": items}, False, False
    if ref.kind == "enum":
        return {"enum": list(ref.literals)}, False, False
    if ref.kind == "object":
        return _inline_object(ref, res, depth=depth), False, False
    if ref.kind == "intersection":
        parts = [_ref_schema(o, res, depth=depth + 1)[0] for o in ref.options]
        return {"allOf": parts}, False, False
    if ref.kind == "union":
        options = [_ref_schema(o, res, depth=depth + 1)[0] for o in ref.options]
        return {"oneOf": options}, False, False
    if ref.kind == "unresolved":
        return {"x-unresolved-type": ref.expr}, False, False
    # named
    name = ref.name
    if name in _NAMED_PRIMITIVES:
        return dict(_NAMED_PRIMITIVES[name]), False, False
    if name in res.aliases:
        return _ref_schema(res.aliases[name], res, depth=depth + 1)
    if name in res.known:
        return {"$ref": f"{_slug(res.known[name])}.json"}, False, False
    return {"x-unresolved-type": name}, False, False


def _inline_object(ref: _TypeRef, res: _Resolver, *, depth: int) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for prop in ref.props:
        if prop.name == "[index]":
            schema["additionalProperties"] = _ref_schema(prop.type_ref, res, depth=depth + 1)[0]
            continue
        field_schema, is_req = _prop_schema(prop, res, depth=depth + 1)
        properties[prop.name] = field_schema
        if is_req:
            required.append(prop.name)
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = sorted(required)
    return schema


def _inject_null(schema: dict[str, Any]) -> dict[str, Any]:
    """Admet `null` dans le schéma (`| null` : facultatif et null admis)."""
    if "type" in schema:
        current = schema["type"]
        if isinstance(current, str) and current != "null":
            schema["type"] = [current, "null"]
        elif isinstance(current, list) and "null" not in current:
            schema["type"] = [*current, "null"]
    elif "enum" in schema:
        if None not in schema["enum"]:
            schema["enum"] = [*schema["enum"], None]
    elif "oneOf" in schema:
        if {"type": "null"} not in schema["oneOf"]:
            schema["oneOf"] = [*schema["oneOf"], {"type": "null"}]
    elif "$ref" in schema:
        schema = {"oneOf": [{"$ref": schema["$ref"]}, {"type": "null"}]}
    return schema


def _prop_schema(prop: _Prop, res: _Resolver, *, depth: int = 0) -> tuple[dict[str, Any], bool]:
    schema, nullable, opt_in_type = _ref_schema(prop.type_ref, res, depth=depth)
    schema = dict(schema)
    if nullable:
        schema = _inject_null(schema)
    schema["x-visibility"] = prop.visibility
    if prop.doc:
        schema["description"] = prop.doc
    if prop.decorators:
        schema["x-annotations"] = prop.decorators
    required = not (prop.optional or nullable or opt_in_type)
    return schema, required


def _build_schema(decl: _Decl, res: _Resolver) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$id": f"{_slug(decl.qualified)}.json",
        "title": decl.name,
        "x-language": "typescript",
        "x-kind": decl.kind,
        "x-visibility": decl.visibility,
    }
    if decl.doc:
        schema["description"] = decl.doc
    if decl.decorators:
        schema["x-annotations"] = decl.decorators
    if decl.kind == "enum":
        schema["enum"] = decl.enum_values
        return schema

    schema["type"] = "object"
    properties: dict[str, Any] = {}
    required: list[str] = []
    for prop in decl.props:
        if prop.name == "[index]":
            schema["additionalProperties"] = _ref_schema(prop.type_ref, res)[0]
            continue
        if not prop.name:
            continue
        field_schema, is_req = _prop_schema(prop, res)
        properties[prop.name] = field_schema
        if is_req:
            required.append(prop.name)
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = sorted(required)
    if decl.bases:
        schema["allOf"] = [
            {"$ref": f"{_slug(res.known[b])}.json"} if b in res.known
            else {"x-unresolved-type": b}
            for b in decl.bases
        ]
    return schema


def _grammar_for(path: str) -> str:
    return "tsx" if path.endswith(".tsx") else "typescript"


class TypeScriptParser:
    """Parseur TypeScript fondé sur tree-sitter. N'exécute jamais le code lu."""

    id = "typescript"
    cible = CIBLE_RAW
    label = "TypeScript"
    version = f"1.0-ts{grammar_version('typescript')}"

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        from ossie_studio.sources.collect_core import schema_plugin_run

        return schema_plugin_run(context, self)

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path) and not path.endswith(".d.ts")

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        decls: list[_Decl] = []
        sources: dict[str, set[str]] = {}
        seen: set[str] = set()
        for path, raw in sorted(files.items()):
            if path.endswith(".d.ts"):
                continue
            module = _module_of(path)
            try:
                tree = ts_parse(_grammar_for(path), raw)
            except Exception as exc:  # pragma: no cover - tree-sitter ne lève pas
                raise ParserError("syntax") from exc
            for decl in _extract_file(raw, tree.root_node):
                qualified = f"{module}.{decl.qualified}" if module else decl.qualified
                decl.qualified = qualified
                if qualified not in seen:
                    seen.add(qualified)
                    decls.append(decl)
                sources.setdefault(qualified, set()).add(path)
        known: dict[str, str] = {}
        aliases: dict[str, _TypeRef] = {}
        for decl in decls:
            known.setdefault(decl.name, decl.qualified)
            known[decl.qualified] = decl.qualified
            if decl.alias_ref is not None:
                aliases[decl.name] = decl.alias_ref
                aliases[decl.qualified] = decl.alias_ref
        res = _Resolver(known=known, aliases=aliases)
        objects: list[ParsedObject] = []
        for decl in sorted(decls, key=lambda d: d.qualified):
            if decl.alias_ref is not None:
                continue  # alias non objet : pas de schéma propre
            objects.append(
                ParsedObject(
                    key=_slug(decl.qualified),
                    sources=sorted(sources[decl.qualified]),
                    schema=dump_schema(_build_schema(decl, res)),
                )
            )
        return objects
