"""Ce que l'éditeur BPMN demande à Ossie (epic « Cartographie — processus BPMN », ticket
48335b93) : la boîte à outils, ce que devient un objet posé, et ce qu'un élément du
dessin peut désigner. C'est la cartographie qui décide (règle « souple », Gael) : les
outils « Liés au processus » viennent des relations ArchiMate du processus métier
détaillé ; poser un objet non relié reste permis, avec un avertissement.

Types ArchiMate que l'on pose (et rien d'autre ; tout autre type se décide avec Gael) :
acteur → participant (piscine), rôle → couloir, objet métier → objet de données,
application → stockage de données, collaboration → rattachée à la collaboration du
diagramme (avec la liste de ses membres).

Les objets sont désignés par `type/CLÉ` (ticket 421832ee). Un outil porte la clé
seule (le composant la recopie dans la référence, avec le type que rend la pose),
sauf si elle est ambiguë dans l'espace : il porte alors `type/CLÉ`, que
l'enregistrement remet en clé seule."""

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
    """Une relation de l'un de ces types ArchiMate va-t-elle de `source` à `target`
    (identifiants `type/CLÉ`) ?"""
    for ref, (_path, obj) in space.objects.items():
        if space.ends(obj) == (source, target) and space.archimate(ref) in kinds:
            return True
    return False


def _posable(space: Cartographie, ref: str) -> str | None:
    archimate = space.archimate(ref)
    return archimate if archimate in GENRES else None


def is_linked(space: Cartographie, detail_of: str, key: str) -> bool:
    """L'objet est-il relié au processus métier par la relation de son genre ? (Les
    deux sont désignés par `type/CLÉ`, ou par une clé seule non ambiguë.)"""
    detail, ref = space.resolve(detail_of), space.resolve(key)
    archimate = space.archimate(ref)
    link = LINKS.get(archimate or "")
    if link is None or detail is None or ref is None:
        return False
    source, target = (ref, detail) if link.object_is_source else (detail, ref)
    return _related(space, source, target, (link.archimate,))


def members(space: Cartographie, collaboration: str) -> list[dict[str, Any]]:
    """Les acteurs et rôles que la collaboration agrège ou compose, avec le genre à
    créer pour chacun (acteur → participant, rôle → couloir)."""
    collab = space.resolve(collaboration)
    found = []
    for ref, (_path, obj) in space.objects.items():
        source, target_ref = space.ends(obj)
        if collab is None or source != collab or space.archimate(ref) not in MEMBERSHIP:
            continue
        target = space.find(target_ref)
        archimate = space.archimate(target_ref)
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


def tool_key(space: Cartographie, obj: CartoObject) -> str:
    """Ce que porte l'outil d'un objet : sa clé seule, ou `type/CLÉ` si elle est
    ambiguë dans l'espace."""
    return obj.key if space.resolve(obj.key) == obj.ref else obj.ref


def _tool(space: Cartographie, obj: CartoObject, archimate: str, cat: Catalog) -> dict[str, Any]:
    ctype = space.types.get(obj.type)
    base = cat.get(archimate)
    return {
        "cle": tool_key(space, obj),
        "libelle": obj.name or obj.key,
        "icone": base.icon() if base else "",
        "description": f"{obj.key} · {ctype.title if ctype else obj.type}",
    }


def _tools(space: Cartographie, refs: list[str], cat: Catalog) -> list[dict[str, Any]]:
    return [
        _tool(space, space.objects[r][1], str(space.archimate(r)), cat)
        for r in _sorted(space, refs)
    ]


def _sorted(space: Cartographie, refs: list[str]) -> list[str]:
    def rank(ref: str) -> tuple[int, str, str]:
        obj = space.objects[ref][1]
        return (_ORDER.index(str(space.archimate(ref))), (obj.name or obj.key).casefold(), ref)

    return sorted(refs, key=rank)


def process_toolbox(
    space: Cartographie, detail_of: str | None, cat: Catalog | None = None
) -> list[dict[str, Any]]:
    """La boîte à outils de l'éditeur BPMN : « Liés au processus » d'abord (ce qui est
    affecté au processus métier, ce qu'il accède, ce qui le sert), puis les autres
    objets de l'espace du même genre. Sans processus métier (ou s'il n'existe plus) :
    un seul groupe, tout l'espace. Les libellés des groupes sont traduits par l'écran."""
    cat = cat or catalog()
    refs = [r for r in space.objects if _posable(space, r)]
    detail = space.resolve(detail_of)
    if detail is None:
        tools = _tools(space, refs, cat)
        return [{"id": SPACE_GROUP, "libelle": "", "outils": tools}] if tools else []
    linked = [r for r in refs if is_linked(space, detail, r)]
    others = [r for r in refs if r not in linked]
    groups = []
    for group, chosen in ((LINKED_GROUP, linked), (OTHERS_GROUP, others)):
        tools = _tools(space, chosen, cat)
        if tools:
            groups.append({"id": group, "libelle": "", "outils": tools})
    return groups


def process_drop(
    space: Cartographie, key: str, detail_of: str | None, collaboration: str | None = None
) -> dict[str, Any]:
    """Ce que devient l'objet posé (`key` : ce que porte son outil) : son nom, son
    type, le genre d'élément BPMN à créer (et les membres pour une collaboration).
    S'il n'est pas relié — au processus métier, ou à la collaboration rattachée pour
    un participant — un avertissement nomme la relation qui manque (l'écran propose
    « Créer la relation »). Rien n'est créé ici et la pose n'est jamais bloquée."""
    ref = space.resolve(key)
    if ref is None:
        raise CartographieError("object_not_found", status=404, key=key)
    obj = space.objects[ref][1]
    archimate = _posable(space, ref)
    if archimate is None:
        raise CartographieError(
            "process_drop_unsupported", key=key, archimate=space.archimate(ref) or ""
        )
    result: dict[str, Any] = {
        "nom": obj.name or obj.key,
        "type": obj.type,
        "genre": GENRES[archimate],
    }
    if archimate == COLLABORATION:
        result["membres"] = members(space, ref)
    warning = _warning(space, ref, archimate, detail_of, collaboration)
    if warning is not None:
        result["avertissement"] = warning
    return result


def _name(space: Cartographie, ref: str) -> str:
    found = space.find(ref)
    return (found[1].name or found[1].key) if found else ref


def _warning(
    space: Cartographie,
    ref: str,
    archimate: str,
    detail_of: str | None,
    collaboration: str | None,
) -> dict[str, Any] | None:
    """La relation qui manque, nommée (l'écran en fait la phrase et l'action)."""
    detail = space.resolve(detail_of)
    collab = space.resolve(collaboration)
    if archimate == ACTOR and collab and _related(space, collab, ref, MEMBERSHIP):
        return None  # membre de la collaboration rattachée : relié
    if detail is not None:
        if is_linked(space, detail, ref):
            return None
        link = LINKS[archimate]
        source, target = (ref, detail) if link.object_is_source else (detail, ref)
        return _missing(space, link.archimate, source, target)
    if archimate == ACTOR and collab:
        return _missing(space, "Aggregation", collab, ref)
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
        {"cle": obj.key, "nom": obj.name or obj.key, "type": obj.type}
        for ref, (_path, obj) in space.objects.items()
        if space.archimate(ref) in kinds
    ]
    return sorted(found, key=lambda o: (o["nom"].casefold(), o["cle"]))
