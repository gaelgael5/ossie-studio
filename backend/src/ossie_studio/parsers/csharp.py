"""Parseur `csharp` : lit les fichiers `.cs` et produit un schéma par classe,
record, struct, enum et interface, selon les conventions des schémas générés.

Analyse **syntaxique** (tree-sitter), tolérante aux fichiers incomplets : un type
venu d'un autre fichier ou d'une bibliothèque devient `x-unresolved-type` (la
résolution croisée viendra du service de recherche de schéma commun). Les classes
**partielles** (même espace de noms et même nom, dans plusieurs fichiers) sont
fusionnées en un seul schéma.
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

_GLOBS = ["**/*.cs"]
_SLUG = re.compile(r"[^a-zA-Z0-9]+")

# Correspondance des types C# vers JSON Schema.
_PRIMITIVES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "bool": {"type": "boolean"},
    "byte": {"type": "integer"},
    "sbyte": {"type": "integer"},
    "short": {"type": "integer"},
    "ushort": {"type": "integer"},
    "int": {"type": "integer", "format": "int32"},
    "uint": {"type": "integer"},
    "long": {"type": "integer", "format": "int64"},
    "ulong": {"type": "integer"},
    "float": {"type": "number", "format": "float"},
    "double": {"type": "number", "format": "double"},
    "decimal": {"type": "number"},
    "Guid": {"type": "string", "format": "uuid"},
    "DateTime": {"type": "string", "format": "date-time"},
    "DateTimeOffset": {"type": "string", "format": "date-time"},
    "DateOnly": {"type": "string", "format": "date"},
    "TimeOnly": {"type": "string", "format": "time"},
    "object": {},
}
# Types **valeur** (obligatoires s'ils ne sont pas annotés nullable). `string` et
# `object` sont des types référence.
_VALUE_TYPES = frozenset(_PRIMITIVES) - {"string", "object"}
_LIST_TYPES = frozenset({"List", "IList", "IEnumerable", "ICollection", "IReadOnlyList",
                         "IReadOnlyCollection", "HashSet", "ISet", "Collection"})
_DICT_TYPES = frozenset({"Dictionary", "IDictionary", "IReadOnlyDictionary", "SortedDictionary"})
_KINDS = {
    "class_declaration": "class",
    "record_declaration": "record",
    "struct_declaration": "struct",
    "record_struct_declaration": "record",
    "enum_declaration": "enum",
    "interface_declaration": "interface",
}


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-").lower() or "objet"


# --- Représentation intermédiaire (sans nœud tree-sitter, pour survivre au
#     changement de fichier source entre extraction et construction) ----------


@dataclass
class _TypeRef:
    kind: str  # named | array | dict | empty
    name: str = ""
    nullable: bool = False
    inner: _TypeRef | None = None
    value: _TypeRef | None = None


@dataclass
class _Member:
    name: str
    type_ref: _TypeRef
    doc: str
    attributes: list[dict[str, Any]]
    required_attr: bool
    max_length: int | None


@dataclass
class _Type:
    qualified: str
    name: str
    kind: str
    visibility: str
    visibility_source: str | None
    doc: str
    generics: list[str]
    bases: list[str]
    attributes: list[dict[str, Any]]
    members: list[_Member] = field(default_factory=list)
    enum_members: list[str] = field(default_factory=list)
    namespace: str = ""
    usings: list[str] = field(default_factory=list)

    def merge(self, other: _Type) -> None:
        seen = {m.name for m in self.members}
        self.members += [m for m in other.members if m.name not in seen]
        self.bases += [b for b in other.bases if b not in self.bases]
        self.attributes += other.attributes
        if not self.doc:
            self.doc = other.doc
        self.usings += [u for u in other.usings if u not in self.usings]


# --- Navigation de l'arbre (utilise `src`, les octets du fichier courant) -----


def _text(src: bytes, node: Node | None) -> str:
    return node_text(src, node) if node is not None else ""


def _modifiers(src: bytes, node: Node) -> list[str]:
    return [_text(src, c) for c in node.named_children if c.type == "modifier"]


def _visibility(mods: list[str], *, nested: bool) -> tuple[str, str | None]:
    access = [m for m in mods if m in ("public", "internal", "protected", "private", "file")]
    source = " ".join(access)
    if "public" in mods:
        value = "public"
    elif "protected" in mods:
        value = "protected"
    elif "private" in mods:
        value = "private"
    elif "internal" in mods or "file" in mods:
        value = "internal"
    else:
        value = "private" if nested else "internal"
    return value, (source if source and source != value else None)


def _doc_blob(src: bytes, node: Node) -> str:
    """Le commentaire XML (`///`) qui précède un nœud, lignes recollées."""
    prev = node.prev_sibling
    lines: list[str] = []
    while prev is not None and prev.type == "comment":
        text = _text(src, prev)
        if text.startswith("///"):
            lines.insert(0, text[3:].strip())
        prev = prev.prev_sibling
    return " ".join(lines)


# Balises de référence auto-fermantes : on garde le nom visé (`cref` sans son
# préfixe de genre « T: », « M: »…, `name`, ou le mot-clé de `langword`).
_XML_REF = re.compile(
    r"<(?:see|seealso|paramref|typeparamref)\s+(?:cref|name|langword|href)\s*=\s*\"([^\"]*)\"\s*/>"
)
_XML_LINK = re.compile(r"<see\s+href\s*=\s*\"[^\"]*\"\s*>(.*?)</see>", re.DOTALL)
_XML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_XML_ENTITIES = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&amp;": "&"}


def _xml_text(fragment: str) -> str:
    """Texte lisible d'un fragment de doc XML : les références deviennent le nom
    visé, les balises de mise en forme (`<c>`, `<b>`, `<para>`…) disparaissent
    en gardant leur texte, les entités sont décodées."""
    text = _XML_LINK.sub(r"\1", fragment)
    text = _XML_REF.sub(lambda m: m.group(1).split(":", 1)[-1], text)
    text = _XML_TAG.sub(" ", text)
    for entity, char in _XML_ENTITIES.items():
        text = text.replace(entity, char)
    text = re.sub(r"\s+([.,;:!?)])", r"\1", re.sub(r"\s+", " ", text))
    return text.strip()


def _doc_comment(src: bytes, node: Node) -> str:
    blob = _doc_blob(src, node)
    match = re.search(r"<summary>(.*?)</summary>", blob, re.DOTALL)
    return _xml_text(match.group(1) if match else blob)


def _doc_params(src: bytes, node: Node) -> dict[str, str]:
    """`<param name="X">…</param>` du commentaire d'un type : la doc des
    paramètres d'un record positionnel, qui deviennent ses propriétés."""
    blob = _doc_blob(src, node)
    return {
        m.group(1): _xml_text(m.group(2))
        for m in re.finditer(r'<param\s+name\s*=\s*"([^"]+)"\s*>(.*?)</param>', blob, re.DOTALL)
    }


