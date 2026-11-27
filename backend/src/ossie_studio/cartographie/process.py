"""Format d'un processus BPMN de la cartographie (enabler 1d35290d, epic « Cartographie
— processus BPMN »).

Un processus = **un fichier XML BPMN 2.0 standard**, dessin compris (section `bpmndi`),
rangé dans `cartographie/processus/<clé en minuscules>.bpmn`. La clé suit la règle des
clés de la cartographie (majuscules, chiffres, tirets, unique, figée) ; elle est aussi
l'`id` du `bpmn:definitions`. Ossie **garde le fichier tel quel** : il le lit et le
contrôle, et n'y change que ce qui doit l'être (l'`id` du document à l'import, la
référence au processus métier détaillé), par des retouches ciblées du texte : un fichier
venu de bpmn.io, Camunda ou Signavio garde ses préfixes, ses extensions et son dessin.

**Lien avec la cartographie** : l'extension du composant `urban-bpmn`,
`<urban:reference cle="…" type="…"/>` dans `bpmn:extensionElements` (espace de noms
`https://github.com/gaelgael5/urban-bpmn/schema/1.0`), qu'un autre outil ignore :
- sur `bpmn:process` : le processus métier (`BusinessProcess`) dont il est le détail ;
- sur `bpmn:collaboration` : une collaboration métier (`BusinessCollaboration`), au plus
  une par fichier ;
- sur un participant, un couloir, un objet ou un stockage de données : l'objet Ossie.

Le XML garde le nom de chaque élément rattaché (lisible dans un autre outil) ; la source
est l'objet Ossie : la liste des objets référencés, noms à jour, est passée au composant
à l'ouverture, qui les applique (`references`, contrat d'urban-bpmn).

Contrôles à la lecture (avec l'identifiant en cause) : XML illisible ou qui n'est pas du
BPMN 2.0 ; dessin absent ; `id` du document différent de la clé ; référence vers une clé
inexistante ; processus métier désigné qui n'est pas un `BusinessProcess` ; collaboration
désignée qui n'est pas une `BusinessCollaboration` ; plus d'une collaboration rattachée.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException  # type: ignore[import-untyped]
from defusedxml import ElementTree as SafeET

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.cartographie.model import CARTO_DIR, is_valid_key
from ossie_studio.cartographie.space import Cartographie, read_cartographie

PROCESSES_DIR = f"{CARTO_DIR}/processus"
PROCESS_SUFFIX = ".bpmn"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
# L'extension du composant urban-bpmn (`src/moddle/urban.json`).
URBAN_NS = "https://github.com/gaelgael5/urban-bpmn/schema/1.0"
DETAIL_ARCHIMATE = "BusinessProcess"
COLLABORATION_ARCHIMATE = "BusinessCollaboration"

# Les éléments que l'on compte (BPMN 2.0, noms locaux).
FLOW_NODES = frozenset(
    {
        "task", "userTask", "serviceTask", "sendTask", "receiveTask", "manualTask",
        "businessRuleTask", "scriptTask", "subProcess", "transaction", "adHocSubProcess",
        "callActivity", "startEvent", "endEvent", "intermediateCatchEvent",
        "intermediateThrowEvent", "boundaryEvent", "exclusiveGateway", "inclusiveGateway",
        "parallelGateway", "complexGateway", "eventBasedGateway",
    }
)  # fmt: skip
FLOWS = frozenset({"sequenceFlow", "messageFlow"})
# Un élément rattaché, selon sa nature (nom local) : ce que montre une vignette.
ATTACHABLE = {
    "process": "process",
    "collaboration": "collaboration",
    "participant": "participant",
    "lane": "lane",
    "dataObjectReference": "data_object",
    "dataObject": "data_object",
    "dataStoreReference": "data_store",
}


def process_path(key: str) -> str:
    return f"{PROCESSES_DIR}/{key.lower()}{PROCESS_SUFFIX}"


def _local(tag: str) -> tuple[str, str]:
    """(espace de noms, nom local) d'une balise `{ns}nom`."""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


# --- Lecture ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """Un élément du processus rattaché à un objet Ossie."""

    element: str  # id BPMN de l'élément
    kind: str  # process | collaboration | participant | lane | data_object | data_store | other
    name: str  # nom porté par le XML (la source reste l'objet Ossie)
    key: str
    type: str = ""  # slug du type de l'espace, tel que l'extension le garde


