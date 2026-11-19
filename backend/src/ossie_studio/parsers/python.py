"""Parseur `python` : lit les fichiers `.py` et produit un schéma par **classe de
données** — dataclass, modèle pydantic, classe attrs, `TypedDict`, `NamedTuple`,
`Enum`, et classe ordinaire portant des attributs de classe annotés.

Analyse **syntaxique** (tree-sitter), jamais d'exécution ni d'import du code lu
(code tiers, non fiable). Une classe venue d'un autre fichier ou d'une
bibliothèque devient `x-unresolved-type` ; la résolution croisée viendra du
service de recherche de schéma commun. Les références au sein du même groupe de
fichiers sont résolues en `$ref`.
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

_GLOBS = ["**/*.py"]
_SLUG = re.compile(r"[^a-zA-Z0-9]+")

# Correspondance des types Python vers JSON Schema.
_PRIMITIVES: dict[str, dict[str, Any]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "bytes": {"type": "string", "format": "byte"},
    "Decimal": {"type": "number"},
    "complex": {"type": "string"},
    "UUID": {"type": "string", "format": "uuid"},
    "datetime": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
    "time": {"type": "string", "format": "time"},
    "Any": {},
    "object": {},
    "None": {"type": "null"},
    "NoneType": {"type": "null"},
}
_LIST_TYPES = frozenset(
    {"list", "List", "Sequence", "MutableSequence", "set", "Set", "frozenset",
     "FrozenSet", "MutableSet", "Collection", "Iterable", "tuple", "Tuple"}
)
_DICT_TYPES = frozenset(
    {"dict", "Dict", "Mapping", "MutableMapping", "OrderedDict", "DefaultDict", "defaultdict"}
)
_OPTIONAL = frozenset({"Optional"})
_UNION = frozenset({"Union"})
_LITERAL = frozenset({"Literal"})
_ANNOTATED = frozenset({"Annotated"})
_NOTREQUIRED = frozenset({"NotRequired"})
_REQUIRED = frozenset({"Required"})
_CLASSVAR = frozenset({"ClassVar"})

_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})
_MODEL_BASES = frozenset({"BaseModel"})
_TYPEDDICT_BASES = frozenset({"TypedDict"})
_NAMEDTUPLE_BASES = frozenset({"NamedTuple"})
# Bases de « cadre » : donnent le genre, pas un parent résolu en $ref.
_FRAMEWORK_BASES = (
    _ENUM_BASES
    | _MODEL_BASES
    | _TYPEDDICT_BASES
    | _NAMEDTUPLE_BASES
    | {"object", "Generic", "Protocol", "ABC", "ABCMeta"}
)
_DATACLASS_DECORATORS = frozenset({"dataclass"})
_ATTRS_DECORATORS = frozenset({"s", "attrs", "attr", "define", "mutable", "frozen", "attributes"})


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


def _obj_visibility(name: str) -> str:
    """Python n'a pas de modificateur d'accès : la convention du souligné fait foi.
    Une classe `_Foo` est interne au module."""
    return "internal" if name.startswith("_") else "public"


def _field_visibility(name: str) -> str:
    """Un attribut `_x` est protégé par convention ; sinon public."""
    return "protected" if name.startswith("_") else "public"


def _module_of(path: str) -> str:
    """Nom de module dérivé du chemin, pour un identifiant d'objet stable et sans
    collision entre fichiers d'une même source (Python n'a pas d'espace de noms)."""
    rel = path.replace("\\", "/").lstrip("./")
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [p for p in rel.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


# --- Représentation intermédiaire (sans nœud tree-sitter) --------------------


@dataclass
class _TypeRef:
    kind: str  # named | array | dict | union | literal | empty
    name: str = ""
    nullable: bool = False
    inner: _TypeRef | None = None
    value: _TypeRef | None = None
    options: list[_TypeRef] = field(default_factory=list)
    literals: list[Any] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)


@dataclass
class _Member:
    name: str
    type_ref: _TypeRef
    doc: str
    has_default: bool
    annotations: list[dict[str, Any]]
    constraints: dict[str, Any]
    not_required: bool = False
    required_flag: bool = False


@dataclass
class _Type:
    qualified: str
    name: str
    kind: str
    doc: str
    bases: list[str]
    decorators: list[dict[str, Any]]
    total: bool = True
    members: list[_Member] = field(default_factory=list)
    enum_members: list[str] = field(default_factory=list)