def _attributes(src: bytes, node: Node) -> tuple[list[dict[str, Any]], bool, int | None]:
    result: list[dict[str, Any]] = []
    required = False
    max_length: int | None = None
    for group in node.named_children:
        if group.type != "attribute_list":
            continue
        for attr in group.named_children:
            if attr.type != "attribute":
                continue
            name = _text(src, attr.child_by_field_name("name"))
            args: list[str] = []
            for arglist in attr.named_children:
                if arglist.type == "attribute_argument_list":
                    args += [_text(src, a) for a in arglist.named_children]
            result.append({"nom": name, "arguments": args})
            short = name.rsplit(".", 1)[-1]
            if short == "Required":
                required = True
            if short in ("MaxLength", "StringLength") and args:
                digits = re.sub(r"[^0-9]", "", args[0])
                if digits:
                    max_length = int(digits)
    return result, required, max_length


def _type_ref(src: bytes, node: Node | None) -> _TypeRef:
    if node is None:
        return _TypeRef("empty")
    if node.type == "nullable_type":
        ref = _type_ref(src, node.child_by_field_name("type"))
        ref.nullable = True
        return ref
    if node.type == "array_type":
        element = node.child_by_field_name("type") or (
            node.named_children[0] if node.named_children else None
        )
        return _TypeRef("array", inner=_type_ref(src, element))
    if node.type == "generic_name":
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        base = _text(src, ident)
        arglist = next((c for c in node.named_children if c.type == "type_argument_list"), None)
        args = [c for c in (arglist.named_children if arglist else []) if c.type != "comment"]
        if base == "Nullable" and args:
            ref = _type_ref(src, args[0])
            ref.nullable = True
            return ref
        if base in _LIST_TYPES and args:
            return _TypeRef("array", inner=_type_ref(src, args[0]))
        if base in _DICT_TYPES and len(args) >= 2:
            return _TypeRef("dict", value=_type_ref(src, args[1]))
        return _TypeRef("named", name=base)
    if node.type == "qualified_name":
        return _TypeRef("named", name=_text(src, node.child_by_field_name("name")))
    return _TypeRef("named", name=_text(src, node))


# --- Extraction des types ----------------------------------------------------


def _extract_file(src: bytes, root: Node) -> list[_Type]:
    out: list[_Type] = []
    _walk(src, root, namespace="", enclosing="", out=out, usings=())
    return out


