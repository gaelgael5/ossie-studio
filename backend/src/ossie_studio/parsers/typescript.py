"""Parseur `typescript` : lit les fichiers `.ts` et `.tsx` (hors `.d.ts` par
défaut) et produit un schéma par **interface**, **alias de type objet**
(`type X = { … }`), **classe** et **enum**.

Analyse **syntaxique** (tree-sitter), jamais d'exécution du code lu. Un type venu
d'un autre fichier ou d'un paquet (`node_modules`) devient `x-unresolved-type` ;
la résolution croisée viendra du service de recherche de schéma commun. Les
références au sein du même groupe de fichiers sont résolues en `$ref` ; un alias
de type non objet du même groupe est résolu et inliné.

Cartographie applicative (ticket f619f4a7) : les méthodes des classes exportées
(jamais `private` ni `#privées`) vont dans leur `<clé>.extend.yaml`, avec leurs
points d'entrée NestJS ; les fonctions exportées et les routes Express sont notées
par fichier dans l'index `functions-typescript.yaml`, que le socle range dans le
`.extend.yaml` du module (`parsers.ts_entrypoints`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Node

from ossie_studio.parsers.application import dump_functions
from ossie_studio.parsers.base import (
    CIBLE_RAW,
    OutputAction,
    ParsedObject,
    ParserError,
    PluginContext,
    PluginParam,
    PluginResult,
    matches_globs,
)
from ossie_studio.parsers.conventions import (
    SchemaReadError,
    dump_schema,
    load_schema,
    schema_file,
)
from ossie_studio.parsers.treesitter import grammar_version, node_text
from ossie_studio.parsers.treesitter import parse as ts_parse
from ossie_studio.parsers.ts_entrypoints import (
    ROUTER_FACTORIES,
    Decorator,
    is_router,
    nest_entry_point,
    router_entry_point,
)

_GLOBS = ["**/*.ts", "**/*.tsx"]
# Fonctions exportées et routes Express, par fichier d'origine (rangées par le socle).
FUNCTIONS_INDEX = "functions-typescript.yaml"
_FUNCTIONS_VERSION = 1
_WRAPPERS = frozenset({"Promise", "Observable", "PromiseLike"})
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
    namespace: str = ""
    methods: list[_Func] = field(default_factory=list)


@dataclass
class _Param:
    name: str
    type_text: str
    type_ref: _TypeRef
    optional: bool


@dataclass
class _Func:
    """Une méthode ou une fonction : signature, emplacement, point d'entrée."""

    name: str
    visibility: str
    params: list[_Param]
    returns_text: str
    result: _TypeRef | None
    static: bool
    is_async: bool
    doc: str
    decorators: list[str]
    line: int
    entry: dict[str, Any] | None = None
    file: str = ""


# --- Navigation --------------------------------------------------------------


def _text(src: bytes, node: Node | None) -> str:
    return node_text(src, node) if node is not None else ""


def _jsdoc(src: bytes, node: Node) -> str:
    prev = node.prev_sibling
    # Une déclaration exportée est enveloppée : le JSDoc précède l'`export`.
    if (prev is None or prev.type != "comment") and node.parent is not None:
        if node.parent.type == "export_statement":
            prev = node.parent.prev_sibling
    return _comment_doc(src, prev)


def _comment_doc(src: bytes, prev: Node | None) -> str:
    """Le texte d'un commentaire JSDoc (`/** … */`), "" sinon."""
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
    decl = _Decl(
        qualified=_qualify(prefix, name),
        name=name,
        kind="class",
        visibility=_vis(exported),
        doc=_jsdoc(src, node),
        props=props,
        bases=_heritage_bases(src, node),
        decorators=_decorators(src, node),
    )
    if exported and body is not None:
        decl.methods = _methods(src, body, owner=_class_decorators(src, node))
    return decl


# --- Méthodes, fonctions, points d'entrée (cartographie applicative) ---------


