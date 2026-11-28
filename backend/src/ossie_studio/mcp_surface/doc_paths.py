"""Les chemins d'opération de `patch_document` (ticket 10cc4ed6) : JSONPath pour un
document YAML/JSON, XPath pour un processus BPMN (Gael : « le JSONPath ne s'applique
pas, mais dans ce cas précis il faut mettre en xmlpath »).

Règle commune : un chemin d'opération désigne **exactement un nœud** (sauf `add`, qui
désigne la place d'un nœud nouveau). Zéro ou plusieurs nœuds, c'est une erreur
explicite. Opérations :

- `set` : remplace la valeur d'un nœud qui existe ;
- `add` : ajoute un membre absent d'un objet, insère dans un tableau (indice, ou
  l'indice qui suit le dernier pour ajouter à la fin) ; en XPath, ajoute un élément
  enfant (fragment XML) ou un attribut absent (`…/@nom`) ;
- `remove` : retire un nœud qui existe.

JSONPath : la forme « singulière » de la RFC 9535 (noms et indices : `$.a.b[0]['c d']`),
la seule qui désigne un nœud et un seul. XPath 1.0 (lxml), préfixes `bpmn:`, `bpmndi:`,
`dc:`, `di:`, `urban:` et ceux déclarés par le document ; le fichier est relu et réécrit
par lxml, qui garde préfixes, commentaires et extensions.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Literal

from lxml import etree

Op = Literal["set", "add", "remove"]

_SEGMENT = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(-?\d+)\]|\['([^']+)'\]|\[\"([^\"]+)\"\]")


class PathError(Exception):
    """Une opération impossible : le code et la raison, en français."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.index = -1  # le rang de l'opération en cause


@dataclass(frozen=True)
class Operation:
    op: Op
    path: str
    value: Any = None


# --- JSONPath -----------------------------------------------------------------------------


def parse_jsonpath(path: str) -> list[str | int]:
    """`$.a.b[0]['c d']` → ['a', 'b', 0, 'c d'] ; refus `write_jsonpath_invalid` si la
    forme n'est pas une requête singulière."""
    text = path.strip()
    if not text.startswith("$"):
        raise PathError("write_jsonpath_invalid", "Un JSONPath commence par « $ ».")
    pos = 1
    segments: list[str | int] = []
    while pos < len(text):
        m = _SEGMENT.match(text, pos)
        if m is None:
            raise PathError(
                "write_jsonpath_invalid",
                f"JSONPath non lu à partir de « {text[pos:]} » : attendu « .nom », « [indice] » "
                "ou « ['nom'] » (un seul nœud : ni « * », ni « .. », ni filtre).",
            )
        name, index, quoted, double = m.groups()
        segments.append(int(index) if index is not None else (name or quoted or double))
        pos = m.end()
    if not segments:
        raise PathError(
            "write_jsonpath_invalid", "Le document entier ne se modifie pas : « $ » seul."
        )
    return segments


def _step(node: Any, seg: str | int, where: str) -> Any:
    if isinstance(seg, int):
        if not isinstance(node, list):
            raise PathError("write_path_not_single", f"{where} n'est pas un tableau.")
        index = seg if seg >= 0 else len(node) + seg
        if not 0 <= index < len(node):
            raise PathError("write_path_not_single", f"{where}[{seg}] n'existe pas (aucun nœud).")
        return node[index]
    if not isinstance(node, dict):
        raise PathError("write_path_not_single", f"{where} n'est pas un objet.")
    if seg not in node:
        raise PathError("write_path_not_single", f"{where}.{seg} n'existe pas (aucun nœud).")
    return node[seg]


def apply_json(document: Any, operations: list[Operation]) -> Any:
    """Applique les opérations sur une copie du document ; la première impossible
    lève PathError (avec son rang dans `index`)."""
    doc = copy.deepcopy(document)
    for i, operation in enumerate(operations):
        try:
            _apply_one(doc, parse_jsonpath(operation.path), operation)
        except PathError as exc:
            exc.index = i
            raise
    return doc


def _apply_one(doc: Any, segments: list[str | int], operation: Operation) -> None:
    node = doc
    where = "$"
    for seg in segments[:-1]:
        node = _step(node, seg, where)
        where += f"[{seg}]" if isinstance(seg, int) else f".{seg}"
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(node, list):
            raise PathError("write_path_not_single", f"{where} n'est pas un tableau.")
        index = last if last >= 0 else len(node) + last
        if operation.op == "add":
            if not 0 <= index <= len(node):
                raise PathError(
                    "write_path_not_single",
                    f"{where}[{last}] : un ajout se fait à un indice existant ou à la fin "
                    f"({len(node)}).",
                )
            node.insert(index, operation.value)
            return
        if not 0 <= index < len(node):
            raise PathError("write_path_not_single", f"{where}[{last}] n'existe pas (aucun nœud).")
        if operation.op == "remove":
            node.pop(index)
        else:
            node[index] = operation.value
        return
    if not isinstance(node, dict):
        raise PathError("write_path_not_single", f"{where} n'est pas un objet.")
    exists = last in node
    if operation.op == "add":
        if exists:
            raise PathError(
                "write_path_not_single",
                f"{where}.{last} existe déjà : « set » pour le remplacer.",
            )
        node[last] = operation.value
    elif not exists:
        raise PathError(
            "write_path_not_single",
            f"{where}.{last} n'existe pas (aucun nœud)"
            + (" : « add » pour l'ajouter." if operation.op == "set" else "."),
        )
    elif operation.op == "remove":
        del node[last]
    else:
        node[last] = operation.value