# `using A.B;` (ni `using static`, ni alias `using X = A.B;`, ni `global::`) :
# les namespaces importés, dont le socle se sert pour départager deux types
# homonymes de la source (résolution des références).
_USING = re.compile(
    r"^\s*(?:global\s+)?using\s+(?!static\b)(?![\w.]+\s*=)(?:global::)?([\w.]+)\s*;"
)


def _level_usings(src: bytes, node: Node, inherited: tuple[str, ...]) -> tuple[str, ...]:
    found = list(inherited)
    for child in node.named_children:
        if child.type == "using_directive":
            match = _USING.match(_text(src, child))
            if match and match.group(1) not in found:
                found.append(match.group(1))
    return tuple(found)


def _walk(
    src: bytes,
    node: Node,
    *,
    namespace: str,
    enclosing: str,
    out: list[_Type],
    usings: tuple[str, ...],
) -> None:
    current = namespace
    usings = _level_usings(src, node, usings)
    for child in node.named_children:
        if child.type == "file_scoped_namespace_declaration":
            # S'applique à tous les frères qui suivent dans le fichier.
            current = _text(src, child.child_by_field_name("name"))
        elif child.type == "namespace_declaration":
            ns = _text(src, child.child_by_field_name("name"))
            body = child.child_by_field_name("body")
            _walk(src, body or child, namespace=ns, enclosing="", out=out, usings=usings)
        elif child.type in _KINDS:
            info = _build_type(src, child, namespace=current, enclosing=enclosing)
            info.usings = list(usings)
            out.append(info)
            body = child.child_by_field_name("body")
            inner = _text(src, child.child_by_field_name("name"))
            if body is not None:
                deeper = f"{enclosing}.{inner}" if enclosing else inner
                _walk(src, body, namespace=current, enclosing=deeper, out=out, usings=usings)
        elif child.type == "declaration_list":
            _walk(src, child, namespace=current, enclosing=enclosing, out=out, usings=usings)


def _build_type(src: bytes, node: Node, *, namespace: str, enclosing: str) -> _Type:
    name = _text(src, node.child_by_field_name("name")) or "?"
    qualified = ".".join(p for p in (namespace, enclosing, name) if p)
    mods = _modifiers(src, node)
    visibility, source = _visibility(mods, nested=bool(enclosing))
    attributes, _, _ = _attributes(src, node)
    generics = [
        _text(src, tp.child_by_field_name("name"))
        for container in node.named_children
        if container.type == "type_parameter_list"
        for tp in container.named_children
        if tp.type == "type_parameter"
    ]
    bases = [
        _simple_base(src, c)
        for container in node.named_children
        if container.type == "base_list"
        for c in container.named_children
        if c.type != "comment"
    ]
    info = _Type(
        qualified=qualified,
        name=name,
        kind=_KINDS[node.type],
        visibility=visibility,
        visibility_source=source,
        doc=_doc_comment(src, node),
        generics=generics,
        bases=bases,
        attributes=attributes,
        namespace=namespace,
    )
    if info.kind == "enum":
        body = node.child_by_field_name("body")
        if body is not None:
            info.enum_members = [
                _text(src, m.child_by_field_name("name"))
                for m in body.named_children
                if m.type == "enum_member_declaration"
            ]
    else:
        info.members = _members(src, node)
    return info


def _simple_base(src: bytes, node: Node) -> str:
    if node.type == "generic_name":
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        return _text(src, ident)
    if node.type == "qualified_name":
        return _text(src, node.child_by_field_name("name"))
    return _text(src, node)


def _members(src: bytes, node: Node) -> list[_Member]:
    members: list[_Member] = []
    # Record positionnel : paramètres du constructeur primaire → propriétés,
    # documentées par les `<param>` du commentaire du record.
    param_docs = _doc_params(src, node)
    for params in node.named_children:
        if params.type != "parameter_list":
            continue
        for param in params.named_children:
            if param.type != "parameter":
                continue
            attrs, req, maxlen = _attributes(src, param)
            param_name = _text(src, param.child_by_field_name("name"))
            members.append(
                _Member(
                    param_name,
                    _type_ref(src, param.child_by_field_name("type")),
                    param_docs.get(param_name, ""),
                    attrs,
                    req,
                    maxlen,
                )
            )
    body = node.child_by_field_name("body")
    if body is None:
        return members
    for decl in body.named_children:
        mods = _modifiers(src, decl)
        if decl.type == "property_declaration" and "public" in mods and "static" not in mods:
            attrs, req, maxlen = _attributes(src, decl)
            members.append(
                _Member(
                    _text(src, decl.child_by_field_name("name")),
                    _type_ref(src, decl.child_by_field_name("type")),
                    _doc_comment(src, decl),
                    attrs,
                    req,
                    maxlen,
                )
            )
        elif decl.type == "field_declaration" and "public" in mods and "static" not in mods:
            var = next((c for c in decl.named_children if c.type == "variable_declaration"), None)
            if var is None:
                continue
            type_ref = _type_ref(src, var.child_by_field_name("type"))
            attrs, req, maxlen = _attributes(src, decl)
            doc = _doc_comment(src, decl)
            for declr in var.named_children:
                if declr.type == "variable_declarator":
                    vname = declr.child_by_field_name("name") or (
                        declr.named_children[0] if declr.named_children else None
                    )
                    members.append(_Member(_text(src, vname), type_ref, doc, attrs, req, maxlen))
    return members