@dataclass(frozen=True)
class Shape:
    """Une forme du dessin (`bpmndi:BPMNShape`) : géométrie et nature."""

    element: str
    kind: str  # nom local de l'élément BPMN dessiné
    name: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Edge:
    element: str
    points: tuple[tuple[float, float], ...]


@dataclass
class Process:
    """Ce qu'Ossie lit d'un fichier BPMN : identité, rattachements, nombres, dessin."""

    id: str  # id du bpmn:definitions
    name: str
    references: list[Reference] = field(default_factory=list)
    participants: int = 0
    lanes: int = 0
    elements: int = 0
    flows: int = 0
    shapes: list[Shape] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @property
    def has_diagram(self) -> bool:
        return bool(self.shapes)

    @property
    def detail_of(self) -> str | None:
        """La clé du processus métier dont ce processus est le détail."""
        return next((r.key for r in self.references if r.kind == "process"), None)

    @property
    def collaborations(self) -> list[Reference]:
        return [r for r in self.references if r.kind == "collaboration"]

    def attached(self) -> list[Reference]:
        """Les éléments rattachés autres que le processus et la collaboration."""
        return [r for r in self.references if r.kind not in ("process", "collaboration")]


def _reference_of(element: Element) -> tuple[str, str] | None:
    for child in element:
        if _local(child.tag) != (BPMN_NS, "extensionElements"):
            continue
        for value in child:
            if _local(value.tag) == (URBAN_NS, "reference") and value.get("cle"):
                return str(value.get("cle")), str(value.get("type") or "")
    return None