# --- XPath (BPMN) -------------------------------------------------------------------------

NAMESPACES = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "urban": "https://github.com/gaelgael5/urban-bpmn/schema/1.0",
}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
_ATTRIBUTE = re.compile(r"^(.*)/@([A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)$")


def _namespaces(root: Any) -> dict[str, str]:
    found = {k: v for k, v in (root.nsmap or {}).items() if k}
    return {**found, **NAMESPACES}


def _select(root: Any, path: str, namespaces: dict[str, str]) -> list[Any]:
    try:
        result = root.xpath(path, namespaces=namespaces)
    except (etree.XPathEvalError, etree.XPathSyntaxError) as exc:
        raise PathError("write_xpath_invalid", f"XPath non lu : {exc}.") from None
    if not isinstance(result, list):
        raise PathError(
            "write_xpath_invalid",
            "Le XPath doit désigner un élément, un attribut ou un texte, pas une valeur calculée.",
        )
    return result


def _single(nodes: list[Any], path: str) -> Any:
    if len(nodes) != 1:
        raise PathError(
            "write_path_not_single",
            f"Le XPath « {path} » désigne {len(nodes)} nœud(s) ; il en faut exactement un.",
        )
    return nodes[0]


def _fragment(value: Any, namespaces: dict[str, str]) -> Any:
    """Un fragment XML, avec les préfixes du document."""
    if not isinstance(value, str) or not value.strip():
        raise PathError("write_xpath_value_invalid", "La valeur d'un élément est un fragment XML.")
    declared = " ".join(f'xmlns:{k}="{v}"' for k, v in namespaces.items())
    try:
        holder = etree.fromstring(f"<holder {declared}>{value}</holder>".encode(), _PARSER)
    except etree.XMLSyntaxError as exc:
        raise PathError("write_xpath_value_invalid", f"Fragment XML mal formé : {exc}.") from None
    children = list(holder)
    if len(children) != 1:
        raise PathError(
            "write_xpath_value_invalid", "La valeur d'un élément est un seul élément XML."
        )
    return children[0]


def apply_xml(text: str, operations: list[Operation]) -> str:
    """Applique les opérations sur le XML ; rend le XML réécrit par lxml."""
    try:
        root = etree.fromstring(text.encode("utf-8"), _PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise PathError("carto_process_unreadable", f"XML mal formé : {exc}.") from None
    namespaces = _namespaces(root)
    for i, operation in enumerate(operations):
        try:
            _apply_xml_one(root, operation, namespaces)
        except PathError as exc:
            exc.index = i
            raise
    out: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    return out.decode("utf-8") + ("\n" if not out.endswith(b"\n") else "")


def _qualified(name: str, namespaces: dict[str, str]) -> str:
    if ":" in name:
        prefix, local = name.split(":", 1)
        if prefix not in namespaces:
            raise PathError("write_xpath_invalid", f"Préfixe inconnu « {prefix} ».")
        return f"{{{namespaces[prefix]}}}{local}"
    return name


def _apply_xml_one(root: Any, operation: Operation, namespaces: dict[str, str]) -> None:
    attribute = _ATTRIBUTE.match(operation.path.strip())
    if operation.op == "add" and attribute:
        owner = _single(_select(root, attribute.group(1), namespaces), attribute.group(1))
        name = _qualified(attribute.group(2), namespaces)
        if not isinstance(owner, etree._Element):
            raise PathError("write_path_not_single", "Un attribut s'ajoute à un élément.")
        if name in owner.attrib:
            raise PathError("write_path_not_single", "Cet attribut existe déjà : « set ».")
        owner.set(name, str(operation.value))
        return
    node = _single(_select(root, operation.path, namespaces), operation.path)
    if isinstance(node, etree._Element):
        if operation.op == "remove":
            parent = node.getparent()
            if parent is None:
                raise PathError("write_path_not_single", "L'élément racine ne se retire pas.")
            parent.remove(node)
        elif operation.op == "add":
            node.append(_fragment(operation.value, namespaces))
        else:
            parent = node.getparent()
            if parent is None:
                raise PathError("write_path_not_single", "L'élément racine ne se remplace pas.")
            parent.replace(node, _fragment(operation.value, namespaces))
        return
    owner = node.getparent() if hasattr(node, "getparent") else None
    if owner is None:
        raise PathError(
            "write_xpath_invalid", "Le XPath doit désigner un élément, un attribut ou un texte."
        )
    if getattr(node, "is_attribute", False):
        if operation.op == "remove":
            del owner.attrib[node.attrname]
        elif operation.op == "set":
            owner.set(node.attrname, str(operation.value))
        else:
            raise PathError("write_path_not_single", "Cet attribut existe déjà : « set ».")
        return
    if getattr(node, "is_text", False) or getattr(node, "is_tail", False):
        text = "" if operation.op == "remove" else str(operation.value)
        if getattr(node, "is_tail", False):
            owner.tail = text
        else:
            owner.text = text
        return
    raise PathError(
        "write_xpath_invalid", "Le XPath doit désigner un élément, un attribut ou un texte."
    )