def _raw_decorator(src: bytes, node: Node) -> Decorator | None:
    expr = node.named_children[0] if node.named_children else None
    if expr is None:
        return None
    if expr.type == "call_expression":
        name = _text(src, expr.child_by_field_name("function"))
        args_node = expr.child_by_field_name("arguments")
        args = tuple(_text(src, a) for a in args_node.named_children) if args_node else ()
        return Decorator(name.rsplit(".", 1)[-1], args)
    return Decorator(_text(src, expr).rsplit(".", 1)[-1])


def _class_decorators(src: bytes, node: Node) -> list[Decorator]:
    """Les décorateurs d'une classe : les siens, et ceux portés par son `export`."""
    holders = [node]
    if node.parent is not None and node.parent.type == "export_statement":
        holders.append(node.parent)
    found = [
        _raw_decorator(src, c) for h in holders for c in h.named_children if c.type == "decorator"
    ]
    return [d for d in found if d is not None]


def _params(src: bytes, node: Node | None) -> list[_Param]:
    params: list[_Param] = []
    for param in node.named_children if node is not None else []:
        if param.type not in ("required_parameter", "optional_parameter"):
            continue
        type_node = param.child_by_field_name("type")
        has_type = type_node is not None and bool(type_node.named_children)
        inner = type_node.named_children[0] if type_node is not None and has_type else None
        params.append(
            _Param(
                name=_text(src, param.child_by_field_name("pattern")),
                type_text=re.sub(r"\s+", " ", _text(src, inner)).strip(),
                type_ref=_type_ref(src, type_node) if type_node is not None else _TypeRef("empty"),
                optional=param.type == "optional_parameter"
                or param.child_by_field_name("value") is not None
                or _has_question(param),
            )
        )
    return params


def _result(src: bytes, annotation: Node | None) -> tuple[str, _TypeRef | None, bool]:
    """(type tel qu'écrit, vrai résultat, asynchrone) : `Promise<T>` → `T`."""
    has_type = annotation is not None and bool(annotation.named_children)
    node = annotation.named_children[0] if annotation is not None and has_type else None
    if node is None:
        return "", None, False
    text = re.sub(r"\s+", " ", _text(src, node)).strip()
    wrapped = False
    while node is not None and node.type == "generic_type":
        base = _text(src, node.child_by_field_name("name")).rsplit(".", 1)[-1]
        args = node.child_by_field_name("type_arguments")
        if base not in _WRAPPERS or args is None or not args.named_children:
            break
        node = args.named_children[0]
        wrapped = True
    if node is None or _text(src, node) in ("void", "undefined", "never"):
        return text, None, wrapped
    return text, _type_ref(src, node), wrapped


def _member_doc(src: bytes, member: Node) -> str:
    """Le JSDoc d'une méthode : il précède ses décorateurs."""
    prev = member.prev_sibling
    while prev is not None and prev.type == "decorator":
        prev = prev.prev_sibling
    return _comment_doc(src, prev)


def _methods(src: bytes, body: Node, *, owner: list[Decorator]) -> list[_Func]:
    """Les méthodes d'une classe exportée, sauf `private`, `#privées`, accesseurs
    et constructeur ; les décorateurs d'une méthode la précèdent dans le corps."""
    methods: list[_Func] = []
    pending: list[Decorator] = []
    for member in body.named_children:
        if member.type == "decorator":
            found = _raw_decorator(src, member)
            if found is not None:
                pending.append(found)
            continue
        if member.type == "comment":
            continue
        decorators, pending = pending, []
        if member.type != "method_definition":
            continue
        name_node = member.child_by_field_name("name")
        tokens = {c.type for c in member.children}
        accessibility = next(
            (c for c in member.children if c.type == "accessibility_modifier"), None
        )
        visibility = _text(src, accessibility) if accessibility is not None else "public"
        if (
            name_node is None
            or name_node.type == "private_property_identifier"
            or _text(src, name_node) == "constructor"
            or visibility == "private"
            or tokens & {"get", "set"}
        ):
            continue
        text, result, wrapped = _result(src, member.child_by_field_name("return_type"))
        methods.append(
            _Func(
                name=_text(src, name_node),
                visibility=visibility if visibility in ("public", "protected") else "public",
                params=_params(src, member.child_by_field_name("parameters")),
                returns_text=text,
                result=result,
                static="static" in tokens,
                is_async="async" in tokens or wrapped,
                doc=_member_doc(src, member),
                decorators=[d.name for d in decorators],
                line=name_node.start_point[0] + 1,
                entry=nest_entry_point(decorators, owner),
            )
        )
    return methods