def _number(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0


def parse_process(text: str) -> Process:
    """Lit un fichier BPMN. Lève CartographieError « process_unreadable » (XML
    illisible, avec le message de l'analyseur) ou « process_not_bpmn »."""
    try:
        root = SafeET.fromstring(text)
    except (ParseError, DefusedXmlException) as exc:
        raise CartographieError("process_unreadable", detail=str(exc)) from None
    if _local(root.tag) != (BPMN_NS, "definitions"):
        raise CartographieError("process_not_bpmn", detail=root.tag)
    names: dict[str, tuple[str, str]] = {}  # id -> (nature, nom)
    process = Process(id=str(root.get("id") or ""), name="")
    first_process_name = collaboration_name = ""
    for element in root.iter():
        ns, local = _local(element.tag)
        if ns != BPMN_NS:
            continue
        element_id = str(element.get("id") or "")
        name = str(element.get("name") or "")
        if element_id:
            names[element_id] = (local, name)
        if local == "participant":
            process.participants += 1
        elif local == "lane":
            process.lanes += 1
        elif local in FLOW_NODES:
            process.elements += 1
        elif local in FLOWS:
            process.flows += 1
        elif local == "process" and not first_process_name:
            first_process_name = name
        elif local == "collaboration" and not collaboration_name:
            collaboration_name = name
        found = _reference_of(element)
        if found is not None:
            kind = ATTACHABLE.get(local, "other")
            process.references.append(Reference(element_id, kind, name, found[0], found[1]))
    process.name = first_process_name or collaboration_name
    for diagram_element in root.iter():
        ns, local = _local(diagram_element.tag)
        if ns != BPMNDI_NS or local not in ("BPMNShape", "BPMNEdge"):
            continue
        target = str(diagram_element.get("bpmnElement") or "")
        if local == "BPMNShape":
            bounds = next((c for c in diagram_element if _local(c.tag) == (DC_NS, "Bounds")), None)
            if bounds is None:
                continue
            kind, name = names.get(target, ("", ""))
            process.shapes.append(
                Shape(
                    target,
                    kind,
                    name,
                    _number(bounds.get("x")),
                    _number(bounds.get("y")),
                    _number(bounds.get("width")),
                    _number(bounds.get("height")),
                )
            )
        else:
            points = tuple(
                (_number(p.get("x")), _number(p.get("y")))
                for p in diagram_element
                if _local(p.tag) == (DI_NS, "waypoint")
            )
            process.edges.append(Edge(target, points))
    return process


def check_process(
    process: Process, path: str, key: str, space: Cartographie, cat: Catalog
) -> list[Problem]:
    """Les contrôles d'un processus contre la cartographie de la branche."""
    problems: list[Problem] = []

    def add(code: str, detail: str = "", where: str = "") -> None:
        problems.append(Problem(path, code, detail, field=where))

    if not process.has_diagram:
        add("process_no_diagram")
    if process.id != key:
        add("process_id_mismatch", process.id, "definitions")
    for ref in process.references:
        if ref.key not in space.objects:
            add("process_reference_unknown", ref.key, ref.element)
            continue
        archimate = space.archimate(ref.key)
        if ref.kind == "process" and archimate != DETAIL_ARCHIMATE:
            add("process_detail_not_business_process", ref.key, ref.element)
        if ref.kind == "collaboration" and archimate != COLLABORATION_ARCHIMATE:
            add("process_collaboration_not_business_collaboration", ref.key, ref.element)
    collaborations = process.collaborations
    if len(collaborations) > 1:
        for ref in collaborations[1:]:
            add("process_collaboration_multiple", ref.key, ref.element)
    return problems


@dataclass
class ProcessRead:
    key: str
    path: str
    process: Process | None
    problems: list[Problem]


def list_processes(root: Path) -> list[str]:
    """Les clés des processus de la branche (d'après le nom de leur fichier)."""
    folder = root / PROCESSES_DIR
    if not folder.is_dir():
        return []
    return sorted(f.name[: -len(PROCESS_SUFFIX)].upper() for f in folder.glob(f"*{PROCESS_SUFFIX}"))


def read_process(
    root: Path, key: str, space: Cartographie | None = None, cat: Catalog | None = None
) -> ProcessRead:
    """Lit un processus avec ses contrôles. Refus nommé « process_not_found »."""
    path = process_path(key)
    file = root / path
    if not is_valid_key(key) or not file.is_file():
        raise CartographieError("process_not_found", status=404, key=key)
    try:
        process = parse_process(file.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return ProcessRead(key, path, None, [Problem(path, "process_unreadable", str(exc))])
    except CartographieError as exc:
        detail = str(exc.detail.get("detail", ""))
        return ProcessRead(key, path, None, [Problem(path, exc.code, detail)])
    space = space if space is not None else read_cartographie(root, cat)
    problems = check_process(process, path, key, space, cat or catalog())
    return ProcessRead(key, path, process, sorted(set(problems)))


def referenced_objects(process: Process, space: Cartographie) -> list[dict[str, Any]]:
    """Les objets que le processus référence, **noms à jour** : ce que reçoit le
    composant à l'ouverture (`references`) pour remettre les noms des éléments."""
    out: dict[str, dict[str, Any]] = {}
    for ref in process.references:
        found = space.objects.get(ref.key)
        if found is None or ref.key in out:
            continue
        out[ref.key] = {"cle": ref.key, "nom": found[1].name, "type": found[1].type}
    return list(out.values())


def processes_detailing(root: Path, key: str) -> list[str]:
    """Les processus BPMN qui détaillent un processus métier (par sa clé)."""
    out = []
    for process_key in list_processes(root):
        try:
            text = (root / process_path(process_key)).read_text(encoding="utf-8")
            if parse_process(text).detail_of == key:
                out.append(process_key)
        except (CartographieError, UnicodeDecodeError):
            continue
    return out


# --- Écriture -----------------------------------------------------------------------------

_START_TAG = r"<(?:(?P<prefix>[A-Za-z_][\w.-]*):)?{name}(?=[\s/>])[^>]*>"
_ID_ATTR = re.compile(r"""(\sid\s*=\s*)(["'])(.*?)\2""", re.DOTALL)


def _start_tag(text: str, name: str, start: int = 0) -> re.Match[str] | None:
    """La première balise ouvrante `<x:name …>` (ou sans préfixe) à partir de `start`,
    hors commentaires et instructions."""
    pattern = re.compile(_START_TAG.format(name=re.escape(name)), re.DOTALL)
    position = start
    while True:
        found = pattern.search(text, position)
        if found is None:
            return None
        before = text[: found.start()]
        if before.rfind("<!--") > before.rfind("-->"):  # dans un commentaire
            position = found.end()
            continue
        return found


def with_definitions_id(text: str, key: str) -> str:
    """Le même texte, l'`id` du `bpmn:definitions` remplacé par la clé (ajouté s'il
    manque). Rien d'autre ne change."""
    tag = _start_tag(text, "definitions")
    if tag is None:
        raise CartographieError("process_not_bpmn")
    original = tag.group(0)
    found = _ID_ATTR.search(original)
    if found:
        changed = original[: found.start(3)] + key + original[found.end(3) :]
    else:
        name_end = len(tag.group("prefix") or "") + (2 if tag.group("prefix") else 1)
        name_end += len("definitions")
        changed = f'{original[:name_end]} id="{key}"{original[name_end:]}'
    return text[: tag.start()] + changed + text[tag.end() :]


def with_process_name(text: str, name: str) -> str:
    """Le même texte, le nom du premier `bpmn:process` remplacé (ajouté s'il manque)."""
    tag = _start_tag(text, "process")
    if tag is None:
        raise CartographieError("process_not_bpmn")
    original = tag.group(0)
    value = _escape(name)
    found = _NAME_ATTR.search(original)
    if found:
        changed = original[: found.start(3)] + value + original[found.end(3) :]
    else:
        name_end = len(f"{tag.group('prefix')}:" if tag.group("prefix") else "") + len("<process")
        changed = f'{original[:name_end]} name="{value}"{original[name_end:]}'
    return text[: tag.start()] + changed + text[tag.end() :]


_NAME_ATTR = re.compile(r"""(\sname\s*=\s*)(["'])(.*?)\2""", re.DOTALL)


def _escape(value: str) -> str:
    """Une valeur d'attribut XML (entre guillemets doubles)."""
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _reference_xml(key: str, type_slug: str) -> str:
    type_attr = f' type="{type_slug}"' if type_slug else ""
    return f'<urban:reference xmlns:urban="{URBAN_NS}" cle="{key}"{type_attr}/>'


def _without_urban_references(text: str, inner: str) -> str:
    """`inner` sans ses références d'urban-bpmn, quel que soit leur préfixe : celui
    déclaré en tête du fichier (bpmn.io) ou sur la balise elle-même (Ossie)."""
    declared = re.findall(r"xmlns:([A-Za-z_][\w.-]*)\s*=\s*[\"']" + re.escape(URBAN_NS), text)
    for prefix in set(declared):
        name = re.escape(prefix) + r":reference"
        inner = re.sub(rf"<{name}\b[^>]*/>", "", inner)
        inner = re.sub(rf"<{name}\b[^>]*>.*?</{name}\s*>", "", inner, flags=re.DOTALL)
    return inner


def with_detail(text: str, key: str | None, type_slug: str = "") -> str:
    """Le même texte, le premier `bpmn:process` rattaché au processus métier `key`
    (None : rattachement retiré). Seule la balise de l'extension change ; l'espace de
    noms d'urban-bpmn est déclaré sur elle, le reste du fichier est intact."""
    tag = _start_tag(text, "process")
    if tag is None:
        raise CartographieError("process_not_bpmn")
    prefix = f"{tag.group('prefix')}:" if tag.group("prefix") else ""
    opening = tag.group(0)
    if opening.endswith("/>"):  # processus vide, écrit en une balise
        if key is None:
            return text
        ext_open, ext_end = f"<{prefix}extensionElements>", f"</{prefix}extensionElements>"
        body = ext_open + _reference_xml(key, type_slug) + ext_end
        return (
            text[: tag.start()] + opening[:-2].rstrip() + ">" + body + f"</{prefix}process>"
            + text[tag.end() :]
        )  # fmt: skip
    close = text.find(f"</{prefix}process>", tag.end())
    ext = _start_tag(text, "extensionElements", tag.end())
    reference = _reference_xml(key, type_slug) if key is not None else ""
    direct = ext is not None and close != -1 and ext.start() < close
    if ext is not None and direct and _direct_child(text, tag.end(), ext.start()):
        ext_prefix = f"{ext.group('prefix')}:" if ext.group("prefix") else ""
        ext_close = text.find(f"</{ext_prefix}extensionElements>", ext.end())
        inner = text[ext.end() : ext_close]
        inner = _without_urban_references(text, inner)
        if not reference and not inner.strip():  # plus rien : le conteneur part aussi
            end = ext_close + len(f"</{ext_prefix}extensionElements>")
            return text[: ext.start()] + text[end:]
        return text[: ext.end()] + reference + inner + text[ext_close:]
    if key is None:
        return text
    body = f"<{prefix}extensionElements>{reference}</{prefix}extensionElements>"
    return text[: tag.end()] + body + text[tag.end() :]


def _direct_child(text: str, start: int, position: int) -> bool:
    """Vrai si la balise en `position` est un enfant direct de l'élément ouvert juste
    avant `start` : seul du blanc ou des commentaires les séparent."""
    between = re.sub(r"<!--.*?-->", "", text[start:position], flags=re.DOTALL)
    return between.strip() == ""


def new_process_xml(key: str, name: str, detail_of: str | None = None) -> str:
    """Un processus neuf : un événement de début, dessiné."""
    escaped = _escape(name)
    extension = (
        f"\n    <bpmn:extensionElements>{_reference_xml(detail_of, '')}</bpmn:extensionElements>"
        if detail_of
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:bpmndi="{BPMNDI_NS}"
    xmlns:dc="{DC_NS}" xmlns:di="{DI_NS}"
    id="{key}" targetNamespace="http://bpmn.io/schema/bpmn" exporter="Ossie Studio">
  <bpmn:process id="Process_1" name="{escaped}" isExecutable="false">{extension}
    <bpmn:startEvent id="StartEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <dc:Bounds x="180" y="160" width="36" height="36" />
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


def save_process(
    root: Path, key: str, text: str, *, create: bool, cat: Catalog | None = None
) -> list[Problem]:
    """Écrit un processus (création, import ou enregistrement du composant) : le texte
    tel quel, l'`id` du document aligné sur la clé. Refus nommés (rien n'est écrit) :
    clé invalide, processus déjà présent ou absent, XML illisible ou pas du BPMN, dessin
    absent, référence vers une clé inexistante ou d'un mauvais type. Rend les autres
    signalements (aucun aujourd'hui)."""
    cat = cat or catalog()
    if not is_valid_key(key):
        raise CartographieError("process_key_invalid", key=key)
    path = process_path(key)
    exists = (root / path).exists()
    if create and exists:
        raise CartographieError("process_exists", status=409, key=key)
    if not create and not exists:
        raise CartographieError("process_not_found", status=404, key=key)
    parse_process(text)  # illisible ou pas du BPMN : refusé avec le message de l'analyseur
    text = with_definitions_id(text, key)
    process = parse_process(text)
    problems = check_process(process, path, key, read_cartographie(root, cat), cat)
    if problems:
        raise CartographieError("process_invalid", problems=problems, key=key)
    (root / PROCESSES_DIR).mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")
    return []


def delete_process(root: Path, key: str) -> None:
    """Supprime le fichier d'un processus ; jamais les objets rattachés."""
    path = root / process_path(key)
    if not is_valid_key(key) or not path.is_file():
        raise CartographieError("process_not_found", status=404, key=key)
    path.unlink()


# --- Aperçu d'une vignette ------------------------------------------------------------------


def _preview_kind(kind: str) -> str:
    """La nature simplifiée d'une forme, pour l'aperçu (et ses symboles)."""
    if kind == "participant":
        return "pool"
    if kind == "lane":
        return "lane"
    if kind.endswith("Event"):
        return "event"
    if kind.endswith("Gateway"):
        return "gateway"
    if kind in ("dataObjectReference", "dataStoreReference", "dataObject"):
        return "data"
    if kind in FLOW_NODES:
        return "task"
    return "other"


def preview(process: Process, space: Cartographie) -> dict[str, Any]:
    """L'aperçu du dessin : les formes (géométrie, nature simplifiée, libellé — le nom
    à jour pour un élément rattaché) et les liens (leurs points)."""
    by_element = {r.element: r for r in process.references}

    def label(shape: Shape) -> str:
        ref = by_element.get(shape.element)
        found = space.objects.get(ref.key) if ref else None
        return found[1].name if found else shape.name

    return {
        "boxes": [
            {
                "x": s.x,
                "y": s.y,
                "l": s.width,
                "h": s.height,
                "kind": _preview_kind(s.kind),
                "label": label(s),
            }
            for s in process.shapes
        ],
        "links": [{"points": [list(p) for p in e.points]} for e in process.edges if e.points],
    }
