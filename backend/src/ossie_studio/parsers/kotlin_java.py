"""Parseur `kotlin-java` : lit les fichiers `.java` et `.kt` et produit un schéma
par type. Un seul identifiant de parseur (les deux langages cohabitent souvent
dans un dépôt et partagent les mêmes annotations), deux grammaires derrière.

Analyse **syntaxique** (tree-sitter), jamais d'exécution du code lu. Un type venu
d'un autre fichier ou d'une bibliothèque devient `x-unresolved-type` ; la
résolution croisée viendra du service de recherche de schéma commun. Les
références au sein du même groupe de fichiers sont résolues en `$ref`.
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

_GLOBS = ["**/*.java", "**/*.kt"]
_SLUG = re.compile(r"[^a-zA-Z0-9]+")

# Correspondance commune des types Java/Kotlin vers JSON Schema (par nom simple).
_TYPES: dict[str, dict[str, Any]] = {
    "String": {"type": "string"},
    "CharSequence": {"type": "string"},
    "char": {"type": "string"},
    "Character": {"type": "string"},
    "Char": {"type": "string"},
    "int": {"type": "integer", "format": "int32"},
    "Integer": {"type": "integer", "format": "int32"},
    "Int": {"type": "integer", "format": "int32"},
    "short": {"type": "integer"},
    "Short": {"type": "integer"},
    "byte": {"type": "integer"},
    "Byte": {"type": "integer"},
    "long": {"type": "integer", "format": "int64"},
    "Long": {"type": "integer", "format": "int64"},
    "float": {"type": "number", "format": "float"},
    "Float": {"type": "number", "format": "float"},
    "double": {"type": "number", "format": "double"},
    "Double": {"type": "number", "format": "double"},
    "boolean": {"type": "boolean"},
    "Boolean": {"type": "boolean"},
    "BigDecimal": {"type": "number"},
    "BigInteger": {"type": "integer"},
    "UUID": {"type": "string", "format": "uuid"},
    "LocalDate": {"type": "string", "format": "date"},
    "LocalDateTime": {"type": "string", "format": "date-time"},
    "Instant": {"type": "string", "format": "date-time"},
    "OffsetDateTime": {"type": "string", "format": "date-time"},
    "ZonedDateTime": {"type": "string", "format": "date-time"},
    "LocalTime": {"type": "string", "format": "time"},
    "Object": {},
    "Any": {},
    "Unit": {"type": "null"},
    "void": {"type": "null"},
    "Void": {"type": "null"},
}
_LIST_TYPES = frozenset(
    {"List", "Set", "Collection", "Iterable", "MutableList", "MutableSet",
     "MutableCollection", "ArrayList", "HashSet", "LinkedList", "Array"}
)
_DICT_TYPES = frozenset(
    {"Map", "MutableMap", "HashMap", "LinkedHashMap", "SortedMap", "TreeMap"}
)
# Annotations qui rendent un champ Java obligatoire.
_REQUIRED_ANNOTATIONS = frozenset({"NotNull", "NonNull", "Id"})


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


def _module_of(path: str) -> str:
    """Repli quand le fichier n'a pas de `package` : le **répertoire** fait office
    d'espace de noms (en JVM, l'arborescence reflète le paquet)."""
    rel = path.replace("\\", "/").lstrip("./")
    parts = [p for p in rel.split("/") if p]
    return ".".join(parts[:-1])  # on laisse le nom de fichier de côté


def _int(text: str) -> int | None:
    digits = re.sub(r"[^0-9-]", "", text)
    try:
        return int(digits)
    except ValueError:
        return None


# --- Représentation intermédiaire --------------------------------------------


@dataclass
class _TypeRef:
    kind: str  # named | array | dict | empty
    name: str = ""
    inner: _TypeRef | None = None
    value: _TypeRef | None = None


@dataclass
class _Member:
    name: str
    type_ref: _TypeRef
    required: bool
    visibility: str
    doc: str
    annotations: list[dict[str, Any]]
    constraints: dict[str, Any]


@dataclass
class _Type:
    qualified: str
    name: str
    kind: str
    language: str
    visibility: str
    doc: str
    generics: list[str]
    bases: list[str]
    annotations: list[dict[str, Any]]
    members: list[_Member] = field(default_factory=list)
    enum_members: list[str] = field(default_factory=list)
    namespace: str = ""


def _text(src: bytes, node: Node | None) -> str:
    return node_text(src, node) if node is not None else ""


def _doc_comment(src: bytes, node: Node) -> str:
    prev = node.prev_sibling
    if prev is not None and prev.type in ("block_comment", "comment", "multiline_comment"):
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


def _child_tokens(node: Node) -> set[str]:
    return {c.type for c in node.children}


# --- Annotations (Java et Kotlin) --------------------------------------------


def _java_annotations(
    src: bytes, modifiers: Node | None
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    annos: list[dict[str, Any]] = []
    required = False
    constraints: dict[str, Any] = {}
    if modifiers is None:
        return annos, required, constraints
    for child in modifiers.named_children:
        if child.type == "marker_annotation":
            name = _text(src, child.child_by_field_name("name")).rsplit(".", 1)[-1]
            annos.append({"nom": name, "arguments": []})
            if name in _REQUIRED_ANNOTATIONS:
                required = True
        elif child.type == "annotation":
            name = _text(src, child.child_by_field_name("name")).rsplit(".", 1)[-1]
            named, positional = _java_anno_args(src, child.child_by_field_name("arguments"))
            annos.append({"nom": name, "arguments": _args_text(named, positional)})
            if name in _REQUIRED_ANNOTATIONS:
                required = True
            _apply_constraints(name, named, positional, constraints)
            if name == "Column" and named.get("nullable") == "false":
                required = True
    return annos, required, constraints


def _java_anno_args(
    src: bytes, arglist: Node | None
) -> tuple[dict[str, str], list[str]]:
    named: dict[str, str] = {}
    positional: list[str] = []
    if arglist is None:
        return named, positional
    for arg in arglist.named_children:
        if arg.type == "element_value_pair":
            key = _text(src, arg.child_by_field_name("key"))
            value = _clean(_text(src, arg.child_by_field_name("value")))
            named[key] = value
        else:
            positional.append(_clean(_text(src, arg)))
    return named, positional


def _kotlin_annotations(
    src: bytes, modifiers: Node | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    annos: list[dict[str, Any]] = []
    constraints: dict[str, Any] = {}
    if modifiers is None:
        return annos, constraints
    for child in modifiers.named_children:
        if child.type != "annotation":
            continue
        inner = child.named_children[0] if child.named_children else None
        if inner is None:
            continue
        if inner.type == "constructor_invocation":
            name = _text(src, inner.child_by_field_name("name"))
            if not name:
                ut = next((c for c in inner.named_children if c.type == "user_type"), None)
                name = _text(src, ut)
            name = name.rsplit(".", 1)[-1]
            value_args = next(
                (c for c in inner.named_children if c.type == "value_arguments"), None
            )
            named, positional = _kotlin_anno_args(src, value_args)
            annos.append({"nom": name, "arguments": _args_text(named, positional)})
            _apply_constraints(name, named, positional, constraints)
        else:  # user_type : annotation marqueur (@Id, @NotNull…)
            name = _text(src, inner).rsplit(".", 1)[-1]
            annos.append({"nom": name, "arguments": []})
    return annos, constraints


def _kotlin_anno_args(
    src: bytes, value_args: Node | None
) -> tuple[dict[str, str], list[str]]:
    named: dict[str, str] = {}
    positional: list[str] = []
    if value_args is None:
        return named, positional
    for arg in value_args.named_children:
        if arg.type != "value_argument":
            continue
        if "=" in _child_tokens(arg):
            kids = arg.named_children
            if len(kids) >= 2:
                named[_text(src, kids[0])] = _clean(_text(src, kids[1]))
        elif arg.named_children:
            positional.append(_clean(_text(src, arg.named_children[0])))
    return named, positional


def _clean(text: str) -> str:
    return text.strip().strip('"').strip("'")


def _args_text(named: dict[str, str], positional: list[str]) -> list[str]:
    return [*positional, *(f"{k}={v}" for k, v in named.items())]


def _apply_constraints(
    name: str, named: dict[str, str], positional: list[str], constraints: dict[str, Any]
) -> None:
    if name == "Size":
        if "max" in named and (n := _int(named["max"])) is not None:
            constraints["maxLength"] = n
        if "min" in named and (n := _int(named["min"])) is not None:
            constraints["minLength"] = n
    elif name == "Pattern":
        if "regexp" in named:
            constraints["pattern"] = named["regexp"]


# --- Types Java --------------------------------------------------------------


def _java_type_ref(src: bytes, node: Node | None) -> tuple[_TypeRef, bool]:
    """(_TypeRef, est_primitive). Un primitif Java (int, double, boolean…) est un
    type valeur, donc obligatoire."""
    if node is None:
        return _TypeRef("empty"), False
    kind = node.type
    if kind in ("integral_type", "floating_point_type", "boolean_type"):
        return _TypeRef("named", name=_text(src, node)), True
    if kind == "array_type":
        element = node.child_by_field_name("element") or (
            node.named_children[0] if node.named_children else None
        )
        inner, _ = _java_type_ref(src, element)
        return _TypeRef("array", inner=inner), False
    if kind == "generic_type":
        base = next((c for c in node.named_children if c.type != "type_arguments"), None)
        name = _base_name(src, base) if base is not None else ""
        args_node = next((c for c in node.named_children if c.type == "type_arguments"), None)
        args = [c for c in (args_node.named_children if args_node else []) if c.type != ","]
        return _java_generic(src, name, args), False
    if kind in ("type_identifier", "scoped_type_identifier"):
        return _TypeRef("named", name=_text(src, node).rsplit(".", 1)[-1]), False
    return _TypeRef("named", name=_text(src, node)), False


def _java_generic(src: bytes, name: str, args: list[Node]) -> _TypeRef:
    if name in _LIST_TYPES and args:
        inner, _ = _java_type_ref(src, args[0])
        return _TypeRef("array", inner=inner)
    if name in _DICT_TYPES and len(args) >= 2:
        value, _ = _java_type_ref(src, args[1])
        return _TypeRef("dict", value=value)
    return _TypeRef("named", name=name)


# --- Types Kotlin ------------------------------------------------------------


def _kotlin_type_ref(src: bytes, node: Node | None) -> tuple[_TypeRef, bool]:
    """(_TypeRef, nullable)."""
    if node is None:
        return _TypeRef("empty"), False
    if node.type == "nullable_type":
        wrapped = next(
            (c for c in node.named_children if c.type in ("user_type", "function_type")), None
        )
        ref, _ = _kotlin_type_ref(src, wrapped)
        return ref, True
    if node.type == "user_type":
        ident = next((c for c in node.named_children if c.type == "type_identifier"), None)
        if ident is None:
            ident = next((c for c in node.named_children if c.type == "identifier"), None)
        name = _text(src, ident)
        args_node = next((c for c in node.named_children if c.type == "type_arguments"), None)
        projections = [
            p for p in (args_node.named_children if args_node else [])
            if p.type == "type_projection"
        ]
        args = [p.named_children[0] for p in projections if p.named_children]
        if name in _LIST_TYPES and args:
            item_ref, _ = _kotlin_type_ref(src, args[0])
            return _TypeRef("array", inner=item_ref), False
        if name in _DICT_TYPES and len(args) >= 2:
            value, _ = _kotlin_type_ref(src, args[1])
            return _TypeRef("dict", value=value), False
        return _TypeRef("named", name=name), False
    return _TypeRef("named", name=_text(src, node)), False


# --- Extraction Java ---------------------------------------------------------

_JAVA_KINDS = {
    "class_declaration": "class",
    "record_declaration": "record",
    "enum_declaration": "enum",
    "interface_declaration": "interface",
}


def _java_extract(src: bytes, root: Node, *, prefix: str) -> list[_Type]:
    package = _java_package(src, root)
    base = package or prefix
    out: list[_Type] = []
    _java_walk(src, root, enclosing=base, out=out)
    for info in out:
        info.namespace = base
    return out


def _java_package(src: bytes, root: Node) -> str:
    for child in root.named_children:
        if child.type == "package_declaration":
            return _text(src, child.named_children[0]) if child.named_children else ""
    return ""


def _java_walk(src: bytes, node: Node, *, enclosing: str, out: list[_Type]) -> None:
    for child in node.named_children:
        if child.type in _JAVA_KINDS:
            info = _java_build_type(src, child, enclosing=enclosing)
            out.append(info)
            body = child.child_by_field_name("body")
            if body is not None:
                deeper = f"{enclosing}.{info.name}" if enclosing else info.name
                _java_walk(src, body, enclosing=deeper, out=out)
        elif child.type in ("ERROR", "program"):
            _java_walk(src, child, enclosing=enclosing, out=out)


def _modifier_visibility(tokens: set[str]) -> str:
    if "public" in tokens:
        return "public"
    if "protected" in tokens:
        return "protected"
    if "private" in tokens:
        return "private"
    return "internal"  # package-private Java → interne


def _java_build_type(src: bytes, node: Node, *, enclosing: str) -> _Type:
    name = _text(src, node.child_by_field_name("name")) or "?"
    qualified = f"{enclosing}.{name}" if enclosing else name
    modifiers = next((c for c in node.named_children if c.type == "modifiers"), None)
    tokens = _child_tokens(modifiers) if modifiers is not None else set()
    annos, _, _ = _java_annotations(src, modifiers)
    generics = _java_generics(src, node)
    bases = _java_bases(src, node)
    info = _Type(
        qualified=qualified,
        name=name,
        kind=_JAVA_KINDS[node.type],
        language="java",
        visibility=_modifier_visibility(tokens),
        doc=_doc_comment(src, node),
        generics=generics,
        bases=bases,
        annotations=annos,
    )
    if info.kind == "enum":
        body = node.child_by_field_name("body")
        if body is not None:
            info.enum_members = [
                _text(src, c.child_by_field_name("name"))
                for c in body.named_children
                if c.type == "enum_constant"
            ]
        return info
    if info.kind == "record":
        info.members += _java_record_components(src, node)
    body = node.child_by_field_name("body")
    if body is not None:
        info.members += _java_fields(src, body)
    return info


def _java_generics(src: bytes, node: Node) -> list[str]:
    tp = next((c for c in node.named_children if c.type == "type_parameters"), None)
    if tp is None:
        return []
    out: list[str] = []
    for p in tp.named_children:
        if p.type != "type_parameter":
            continue
        name_node = p.child_by_field_name("name") or (
            p.named_children[0] if p.named_children else None
        )
        out.append(_text(src, name_node))
    return out


def _java_bases(src: bytes, node: Node) -> list[str]:
    bases: list[str] = []
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        for c in superclass.named_children:
            if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                bases.append(_base_name(src, c))
    interfaces = node.child_by_field_name("interfaces")
    if interfaces is not None:
        for tl in interfaces.named_children:
            target = tl if tl.type == "type_list" else None
            for c in (target.named_children if target else [interfaces]):
                if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                    bases.append(_base_name(src, c))
    return bases


def _base_name(src: bytes, node: Node) -> str:
    if node.type == "generic_type":
        base = next((c for c in node.named_children if c.type == "type_identifier"), None)
        return _text(src, base).rsplit(".", 1)[-1]
    return _text(src, node).rsplit(".", 1)[-1]


def _java_record_components(src: bytes, node: Node) -> list[_Member]:
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    members: list[_Member] = []
    for param in params.named_children:
        if param.type != "formal_parameter":
            continue
        modifiers = next((c for c in param.named_children if c.type == "modifiers"), None)
        annos, forced, constraints = _java_annotations(src, modifiers)
        type_ref, is_value = _java_type_ref(src, param.child_by_field_name("type"))
        members.append(
            _Member(
                name=_text(src, param.child_by_field_name("name")),
                type_ref=type_ref,
                required=is_value or forced,
                visibility="public",
                doc="",
                annotations=annos,
                constraints=constraints,
            )
        )
    return members


def _java_fields(src: bytes, body: Node) -> list[_Member]:
    members: list[_Member] = []
    for decl in body.named_children:
        if decl.type != "field_declaration":
            continue
        modifiers = next((c for c in decl.named_children if c.type == "modifiers"), None)
        tokens = _child_tokens(modifiers) if modifiers is not None else set()
        if "static" in tokens or "transient" in tokens:
            continue
        annos, forced, constraints = _java_annotations(src, modifiers)
        type_ref, is_value = _java_type_ref(src, decl.child_by_field_name("type"))
        visibility = _modifier_visibility(tokens)
        doc = _doc_comment(src, decl)
        for declr in decl.named_children:
            if declr.type != "variable_declarator":
                continue
            members.append(
                _Member(
                    name=_text(src, declr.child_by_field_name("name")),
                    type_ref=type_ref,
                    required=is_value or forced,
                    visibility=visibility,
                    doc=doc,
                    annotations=annos,
                    constraints=constraints,
                )
            )
    return members


# --- Extraction Kotlin -------------------------------------------------------


def _kotlin_extract(src: bytes, root: Node, *, prefix: str) -> list[_Type]:
    package = _kotlin_package(src, root)
    base = package or prefix
    out: list[_Type] = []
    _kotlin_walk(src, root, enclosing=base, out=out)
    for info in out:
        info.namespace = base
    return out


def _kotlin_package(src: bytes, root: Node) -> str:
    for child in root.named_children:
        if child.type == "package_header":
            ident = next(
                (c for c in child.named_children
                 if c.type in ("qualified_identifier", "identifier")),
                None,
            )
            return _text(src, ident)
    return ""


def _kotlin_walk(src: bytes, node: Node, *, enclosing: str, out: list[_Type]) -> None:
    for child in node.named_children:
        if child.type in ("class_declaration", "object_declaration"):
            info = _kotlin_build_type(src, child, enclosing=enclosing)
            out.append(info)
            body = next(
                (c for c in child.named_children if c.type in ("class_body", "enum_class_body")),
                None,
            )
            if body is not None:
                deeper = f"{enclosing}.{info.name}" if enclosing else info.name
                _kotlin_walk(src, body, enclosing=deeper, out=out)
        elif child.type == "ERROR":
            _kotlin_walk(src, child, enclosing=enclosing, out=out)


def _kotlin_build_type(src: bytes, node: Node, *, enclosing: str) -> _Type:
    name = _text(src, node.child_by_field_name("name")) or _text(
        src, next((c for c in node.named_children if c.type == "identifier"), None)
    ) or "?"
    qualified = f"{enclosing}.{name}" if enclosing else name
    modifiers = next((c for c in node.named_children if c.type == "modifiers"), None)
    class_mods = _kotlin_class_modifiers(src, modifiers)
    annos, _ = _kotlin_annotations(src, modifiers)
    kind = _kotlin_kind(node, class_mods)
    info = _Type(
        qualified=qualified,
        name=name,
        kind=kind,
        language="kotlin",
        visibility=_kotlin_visibility(src, modifiers),
        doc=_doc_comment(src, node),
        generics=_kotlin_generics(src, node),
        bases=_kotlin_bases(src, node),
        annotations=annos,
    )
    if kind == "enum":
        body = next((c for c in node.named_children if c.type == "enum_class_body"), None)
        if body is not None:
            info.enum_members = [
                _text(src, next((i for i in e.named_children if i.type == "identifier"), None))
                for e in body.named_children
                if e.type == "enum_entry"
            ]
        return info
    info.members += _kotlin_primary_props(src, node)
    body = next((c for c in node.named_children if c.type == "class_body"), None)
    if body is not None:
        info.members += _kotlin_body_props(src, body)
    return info


def _kotlin_class_modifiers(src: bytes, modifiers: Node | None) -> set[str]:
    if modifiers is None:
        return set()
    return {
        _text(src, c) for c in modifiers.named_children if c.type == "class_modifier"
    }


def _kotlin_kind(node: Node, class_mods: set[str]) -> str:
    tokens = _child_tokens(node)
    if "interface" in tokens:
        return "interface"
    if node.type == "object_declaration":
        return "object"
    if "enum" in class_mods:
        return "enum"
    if "data" in class_mods:
        return "data"
    if "sealed" in class_mods:
        return "sealed"
    return "class"


def _kotlin_visibility(src: bytes, modifiers: Node | None) -> str:
    if modifiers is None:
        return "public"
    for c in modifiers.named_children:
        if c.type == "visibility_modifier":
            vis = _text(src, c)
            return vis if vis in ("public", "private", "protected", "internal") else "public"
    return "public"


def _kotlin_generics(src: bytes, node: Node) -> list[str]:
    tp = next((c for c in node.named_children if c.type == "type_parameters"), None)
    if tp is None:
        return []
    out: list[str] = []
    for p in tp.named_children:
        if p.type == "type_parameter":
            ident = next((c for c in p.named_children if c.type == "identifier"), None)
            if ident is not None:
                out.append(_text(src, ident))
    return out


def _kotlin_bases(src: bytes, node: Node) -> list[str]:
    specs = next((c for c in node.named_children if c.type == "delegation_specifiers"), None)
    if specs is None:
        return []
    bases: list[str] = []
    for spec in specs.named_children:
        if spec.type != "delegation_specifier":
            continue
        inner = spec.named_children[0] if spec.named_children else None
        if inner is None:
            continue
        if inner.type == "constructor_invocation":
            inner = next((c for c in inner.named_children if c.type == "user_type"), inner)
        if inner.type == "user_type":
            ident = next((c for c in inner.named_children if c.type == "type_identifier"), None)
            if ident is None:
                ident = next((c for c in inner.named_children if c.type == "identifier"), None)
            bases.append(_text(src, ident))
    return bases


def _kotlin_primary_props(src: bytes, node: Node) -> list[_Member]:
    ctor = next((c for c in node.named_children if c.type == "primary_constructor"), None)
    if ctor is None:
        return []
    params = next((c for c in ctor.named_children if c.type == "class_parameters"), None)
    if params is None:
        return []
    members: list[_Member] = []
    for param in params.named_children:
        if param.type != "class_parameter":
            continue
        if not ({"val", "var"} & _child_tokens(param)):
            continue  # paramètre sans val/var : pas une propriété
        members.append(_kotlin_member(src, param, name_from="identifier"))
    return members


def _kotlin_body_props(src: bytes, body: Node) -> list[_Member]:
    members: list[_Member] = []
    for decl in body.named_children:
        if decl.type != "property_declaration":
            continue
        var = next((c for c in decl.named_children if c.type == "variable_declaration"), None)
        if var is None:
            continue
        members.append(_kotlin_member(src, decl, var=var))
    return members


def _kotlin_member(
    src: bytes, node: Node, *, name_from: str = "", var: Node | None = None
) -> _Member:
    modifiers = next((c for c in node.named_children if c.type == "modifiers"), None)
    annos, constraints = _kotlin_annotations(src, modifiers)
    if var is not None:
        ident = next((c for c in var.named_children if c.type == "identifier"), None)
        type_node = next(
            (c for c in var.named_children if c.type in ("user_type", "nullable_type")), None
        )
    else:
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        type_node = next(
            (c for c in node.named_children if c.type in ("user_type", "nullable_type")), None
        )
    type_ref, nullable = _kotlin_type_ref(src, type_node)
    return _Member(
        name=_text(src, ident),
        type_ref=type_ref,
        required=not nullable,  # Kotlin : la nullabilité prime pour required
        visibility=_kotlin_visibility(src, modifiers),
        doc=_doc_comment(src, node),
        annotations=annos,
        constraints=constraints,
    )


# --- Construction du schéma --------------------------------------------------


def _ref_schema(ref: _TypeRef, *, generics: set[str], known: dict[str, str]) -> dict[str, Any]:
    if ref.kind == "empty":
        return {}
    if ref.kind == "array":
        inner = _ref_schema(ref.inner or _TypeRef("empty"), generics=generics, known=known)
        return {"type": "array", "items": inner}
    if ref.kind == "dict":
        value = _ref_schema(ref.value or _TypeRef("empty"), generics=generics, known=known)
        return {"type": "object", "additionalProperties": value}
    name = ref.name
    if name in generics:
        return {"x-generic-type": name}
    if name in _TYPES:
        return dict(_TYPES[name])
    if name in known:
        return {"$ref": f"{_slug(known[name])}.json"}
    return {"x-unresolved-type": name}


def _member_schema(member: _Member, *, generics: set[str], known: dict[str, str]) -> dict[str, Any]:
    schema = dict(_ref_schema(member.type_ref, generics=generics, known=known))
    schema["x-visibility"] = member.visibility
    if member.doc:
        schema["description"] = member.doc
    schema.update(member.constraints)
    if member.annotations:
        schema["x-annotations"] = member.annotations
    return schema


def _build_schema(info: _Type, known: dict[str, str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$id": f"{_slug(info.qualified)}.json",
        "title": info.name,
        "x-language": info.language,
        "x-kind": info.kind,
        "x-visibility": info.visibility,
    }
    # Espace de noms d'origine (namespace C#, paquet Java/Kotlin, module TS/Python),
    # affiché tel quel par l'explorateur raw : le `$id`, slugifié, ne le rend pas.
    if info.namespace:
        schema["x-namespace"] = info.namespace
    if info.doc:
        schema["description"] = info.doc
    if info.generics:
        schema["x-generic-parameters"] = info.generics
    if info.annotations:
        schema["x-annotations"] = info.annotations
    if info.kind == "enum":
        schema["enum"] = info.enum_members
        return schema

    schema["type"] = "object"
    generics = set(info.generics)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for member in info.members:
        if not member.name:
            continue
        properties[member.name] = _member_schema(member, generics=generics, known=known)
        if member.required:
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


def _grammar_for(path: str) -> str:
    return "kotlin" if path.endswith((".kt", ".kts")) else "java"


class KotlinJavaParser:
    """Parseur Kotlin/Java fondé sur tree-sitter. N'exécute jamais le code lu."""

    id = "kotlin-java"
    cible = CIBLE_RAW
    label = "Kotlin / Java"
    version = f"1.1-java{grammar_version('java')}-kt{grammar_version('kotlin')}"

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
        types: list[_Type] = []
        sources: dict[str, set[str]] = {}
        seen: set[str] = set()
        for path, raw in sorted(files.items()):
            module = _module_of(path)
            language = _grammar_for(path)
            try:
                tree = ts_parse(language, raw)
            except Exception as exc:  # pragma: no cover - tree-sitter ne lève pas
                raise ParserError("syntax") from exc
            extract = _kotlin_extract if language == "kotlin" else _java_extract
            for info in extract(raw, tree.root_node, prefix=module):
                if info.qualified not in seen:
                    seen.add(info.qualified)
                    types.append(info)
                sources.setdefault(info.qualified, set()).add(path)
        known: dict[str, str] = {}
        for info in types:
            known.setdefault(info.name, info.qualified)
            known[info.qualified] = info.qualified
        objects: list[ParsedObject] = []
        for info in sorted(types, key=lambda t: t.qualified):
            objects.append(
                ParsedObject(
                    key=_slug(info.qualified),
                    sources=sorted(sources[info.qualified]),
                    schema=dump_schema(_build_schema(info, known)),
                )
            )
        return objects