def _function(src: bytes, name: str, fn: Node, holder: Node) -> _Func:
    """Une fonction exportée (`function`, ou fonction fléchée affectée)."""
    text, result, wrapped = _result(src, fn.child_by_field_name("return_type"))
    return _Func(
        name=name,
        visibility="public",
        params=_params(src, fn.child_by_field_name("parameters")),
        returns_text=text,
        result=result,
        static=False,
        is_async=any(c.type == "async" for c in fn.children) or wrapped,
        doc=_jsdoc(src, holder),
        decorators=[],
        line=holder.start_point[0] + 1,
    )


def _exported_functions(src: bytes, root: Node) -> list[_Func]:
    out: list[_Func] = []
    for statement in root.named_children:
        if statement.type != "export_statement":
            continue
        decl = statement.child_by_field_name("declaration") or next(
            (c for c in statement.named_children if c.type == "function_declaration"), None
        )
        if decl is None:
            continue
        if decl.type in ("function_declaration", "generator_function_declaration"):
            name = _text(src, decl.child_by_field_name("name")) or "default"
            out.append(_function(src, name, decl, decl))
        elif decl.type == "lexical_declaration":
            for declarator in decl.named_children:
                value = declarator.child_by_field_name("value")
                functions = ("arrow_function", "function_expression", "function")
                if (
                    declarator.type == "variable_declarator"
                    and value is not None
                    and value.type in functions
                ):
                    name = _text(src, declarator.child_by_field_name("name"))
                    out.append(_function(src, name, value, decl))
    return out


def _created_routers(src: bytes, root: Node) -> set[str]:
    """Les variables créées par une fabrique de routeur (`const app = express()`)."""
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            callee = None
            if value is not None and value.type in ("call_expression", "new_expression"):
                callee = value.child_by_field_name("function") or value.child_by_field_name(
                    "constructor"
                )
            if callee is not None and _text(src, callee).rsplit(".", 1)[-1] in ROUTER_FACTORIES:
                found.add(_text(src, node.child_by_field_name("name")))
        stack.extend(node.named_children)
    return found


def _routes(src: bytes, root: Node) -> list[_Func]:
    """Les routes Express et semblables d'un fichier, comme fonctions du module."""
    created = _created_routers(src, root)
    routes: list[_Func] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        if callee is None or callee.type != "member_expression" or args_node is None:
            continue
        target = callee.child_by_field_name("object")
        prop = callee.child_by_field_name("property")
        if target is None or target.type != "identifier":
            continue
        if not is_router(_text(src, target), created):
            continue
        args = [a for a in args_node.named_children if a.type != "comment"]
        entry = router_entry_point(_text(src, prop), [_text(src, a) for a in args])
        if entry is None:
            continue
        handler = args[-1]
        name = (
            _text(src, handler)
            if handler.type in ("identifier", "member_expression")
            else f"{entry['verb']} {entry['route'] or '?'}"
        )
        routes.append(
            _Func(
                name=name,
                visibility="public",
                params=[],
                returns_text="",
                result=None,
                static=False,
                is_async=False,
                doc="",
                decorators=[],
                line=node.start_point[0] + 1,
                entry=entry,
            )
        )
    return routes