# --- Navigation de l'arbre ---------------------------------------------------


def _text(src: bytes, node: Node | None) -> str:
    return node_text(src, node) if node is not None else ""


def _base_name(src: bytes, node: Node | None) -> str:
    """Dernier segment d'un nom (identifiant, attribut `a.b.C`, ou souscription)."""
    if node is None:
        return ""
    if node.type == "identifier":
        return _text(src, node)
    if node.type == "attribute":
        return _text(src, node.child_by_field_name("attribute"))
    if node.type in ("subscript", "generic_type"):
        target = node.child_by_field_name("value") or (
            node.named_children[0] if node.named_children else None
        )
        return _base_name(src, target) if target is not None else _text(src, node)
    return _text(src, node)


def _docstring(src: bytes, body: Node | None) -> str:
    if body is None:
        return ""
    for child in body.named_children:
        if child.type == "expression_statement" and child.named_children:
            first = child.named_children[0]
            if first.type == "string":
                content = next(
                    (c for c in first.named_children if c.type == "string_content"), None
                )
                return re.sub(r"\s+", " ", _text(src, content)).strip()
        break
    return ""


def _decorators(src: bytes, deco_nodes: list[Node]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for deco in deco_nodes:
        expr = deco.named_children[0] if deco.named_children else None
        if expr is None:
            continue
        if expr.type == "call":
            name = _base_name(src, expr.child_by_field_name("function"))
            args = _call_args_text(src, expr.child_by_field_name("arguments"))
        else:
            name = _base_name(src, expr)
            args = []
        result.append({"nom": name, "arguments": args})
    return result


def _call_args_text(src: bytes, arglist: Node | None) -> list[str]:
    if arglist is None:
        return []
    return [_text(src, c) for c in arglist.named_children]


# --- Types -------------------------------------------------------------------


def _generic_parts(node: Node) -> tuple[Node | None, list[Node]]:
    """Base et arguments d'un `generic_type`/`subscript`."""
    base = None
    params: Node | None = None
    for child in node.named_children:
        if child.type in ("type_parameter", "subscript_arguments"):
            params = child
        elif base is None:
            base = child
    args: list[Node] = []
    if params is not None:
        args = [c for c in params.named_children]
    elif node.type == "subscript":
        value = node.child_by_field_name("value")
        base = base or value
        args = [c for c in node.named_children if c is not value]
    return base, args


def _literal_value(src: bytes, node: Node) -> Any:
    kind = node.type
    if kind == "type" and node.named_children:
        return _literal_value(src, node.named_children[0])
    if kind == "string":
        content = next((c for c in node.named_children if c.type == "string_content"), None)
        return _text(src, content)
    if kind == "integer":
        try:
            return int(_text(src, node))
        except ValueError:
            return _text(src, node)
    if kind in ("true", "false"):
        return kind == "true"
    if kind == "none":
        return None
    return _text(src, node)


def _type_ref(src: bytes, node: Node | None) -> _TypeRef:
    if node is None:
        return _TypeRef("empty")
    if node.type == "type":
        inner = node.named_children[0] if node.named_children else None
        return _type_ref(src, inner)
    if node.type == "none":
        return _TypeRef("named", name="None")
    if node.type == "string":
        # Annotation en chaîne (`x: "Autre"` ou `from __future__ import annotations`).
        content = next((c for c in node.named_children if c.type == "string_content"), None)
        return _TypeRef("named", name=_text(src, content).strip())
    if node.type == "binary_operator":
        operands = _union_operands(src, node)
        return _union_ref(operands)
    if node.type in ("subscript", "generic_type"):
        base_node, arg_nodes = _generic_parts(node)
        base = _base_name(src, base_node) if base_node is not None else ""
        args = [a for a in arg_nodes if a.type not in ("comment",)]
        if base in _OPTIONAL and args:
            ref = _type_ref(src, args[0])
            ref.nullable = True
            return ref
        if base in _UNION and args:
            return _union_ref([_type_ref(src, a) for a in args])
        if base in _LITERAL:
            return _TypeRef("literal", literals=[_literal_value(src, a) for a in args])
        if base in _ANNOTATED and args:
            ref = _type_ref(src, args[0])
            ref.annotations += [_text(src, a) for a in args[1:]]
            return ref
        if base in (_NOTREQUIRED | _REQUIRED) and args:
            return _type_ref(src, args[0])
        if base in _LIST_TYPES:
            item_ref = _type_ref(src, args[0]) if args else _TypeRef("empty")
            return _TypeRef("array", inner=item_ref)
        if base in _DICT_TYPES:
            value = _type_ref(src, args[1]) if len(args) >= 2 else _TypeRef("empty")
            return _TypeRef("dict", value=value)
        if base in _CLASSVAR and args:
            return _type_ref(src, args[0])
        return _TypeRef("named", name=base)
    if node.type in ("identifier", "attribute"):
        return _TypeRef("named", name=_base_name(src, node))
    return _TypeRef("named", name=_text(src, node))


def _union_operands(src: bytes, node: Node) -> list[_TypeRef]:
    out: list[_TypeRef] = []
    for field_name in ("left", "right"):
        child = node.child_by_field_name(field_name)
        if child is None:
            continue
        if child.type == "binary_operator":
            out += _union_operands(src, child)
        else:
            out.append(_type_ref(src, child))
    return out


def _union_ref(operands: list[_TypeRef]) -> _TypeRef:
    nullable = any(o.kind == "named" and o.name in ("None", "NoneType") for o in operands)
    rest = [o for o in operands if not (o.kind == "named" and o.name in ("None", "NoneType"))]
    if not rest:
        return _TypeRef("named", name="None", nullable=nullable)
    if len(rest) == 1:
        rest[0].nullable = rest[0].nullable or nullable
        return rest[0]
    return _TypeRef("union", options=rest, nullable=nullable)


# --- Extraction --------------------------------------------------------------


def _extract_file(src: bytes, root: Node) -> list[_Type]:
    out: list[_Type] = []
    _walk(src, root, enclosing="", decorators=[], out=out)
    return out


def _walk(
    src: bytes, node: Node, *, enclosing: str, decorators: list[Node], out: list[_Type]
) -> None:
    pending: list[Node] = []
    for child in node.named_children:
        if child.type == "decorator":
            pending.append(child)
            continue
        if child.type == "decorated_definition":
            decos = [c for c in child.named_children if c.type == "decorator"]
            definition = child.child_by_field_name("definition")
            if definition is not None and definition.type == "class_definition":
                _emit_class(src, definition, enclosing=enclosing, decos=decos, out=out)
            pending = []
            continue
        if child.type == "class_definition":
            _emit_class(src, child, enclosing=enclosing, decos=pending, out=out)
            pending = []
            continue
        pending = []


def _emit_class(
    src: bytes, node: Node, *, enclosing: str, decos: list[Node], out: list[_Type]
) -> None:
    name = _text(src, node.child_by_field_name("name")) or "?"
    qualified = f"{enclosing}.{name}" if enclosing else name
    bases, total = _bases_and_total(src, node.child_by_field_name("superclasses"))
    kind = _kind_of(bases, _decorators(src, decos))
    body = node.child_by_field_name("body")
    info = _Type(
        qualified=qualified,
        name=name,
        kind=kind,
        doc=_docstring(src, body),
        bases=[b for b in bases if b not in _FRAMEWORK_BASES],
        decorators=_decorators(src, decos),
        total=total,
    )
    if kind == "enum":
        info.enum_members = _enum_members(src, body)
    else:
        info.members = _members(src, body)
    out.append(info)
    if body is not None:
        deeper = f"{enclosing}.{name}" if enclosing else name
        _walk(src, body, enclosing=deeper, decorators=[], out=out)


def _bases_and_total(src: bytes, superclasses: Node | None) -> tuple[list[str], bool]:
    if superclasses is None:
        return [], True
    bases: list[str] = []
    total = True
    for child in superclasses.named_children:
        if child.type == "keyword_argument":
            kw = _text(src, child.child_by_field_name("name"))
            val = _text(src, child.child_by_field_name("value"))
            if kw == "total" and val == "False":
                total = False
            continue
        if child.type == "comment":
            continue
        bases.append(_base_name(src, child))
    return bases, total


def _kind_of(bases: list[str], decorators: list[dict[str, Any]]) -> str:
    if any(b in _ENUM_BASES for b in bases):
        return "enum"
    if any(b in _MODEL_BASES for b in bases):
        return "model"
    if any(b in _TYPEDDICT_BASES for b in bases):
        return "typeddict"
    if any(b in _NAMEDTUPLE_BASES for b in bases):
        return "namedtuple"
    names = {d["nom"] for d in decorators}
    if names & _DATACLASS_DECORATORS:
        return "dataclass"
    if names & _ATTRS_DECORATORS:
        return "attrs"
    return "class"


def _enum_members(src: bytes, body: Node | None) -> list[str]:
    if body is None:
        return []
    members: list[str] = []
    for stmt in body.named_children:
        if stmt.type != "expression_statement" or not stmt.named_children:
            continue
        assign = stmt.named_children[0]
        if assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        if left is None or left.type != "identifier":
            continue
        name = _text(src, left)
        if name.startswith("__") or assign.child_by_field_name("type") is not None:
            continue
        members.append(name)
    return members


def _members(src: bytes, body: Node | None) -> list[_Member]:
    if body is None:
        return []
    members: list[_Member] = []
    for stmt in body.named_children:
        if stmt.type != "expression_statement" or not stmt.named_children:
            continue
        assign = stmt.named_children[0]
        if assign.type != "assignment":
            continue
        type_node = assign.child_by_field_name("type")
        left = assign.child_by_field_name("left")
        if type_node is None or left is None or left.type != "identifier":
            continue  # non annoté → ignoré (attribut de classe simple, pas un champ)
        name = _text(src, left)
        if name.startswith("__"):
            continue
        raw_inner = type_node.named_children[0] if type_node.named_children else None
        if raw_inner is not None and raw_inner.type in ("subscript", "generic_type"):
            base_node, _ = _generic_parts(raw_inner)
            if base_node is not None and _base_name(src, base_node) in _CLASSVAR:
                continue  # ClassVar : variable de classe, pas un champ d'instance
        not_required = bool(
            raw_inner is not None
            and raw_inner.type in ("subscript", "generic_type")
            and _subscript_base(src, raw_inner) in _NOTREQUIRED
        )
        required_flag = bool(
            raw_inner is not None
            and raw_inner.type in ("subscript", "generic_type")
            and _subscript_base(src, raw_inner) in _REQUIRED
        )
        type_ref = _type_ref(src, type_node)
        right = assign.child_by_field_name("right")
        has_default = right is not None
        constraints: dict[str, Any] = {}
        if right is not None and right.type == "call":
            has_default, constraints = _field_call(src, right)
        members.append(
            _Member(
                name=name,
                type_ref=type_ref,
                doc="",
                has_default=has_default,
                annotations=[{"nom": a} for a in type_ref.annotations],
                constraints=constraints,
                not_required=not_required,
                required_flag=required_flag,
            )
        )
    return members


def _subscript_base(src: bytes, node: Node) -> str:
    base_node, _ = _generic_parts(node)
    return _base_name(src, base_node) if base_node is not None else ""


_FIELD_FUNCS = frozenset({"Field", "field", "ib", "attrib"})
_FIELD_KEYS = {
    "max_length": "maxLength",
    "min_length": "minLength",
    "ge": "minimum",
    "le": "maximum",
    "gt": "exclusiveMinimum",
    "lt": "exclusiveMaximum",
    "pattern": "pattern",
    "regex": "pattern",
    "description": "description",
    "alias": "x-alias",
}


def _field_call(src: bytes, call: Node) -> tuple[bool, dict[str, Any]]:
    """Lit un appel `Field(...)`/`field(...)`/`attr.ib(...)` : valeur par défaut et
    contraintes. `Field(...)` (ellipsis seule) reste obligatoire."""
    func = _base_name(src, call.child_by_field_name("function"))
    if func not in _FIELD_FUNCS:
        return True, {}
    arglist = call.child_by_field_name("arguments")
    has_default = False
    constraints: dict[str, Any] = {}
    if arglist is None:
        return True, {}
    for arg in arglist.named_children:
        if arg.type == "ellipsis":
            continue
        if arg.type == "keyword_argument":
            key = _text(src, arg.child_by_field_name("name"))
            value = arg.child_by_field_name("value")
            if key in ("default", "default_factory"):
                has_default = True
            elif key in _FIELD_KEYS and value is not None:
                constraints[_FIELD_KEYS[key]] = _literal_value(src, value)
            continue
        # Premier argument positionnel non-ellipsis → valeur par défaut.
        has_default = True
    return has_default, constraints


# --- Construction du schéma --------------------------------------------------


def _ref_schema(ref: _TypeRef, *, known: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """(schéma, nullable)."""
    if ref.kind == "empty":
        return {}, ref.nullable
    if ref.kind == "array":
        inner, _ = _ref_schema(ref.inner or _TypeRef("empty"), known=known)
        return {"type": "array", "items": inner}, ref.nullable
    if ref.kind == "dict":
        value, _ = _ref_schema(ref.value or _TypeRef("empty"), known=known)
        return {"type": "object", "additionalProperties": value}, ref.nullable
    if ref.kind == "literal":
        return {"enum": list(ref.literals)}, ref.nullable
    if ref.kind == "union":
        options = [_ref_schema(o, known=known)[0] for o in ref.options]
        return {"oneOf": options}, ref.nullable
    name = ref.name
    if name in _PRIMITIVES:
        return dict(_PRIMITIVES[name]), ref.nullable
    if name in known:
        return {"$ref": f"{_slug(known[name])}.json"}, ref.nullable
    return {"x-unresolved-type": name}, ref.nullable


def _member_schema(member: _Member, *, known: dict[str, str]) -> tuple[dict[str, Any], bool]:
    schema, nullable = _ref_schema(member.type_ref, known=known)
    schema = dict(schema)
    schema["x-visibility"] = _field_visibility(member.name)
    if member.doc:
        schema["description"] = member.doc
    schema.update(member.constraints)
    if member.annotations:
        schema["x-annotations"] = member.annotations
    if member.required_flag:
        required = True
    elif member.not_required or nullable or member.has_default:
        required = False
    else:
        required = True
    return schema, required


def _build_schema(info: _Type, *, total: bool, known: dict[str, str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$id": f"{_slug(info.qualified)}.json",
        "title": info.name,
        "x-language": "python",
        "x-kind": info.kind,
        "x-visibility": _obj_visibility(info.name),
    }
    if info.doc:
        schema["description"] = info.doc
    if info.decorators:
        schema["x-annotations"] = info.decorators
    if info.kind == "enum":
        schema["enum"] = info.enum_members
        return schema

    schema["type"] = "object"
    properties: dict[str, Any] = {}
    required: list[str] = []
    for member in info.members:
        if not member.name:
            continue
        prop, is_required = _member_schema(member, known=known)
        properties[member.name] = prop
        if is_required and total:
            required.append(member.name)
        elif is_required and not total and member.required_flag:
            required.append(member.name)
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = sorted(required)
    if info.bases:
        schema["allOf"] = [
            {"$ref": f"{_slug(known[b])}.json"} if b in known else {"x-unresolved-type": b}
            for b in info.bases
        ]
    return schema


class PythonParser:
    """Parseur Python fondé sur tree-sitter. N'exécute ni n'importe jamais le code."""

    id = "python"
    cible = CIBLE_RAW
    label = "Python"
    version = f"1.0-ts{grammar_version('python')}"

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return []

    def run(self, context: PluginContext) -> PluginResult:
        from ossie_studio.sources.collect_core import schema_plugin_run

        return schema_plugin_run(context, self)

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path)

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        collected: list[tuple[str, _Type, bool]] = []
        sources: dict[str, set[str]] = {}
        merged: dict[str, _Type] = {}
        totals: dict[str, bool] = {}
        for path, raw in sorted(files.items()):
            module = _module_of(path)
            try:
                tree = ts_parse("python", raw)
            except Exception as exc:  # pragma: no cover - tree-sitter ne lève pas
                raise ParserError("syntax") from exc
            for info in _extract_file(raw, tree.root_node):
                qualified = f"{module}.{info.qualified}" if module else info.qualified
                info.qualified = qualified
                if qualified not in merged:
                    merged[qualified] = info
                    totals[qualified] = info.total
                    collected.append((qualified, info, info.total))
                sources.setdefault(qualified, set()).add(path)
        known: dict[str, str] = {}
        for qualified, info, _ in collected:
            known.setdefault(info.name, qualified)
            known[qualified] = qualified
        objects: list[ParsedObject] = []
        for qualified, info, total in sorted(collected, key=lambda t: t[0]):
            if info.kind != "enum" and not info.members:
                continue  # classe sans attribut annoté → pas de schéma
            objects.append(
                ParsedObject(
                    key=_slug(qualified),
                    sources=sorted(sources[qualified]),
                    schema=dump_schema(_build_schema(info, total=total, known=known)),
                )
            )
        return objects