# --- Construction du schéma --------------------------------------------------


def _ref_schema(
    ref: _TypeRef, *, generics: set[str], known: dict[str, str]
) -> tuple[dict[str, Any], bool, bool]:
    """(schéma, nullable, est_type_valeur)."""
    if ref.kind == "empty":
        return {}, ref.nullable, False
    if ref.kind == "array":
        inner, _, _ = _ref_schema(ref.inner or _TypeRef("empty"), generics=generics, known=known)
        return {"type": "array", "items": inner}, ref.nullable, False
    if ref.kind == "dict":
        value, _, _ = _ref_schema(ref.value or _TypeRef("empty"), generics=generics, known=known)
        return {"type": "object", "additionalProperties": value}, ref.nullable, False
    name = ref.name
    if name in generics:
        return {"x-generic-type": name}, ref.nullable, False
    if name in _PRIMITIVES:
        return dict(_PRIMITIVES[name]), ref.nullable, name in _VALUE_TYPES
    if name in known:
        return {"$ref": f"{_slug(known[name])}.json"}, ref.nullable, False
    return {"x-unresolved-type": name}, ref.nullable, False


def _member_schema(
    member: _Member, *, generics: set[str], known: dict[str, str]
) -> tuple[dict[str, Any], bool]:
    schema, nullable, is_value = _ref_schema(member.type_ref, generics=generics, known=known)
    schema = dict(schema)
    schema["x-visibility"] = "public"
    if member.doc:
        schema["description"] = member.doc
    if member.max_length is not None:
        schema["maxLength"] = member.max_length
    if member.attributes:
        schema["x-annotations"] = member.attributes
    if nullable:
        required = False
    elif member.required_attr:
        required = True
    elif is_value:
        required = True
    elif "x-generic-type" in schema:
        required = False
    else:
        required = False
        schema["x-nullability"] = "unknown"
    return schema, required


def _build_schema(info: _Type, known: dict[str, str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$id": f"{_slug(info.qualified)}.json",
        "title": info.name,
        "x-language": "csharp",
        "x-kind": info.kind,
        "x-visibility": info.visibility,
    }
    # Espace de noms d'origine (namespace C#, paquet Java/Kotlin, module TS/Python),
    # affiché tel quel par l'explorateur raw : le `$id`, slugifié, ne le rend pas.
    if info.namespace:
        schema["x-namespace"] = info.namespace
    # Namespaces importés par le fichier : le socle de résolution s'en sert pour
    # départager des homonymes (voir sources/schema_resolve.py).
    if info.usings:
        schema["x-usings"] = list(info.usings)
    if info.visibility_source:
        schema["x-visibility-source"] = info.visibility_source
    if info.doc:
        schema["description"] = info.doc
    if info.generics:
        schema["x-generic-parameters"] = info.generics
    if info.attributes:
        schema["x-annotations"] = info.attributes
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
        prop, is_required = _member_schema(member, generics=generics, known=known)
        properties[member.name] = prop
        if is_required:
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


class CSharpParser:
    """Parseur C# fondé sur tree-sitter."""

    id = "csharp"
    cible = CIBLE_RAW
    label = "C#"
    version = f"1.3-ts{grammar_version('csharp')}"

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
        merged: dict[str, _Type] = {}
        sources: dict[str, set[str]] = {}
        for path, raw in sorted(files.items()):
            try:
                tree = ts_parse("csharp", raw)
            except Exception as exc:  # pragma: no cover - tree-sitter ne lève pas
                raise ParserError("syntax") from exc
            for info in _extract_file(raw, tree.root_node):
                if info.qualified in merged:
                    merged[info.qualified].merge(info)
                else:
                    merged[info.qualified] = info
                sources.setdefault(info.qualified, set()).add(path)
        known = {info.name: info.qualified for info in merged.values()}
        objects: list[ParsedObject] = []
        for qualified, info in sorted(merged.items()):
            objects.append(
                ParsedObject(
                    key=_slug(qualified),
                    sources=sorted(sources[qualified]),
                    schema=dump_schema(_build_schema(info, known)),
                )
            )
        return objects