def _function_entry(fn: _Func, res: _Resolver) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": fn.name, "visibility": fn.visibility}
    if fn.static:
        entry["static"] = True
    if fn.is_async:
        entry["async"] = True
    if fn.params:
        entry["parameters"] = []
        for param in fn.params:
            item: dict[str, Any] = {"name": param.name}
            if param.type_text:
                item["type"] = param.type_text
                item["schema"] = _ref_schema(param.type_ref, res)[0]
            if param.optional:
                item["optional"] = True
            entry["parameters"].append(item)
    if fn.result is not None:
        entry["returns"] = {"type": fn.returns_text, "schema": _ref_schema(fn.result, res)[0]}
    if fn.doc:
        entry["description"] = fn.doc
    if fn.decorators:
        entry["annotations"] = fn.decorators
    if fn.file:
        entry["source"] = {"file": fn.file, "line": fn.line}
    if fn.entry:
        entry["entryPoint"] = dict(fn.entry)
    return entry


def file_functions(path: str, raw: bytes) -> list[dict[str, Any]]:
    """Les fonctions exportées et les routes d'un fichier, au format de raw."""
    if path.endswith(".d.ts"):
        return []
    tree = ts_parse(_grammar_for(path), raw)
    found = _exported_functions(raw, tree.root_node) + _routes(raw, tree.root_node)
    res = _Resolver(known={}, aliases={})
    out = []
    for fn in found:
        fn.file = path
        out.append(_function_entry(fn, res))
    return out


def _functions_index(
    previous_text: str | None, files: dict[str, bytes], crcs: dict[str, str], force: bool
) -> str:
    """L'index `functions-typescript.yaml` : par fichier, son empreinte et ses
    fonctions. Un fichier inchangé reprend son entrée."""
    previous: dict[str, Any] = {}
    if previous_text:
        try:
            data = load_schema(previous_text)
        except SchemaReadError:
            data = None
        if isinstance(data, dict) and data.get("version") == _FUNCTIONS_VERSION:
            previous = data.get("files") or {}
    entries: dict[str, Any] = {}
    for path in sorted(files):
        old = previous.get(path)
        if not force and isinstance(old, dict) and old.get("checksum") == crcs.get(path):
            entries[path] = old
            continue
        functions = file_functions(path, files[path])
        if functions:
            entries[path] = {"checksum": crcs.get(path, ""), "functions": functions}
    return dump_schema({"language": "typescript", "version": _FUNCTIONS_VERSION, "files": entries})


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
        return {"$ref": schema_file(_slug(res.known[name]))}, False, False
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
        "$id": schema_file(_slug(decl.qualified)),
        "title": decl.name,
        "x-language": "typescript",
        "x-kind": decl.kind,
        "x-visibility": decl.visibility,
    }
    # Espace de noms d'origine (namespace C#, paquet Java/Kotlin, module TS/Python),
    # affiché tel quel par l'explorateur raw : le `$id`, slugifié, ne le rend pas.
    if decl.namespace:
        schema["x-namespace"] = decl.namespace
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
            {"$ref": schema_file(_slug(res.known[b]))} if b in res.known
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
    # 1.2 : méthodes, fonctions et points d'entrée (cartographie applicative).
    version = f"1.2-ts{grammar_version('typescript')}"

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        from ossie_studio.sources.collect_core import schema_plugin_run

        result = schema_plugin_run(context, self)
        # Fonctions exportées et routes : l'index du plugin, par fichier ; le socle
        # les range dans le `.extend.yaml` de leur module.
        previous = context.raw.read_index(FUNCTIONS_INDEX)
        crcs = {row.path: row.crc for row in context.files}
        text = _functions_index(previous, context.contents, crcs, context.force)
        if text != previous:
            result.writes.append(OutputAction(path=FUNCTIONS_INDEX, content=text))
        return result

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
                for method in decl.methods:
                    method.file = path
                qualified = f"{module}.{decl.qualified}" if module else decl.qualified
                decl.qualified = qualified
                decl.namespace = module
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
            methods = {"methods": [_function_entry(m, res) for m in decl.methods]}
            objects.append(
                ParsedObject(
                    key=_slug(decl.qualified),
                    sources=sorted(sources[decl.qualified]),
                    schema=dump_schema(_build_schema(decl, res)),
                    extend=dump_functions(methods) if decl.methods else None,
                )
            )
        return objects
