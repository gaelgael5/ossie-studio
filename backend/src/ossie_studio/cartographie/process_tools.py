"""Ce que l'éditeur BPMN demande à Ossie (epic « Cartographie — processus BPMN », ticket
48335b93) : la boîte à outils, ce que devient un objet posé, et ce qu'un élément du
dessin peut désigner. C'est la cartographie qui décide (règle « souple », Gael) : les
outils « Liés au processus » viennent des relations ArchiMate du processus métier
détaillé ; poser un objet non relié reste permis, avec un avertissement.

Types ArchiMate que l'on pose (et rien d'autre ; tout autre type se décide avec Gael) :
acteur → participant (piscine), rôle → couloir, objet métier → objet de données,
application → stockage de données, collaboration → rattachée à la collaboration du
diagramme (avec la liste de ses membres)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject
from ossie_studio.cartographie.space import Cartographie

ACTOR = "BusinessActor"
ROLE = "BusinessRole"
COLLABORATION = "BusinessCollaboration"
BUSINESS_OBJECT = "BusinessObject"
APPLICATION = "ApplicationComponent"
BUSINESS_PROCESS = "BusinessProcess"

# Le genre d'élément BPMN que crée la pose d'un objet, par type ArchiMate.
GENRES: dict[str, str] = {
    ACTOR: "participant",
    ROLE: "couloir",
    BUSINESS_OBJECT: "objet-donnees",
    APPLICATION: "stockage-donnees",
    COLLABORATION: "collaboration",
}

# Ordre des outils dans un groupe : acteurs, rôles, collaborations, objets, applications.
_ORDER = (ACTOR, ROLE, COLLABORATION, BUSINESS_OBJECT, APPLICATION)

# Ce qu'un élément du dessin peut désigner (délégué « attach » du composant).
ATTACHABLE: dict[str, tuple[str, ...]] = {
    "bpmn:Participant": (ACTOR,),
    "bpmn:Lane": (ROLE,),
    "bpmn:DataObjectReference": (BUSINESS_OBJECT,),
    "bpmn:DataStoreReference": (APPLICATION,),
    "bpmn:Collaboration": (COLLABORATION,),
    "bpmn:Process": (BUSINESS_PROCESS,),
}

LINKED_GROUP = "lies"
OTHERS_GROUP = "autres"
SPACE_GROUP = "objets"


@dataclass(frozen=True)
class Link:
    """La relation qui relie un objet posé au processus métier : son type ArchiMate et
    le sens (l'objet est-il la source ?)."""

    archimate: str
    object_is_source: bool


# Relié au processus métier : affecté (acteurs, rôles, collaborations), accédé (objets
# métier), le servant (applications).
LINKS: dict[str, Link] = {
    ACTOR: Link("Assignment", True),
    ROLE: Link("Assignment", True),
    COLLABORATION: Link("Assignment", True),
    BUSINESS_OBJECT: Link("Access", False),
    APPLICATION: Link("Serving", True),
}

# Les membres d'une collaboration : ce qu'elle agrège ou compose.
MEMBERSHIP = ("Aggregation", "Composition")


def _related(space: Cartographie, source: str, target: str, kinds: tuple[str, ...]) -> bool:
    """Une relation de l'un de ces types ArchiMate va-t-elle de `source` à `target` ?"""
    for key, (_path, obj) in space.objects.items():
        if obj.source == source and obj.target == target and space.archimate(key) in kinds:
            return True
    return False


def _posable(space: Cartographie, key: str) -> str | None:
    archimate = space.archimate(key)
    return archimate if archimate in GENRES else None


def is_linked(space: Cartographie, detail_of: str, key: str) -> bool:
    """L'objet est-il relié au processus métier par la relation de son genre ?"""
    archimate = space.archimate(key)
    link = LINKS.get(archimate or "")
    if link is None:
        return False
    source, target = (key, detail_of) if link.object_is_source else (detail_of, key)
    return _related(space, source, target, (link.archimate,))


def members(space: Cartographie, collaboration: str) -> list[dict[str, Any]]:
    """Les acteurs et rôles que la collaboration agrège ou compose, avec le genre à
    créer pour chacun (acteur → participant, rôle → couloir)."""
    found = []
    for key, (_path, obj) in space.objects.items():
        if obj.source != collaboration or space.archimate(key) not in MEMBERSHIP:
            continue
        target = space.objects.get(obj.target or "")
        archimate = space.archimate(obj.target or "")
        if target is None or archimate not in (ACTOR, ROLE):
            continue
        member = target[1]
        found.append(
            {
                "cle": member.key,
                "nom": member.name or member.key,
                "type": member.type,
                "genre": GENRES[str(archimate)],
            }
        )
    return sorted(found, key=lambda m: (m["genre"] != "participant", m["nom"].casefold()))


def _tool(space: Cartographie, obj: CartoObject, archimate: str, cat: Catalog) -> dict[str, Any]:
    ctype = space.types.get(obj.type)
    base = cat.get(archimate)
    return {
        "cle": obj.key,
        "libelle": obj.name or obj.key,
        "icone": base.icon() if base else "",
        "description": f"{obj.key} · {ctype.title if ctype else obj.type}",
    }


def _tools(space: Cartographie, keys: list[str], cat: Catalog) -> list[dict[str, Any]]:
    return [
        _tool(space, space.objects[k][1], str(space.archimate(k)), cat)
        for k in _sorted(space, keys)
    ]


def _sorted(space: Cartographie, keys: list[str]) -> list[str]:
    def rank(key: str) -> tuple[int, str, str]:
        obj = space.objects[key][1]
        return (_ORDER.index(str(space.archimate(key))), (obj.name or key).casefold(), key)

    return sorted(keys, key=rank)


def process_toolbox(
    space: Cartographie, detail_of: str | None, cat: Catalog | None = None
) -> list[dict[str, Any]]:
    """La boîte à outils de l'éditeur BPMN : « Liés au processus » d'abord (ce qui est
    affecté au processus métier, ce qu'il accède, ce qui le sert), puis les autres
    objets de l'espace du même genre. Sans processus métier (ou s'il n'existe plus) :
    un seul groupe, tout l'espace. Les libellés des groupes sont traduits par l'écran."""
    cat = cat or catalog()
    keys = [k for k in space.objects if _posable(space, k)]
    if not detail_of or detail_of not in space.objects:
        tools = _tools(space, keys, cat)
        return [{"id": SPACE_GROUP, "libelle": "", "outils": tools}] if tools else []
    linked = [k for k in keys if is_linked(space, detail_of, k)]
    others = [k for k in keys if k not in linked]
    groups = []
    for group, chosen in ((LINKED_GROUP, linked), (OTHERS_GROUP, others)):
        tools = _tools(space, chosen, cat)
        if tools:
            groups.append({"id": group, "libelle": "", "outils": tools})
    return groups


def process_drop(
    space: Cartographie, key: str, detail_of: str | None, collaboration: str | None = None
) -> dict[str, Any]:
    """Ce que devient l'objet posé : son nom, son type, le genre d'élément BPMN à créer
    (et les membres pour une collaboration). S'il n'est pas relié — au processus métier,
    ou à la collaboration rattachée pour un participant — un avertissement nomme la
    relation qui manque (l'écran propose « Créer la relation »). Rien n'est créé ici et
    la pose n'est jamais bloquée."""
    found = space.objects.get(key)
    if found is None:
        raise CartographieError("object_not_found", status=404, key=key)
    obj = found[1]
    archimate = _posable(space, key)
    if archimate is None:
        raise CartographieError(
            "process_drop_unsupported", key=key, archimate=space.archimate(key) or ""
        )
    result: dict[str, Any] = {"nom": obj.name or key, "type": obj.type, "genre": GENRES[archimate]}
    if archimate == COLLABORATION:
        result["membres"] = members(space, key)
    warning = _warning(space, key, archimate, detail_of, collaboration)
    if warning is not None:
        result["avertissement"] = warning
    return result


def _name(space: Cartographie, key: str) -> str:
    found = space.objects.get(key)
    return (found[1].name or key) if found else key


def _warning(
    space: Cartographie,
    key: str,
    archimate: str,
    detail_of: str | None,
    collaboration: str | None,
) -> dict[str, Any] | None:
    """La relation qui manque, nommée (l'écran en fait la phrase et l'action)."""
    detail = detail_of if detail_of and detail_of in space.objects else None
    collab = collaboration if collaboration and collaboration in space.objects else None
    if archimate == ACTOR and collab and _related(space, collab, key, MEMBERSHIP):
        return None  # membre de la collaboration rattachée : relié
    if detail is not None:
        if is_linked(space, detail, key):
            return None
        link = LINKS[archimate]
        source, target = (key, detail) if link.object_is_source else (detail, key)
        return _missing(space, link.archimate, source, target)
    if archimate == ACTOR and collab:
        return _missing(space, "Aggregation", collab, key)
    return None


def _missing(space: Cartographie, archimate: str, source: str, target: str) -> dict[str, Any]:
    return {
        "code": "process_drop_not_linked",
        "relation": archimate,
        "source": source,
        "source_name": _name(space, source),
        "target": target,
        "target_name": _name(space, target),
    }


def attach_choices(space: Cartographie, bpmn_type: str) -> list[dict[str, Any]]:
    """Les objets qu'un élément du dessin peut désigner (participant → acteurs, couloir →
    rôles, …), par nom ; aucun pour un autre élément."""
    kinds = ATTACHABLE.get(bpmn_type, ())
    found = [
        {"cle": key, "nom": obj.name or key, "type": obj.type}
        for key, (_path, obj) in space.objects.items()
        if space.archimate(key) in kinds
    ]
    return sorted(found, key=lambda o: (o["nom"].casefold(), o["cle"]))
