"""Lier une propriété d'asset à une propriété de référentiel (ticket 8269c642).

Décisions de Gael (24 novembre 2026) :
- le lien **déclare** (visible à l'écran, dans les fichiers, utilisable par un
  agent) ; la vérification des valeurs viendra dans un ticket à part ;
- il pointe vers le **fichier de schéma du référentiel** et une de ses propriétés ;
- une propriété n'est liée qu'à **un seul** référentiel ;
- clé composée : l'écran oblige à lier aussi les autres propriétés de la clé ;
  elles forment un **groupe** (même identifiant), enregistré en une fois, et
  délié en une fois.

Stockage : travail humain de l'asset, `<clé>.extend.yaml`, sous
`fields.<propriété>.referentiel` = `{schema, property, group?}` (la frontière
`asset_files` y range la clé mémoire `x-referentiel`) ; rien dans le
`<clé>.schema.yaml`, qui reste standard. Comme tout travail humain, le lien
survit au rafraîchissement depuis raw.

Un lien n'est jamais supprimé quand le référentiel change : il devient
**partiel** (la clé a gagné une propriété, une propriété du groupe a disparu)
ou **cassé** (référentiel ou propriété visée introuvable), et il est signalé.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import (
    MODEL_FILE,
    REFERENTIELS_DIR,
    list_referentiels,
    model_path,
    read_referentiel,
)

LINK_X = "x-referentiel"

OK = "ok"
PARTIAL = "partial"
BROKEN = "broken"

# Types simples liables (pas d'objet ni de tableau).
_SIMPLE = {"string", "integer", "number", "boolean"}


class LinkRefusedError(Exception):
    """Le groupe demandé ne peut pas être enregistré (code d'erreur, détail)."""

    def __init__(self, code: str, status: int = 400, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class LinkTarget:
    """Un référentiel vu depuis la curation : sa clé et ses propriétés simples."""

    name: str
    title: str
    schema: str
    key: list[str]
    properties: dict[str, str]  # nom → type

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "schema": self.schema,
            "key": self.key,
            "properties": [{"name": n, "type": t} for n, t in self.properties.items()],
        }


def _simple_type(prop: Any) -> str | None:
    if not isinstance(prop, dict):
        return None
    kind = prop.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    if kind in _SIMPLE:
        return str(kind)
    if kind is None and "enum" in prop:
        return "enum"
    return None


def read_targets(root: Path) -> dict[str, LinkTarget]:
    """Les référentiels de la vue (brouillon compris). Un référentiel illisible
    est ignoré : ses liens apparaîtront cassés."""
    out: dict[str, LinkTarget] = {}
    for name in list_referentiels(root):
        try:
            model = read_referentiel(root, name).model
        except ReferentielError:
            continue
        props = model.get("properties")
        simple = {
            str(n): t
            for n, p in (props.items() if isinstance(props, dict) else [])
            if (t := _simple_type(p)) is not None
        }
        out[name] = LinkTarget(
            name=name,
            title=str(model.get("title") or name),
            schema=model_path(name),
            key=model_key(model),
            properties=simple,
        )
    return out


def name_of(schema: str) -> str:
    """`referentiels/<nom>/modele.schema.yaml` → `<nom>` ("" sinon)."""
    parts = schema.split("/")
    if len(parts) == 3 and parts[0] == REFERENTIELS_DIR and parts[2] == MODEL_FILE:
        return parts[1]
    return ""


def link_of(prop: Any) -> dict[str, str] | None:
    """Le lien porté par une propriété, s'il a la bonne forme."""
    raw = prop.get(LINK_X) if isinstance(prop, dict) else None
    if not isinstance(raw, dict):
        return None
    schema, target = raw.get("schema"), raw.get("property")
    if not isinstance(schema, str) or not isinstance(target, str):
        return None
    link = {"schema": schema, "property": target}
    if isinstance(raw.get("group"), str) and raw["group"]:
        link["group"] = raw["group"]
    return link


def _properties(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = asset.get("properties")
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if isinstance(v, dict)}


def links_of(asset: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {n: link for n, p in _properties(asset).items() if (link := link_of(p)) is not None}


@dataclass(frozen=True)
class LinkView:
    referentiel: str
    title: str
    schema: str
    property: str
    group: str
    key_index: int  # rang dans la clé (1…), 0 hors clé
    key_size: int
    linked_keys: int  # propriétés de la clé couvertes par le groupe
    status: str  # ok | partial | broken
    missing: list[str] = field(default_factory=list)  # propriétés de la clé à lier
    members: list[str] = field(default_factory=list)  # propriétés de l'asset du groupe


def link_views(asset: dict[str, Any], targets: Mapping[str, LinkTarget]) -> dict[str, LinkView]:
    links = links_of(asset)
    out: dict[str, LinkView] = {}
    for prop_name, link in links.items():
        name = name_of(link["schema"])
        target = targets.get(name)
        group = link.get("group", "")
        members = sorted(
            n
            for n, other in links.items()
            if group and other.get("group") == group and other["schema"] == link["schema"]
        ) or [prop_name]
        if target is None or link["property"] not in target.properties:
            out[prop_name] = LinkView(
                name, target.title if target else name, link["schema"], link["property"],
                group, 0, 0, 0, BROKEN, [], members,
            )  # fmt: skip
            continue
        key = target.key
        index = key.index(link["property"]) + 1 if link["property"] in key else 0
        covered = {links[m]["property"] for m in members}
        missing = [k for k in key if k not in covered] if index and len(key) > 1 else []
        out[prop_name] = LinkView(
            name, target.title, link["schema"], link["property"], group, index, len(key),
            len([k for k in key if k in covered]), PARTIAL if missing else OK, missing, members,
        )  # fmt: skip
    return out


def issues(asset: dict[str, Any], targets: Mapping[str, LinkTarget]) -> int:
    """Liens à reprendre (partiels ou cassés), pour le compteur de l'asset."""
    return sum(1 for v in link_views(asset, targets).values() if v.status != OK)


def _group_members(asset: dict[str, Any], prop_name: str) -> list[str]:
    links = links_of(asset)
    link = links.get(prop_name)
    if link is None:
        return []
    group = link.get("group")
    if not group:
        return [prop_name]
    return [n for n, other in links.items() if other.get("group") == group]


def unlink(asset: dict[str, Any], prop_name: str) -> list[str]:
    """Délie la propriété **et tout son groupe** ; rend les propriétés déliées."""
    members = _group_members(asset, prop_name)
    props = _properties(asset)
    for member in members:
        props[member].pop(LINK_X, None)
    return members


def link_group(
    asset: dict[str, Any],
    target: LinkTarget,
    links: Mapping[str, str],
    replacing: str | None = None,
) -> list[str]:
    """Enregistre en une fois un groupe de liens `{propriété de l'asset:
    propriété du référentiel}` ; `replacing` : une propriété dont le groupe
    actuel est remplacé (« Modifier »). Lève LinkRefusedError :

    - propriété de l'asset ou du référentiel inconnue, ou non simple ;
    - propriété déjà liée (hors du groupe remplacé) : une propriété n'est liée
      qu'à un seul référentiel ;
    - clé composée incomplète : toutes les propriétés de la clé doivent être
      associées ;
    - plusieurs liens qui ne forment pas une clé composée."""
    props = _properties(asset)
    if not links:
        raise LinkRefusedError("referentiel_link_invalid")
    unknown = sorted(n for n in links if n not in props)
    if unknown:
        raise LinkRefusedError("referentiel_link_field", 404, fields=unknown)
    wrong = sorted(p for p in links.values() if p not in target.properties)
    if wrong:
        raise LinkRefusedError("referentiel_link_property", properties=wrong)
    targeted = list(links.values())
    if len(set(targeted)) != len(targeted):
        raise LinkRefusedError("referentiel_link_invalid")
    freed = set(_group_members(asset, replacing)) if replacing else set()
    existing = links_of(asset)
    taken = sorted(n for n in links if n in existing and n not in freed)
    if taken:
        raise LinkRefusedError("referentiel_link_taken", 409, fields=taken)
    key = target.key
    composite = len(key) > 1 and any(p in key for p in targeted)
    if composite:
        missing = [k for k in key if k not in targeted]
        if missing:
            raise LinkRefusedError("referentiel_link_incomplete", missing=missing)
        if any(p not in key for p in targeted):
            raise LinkRefusedError("referentiel_link_invalid")
    elif len(links) > 1:
        raise LinkRefusedError("referentiel_link_invalid")
    # « Modifier » un groupe garde son identifiant : même saisie, mêmes octets.
    previous = existing.get(replacing or "", {}).get("group", "")
    for member in freed:
        props[member].pop(LINK_X, None)
    group = (previous or uuid.uuid4().hex[:8]) if composite else ""
    for prop_name, prop_target in links.items():
        link: dict[str, str] = {"schema": target.schema, "property": prop_target}
        if group:
            link["group"] = group
        props[prop_name][LINK_X] = link
    return sorted(set(links) | freed)
