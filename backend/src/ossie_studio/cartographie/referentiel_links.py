"""Lier des propriétés d'un type de cartographie à un référentiel (ticket 43478978).

Gael : « être capable de faire des liens sur certaines propriétés avec des
référentiels de données ». Décisions (28/11) :

- le lien se déclare dans un **fichier d'extension** à côté du type,
  `cartographie/types/<slug>.extend.yaml`, pas dans son JSON Schema : « on fait
  propre puisqu'on n'est plus tenu de respecter des schémas JSON » ;
- n'importe quel référentiel peut être visé, quels que soient ses tags ;
- une valeur absente du référentiel est **refusée** à l'écran et par l'agent ;
  à l'import, la ligne est **taguée** et la personne peut la refuser (module
  commun d'import, `tags`) ; un objet déjà enregistré dont la valeur n'existe plus
  est **signalé** à la lecture, sur sa fiche (`property_referentiel_unknown`).

Format du fichier d'extension :

    referentiels:
      - referentiel: pays
        proprietes:
          pays_livraison: code      # propriété du type: propriété de la clé

Un lien couvre **toute la clé** du référentiel (clé composée : une propriété du
type par propriété de la clé), de même type simple ; une propriété du type n'est
liée qu'à un seul référentiel. Un lien n'est jamais retiré quand le référentiel
change : il devient cassé et il est signalé sur l'écran du type.

Les valeurs des référentiels sont lues une fois par lecture (`KeyIndex`), seulement
pour les référentiels liés ; l'import construit son index une fois (ticket eed2dd0d).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.cartographie.model import TYPES_DIR, CartoObject, is_empty
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import dump_model, model_key, model_tags
from ossie_studio.referentiels.repo import list_referentiels, read_referentiel
from ossie_studio.referentiels.values import key_of

EXTEND_SUFFIX = ".extend.yaml"
LINKS_KEY = "referentiels"
_SIMPLE = frozenset({"string", "integer", "number", "boolean"})


def extend_path(slug: str) -> str:
    return f"{TYPES_DIR}/{slug}{EXTEND_SUFFIX}"


@dataclass(frozen=True)
class ReferentielLink:
    """Un lien : le référentiel visé, et propriété du type → propriété de sa clé."""

    referentiel: str
    properties: dict[str, str]

    def document(self) -> dict[str, Any]:
        return {"referentiel": self.referentiel, "proprietes": dict(self.properties)}

    def payload(self) -> dict[str, Any]:
        return {"referentiel": self.referentiel, "proprietes": dict(self.properties)}


def parse_extend(text: str) -> list[ReferentielLink]:
    """Lit `<slug>.extend.yaml`. Lève CartographieError « type_extend_unreadable »."""
    try:
        data = load_schema(text)
    except SchemaReadError as exc:
        raise CartographieError("type_extend_unreadable", detail=str(exc)) from None
    if data is None:
        return []
    if not isinstance(data, dict):
        raise CartographieError("type_extend_unreadable", detail="not a mapping")
    raw = data.get(LINKS_KEY) or []
    if not isinstance(raw, list):
        raise CartographieError("type_extend_unreadable", detail=f"{LINKS_KEY} : liste attendue")
    links: list[ReferentielLink] = []
    for item in raw:
        name = item.get("referentiel") if isinstance(item, dict) else None
        props = item.get("proprietes") if isinstance(item, dict) else None
        if (
            not isinstance(name, str)
            or not isinstance(props, dict)
            or not props
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in props.items())
        ):
            raise CartographieError(
                "type_extend_unreadable", detail=f"{LINKS_KEY} : lien illisible"
            )
        links.append(ReferentielLink(name, dict(props)))
    return links


def dump_extend(links: list[ReferentielLink]) -> str:
    """Sortie déterministe : liens par référentiel, propriétés dans l'ordre donné."""
    ordered = sorted(links, key=lambda link: (link.referentiel, sorted(link.properties)))
    return dump_model({LINKS_KEY: [link.document() for link in ordered]})


def read_links(root: Path, slug: str) -> list[ReferentielLink]:
    file = root / extend_path(slug)
    if not file.is_file():
        return []
    return parse_extend(file.read_text(encoding="utf-8"))


def write_links(root: Path, slug: str, links: list[ReferentielLink]) -> None:
    """Écrit le fichier d'extension ; sans lien, le fichier disparaît."""
    file = root / extend_path(slug)
    if not links:
        file.unlink(missing_ok=True)
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(dump_extend(links), encoding="utf-8")


def renamed(links: list[ReferentielLink], renames: Mapping[str, str]) -> list[ReferentielLink]:
    """Les liens après le renommage de propriétés du type (ticket 859579bb)."""
    if not renames:
        return links
    return [
        ReferentielLink(
            link.referentiel, {renames.get(k, k): v for k, v in link.properties.items()}
        )
        for link in links
    ]


# --- Les référentiels vus depuis la cartographie --------------------------------------------


def property_kind(schema: Any) -> str | None:
    """Le type simple d'une propriété (une énumération ou une date sont des chaînes)."""
    if not isinstance(schema, dict):
        return None
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    if kind is None and "enum" in schema:
        return "string"
    return str(kind) if kind in _SIMPLE else None


@dataclass(frozen=True)
class ReferentielInfo:
    """Un référentiel tel qu'un lien le voit : titre, tags, clé et types de la clé.
    `error` : le référentiel ne se lit pas (ses liens sont cassés)."""

    name: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    key: list[str] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)  # propriété de la clé → type
    titles: dict[str, str] = field(default_factory=dict)  # propriété de la clé → libellé
    error: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "tags": list(self.tags),
            "key": [
                {"name": k, "type": self.kinds.get(k, ""), "title": self.titles.get(k, k)}
                for k in self.key
            ],
            "error": self.error,
        }


def _info(name: str, model: dict[str, Any]) -> ReferentielInfo:
    props = model.get("properties")
    props = props if isinstance(props, dict) else {}
    key = model_key(model)
    return ReferentielInfo(
        name=name,
        title=str(model.get("title") or name),
        tags=model_tags(model),
        key=key,
        kinds={k: kind for k in key if (kind := property_kind(props.get(k))) is not None},
        titles={k: str(props[k].get("title") or k) for k in key if isinstance(props.get(k), dict)},
    )


def read_info(root: Path, name: str) -> ReferentielInfo:
    try:
        return _info(name, read_referentiel(root, name).model)
    except ReferentielError as exc:
        return ReferentielInfo(name, error=exc.code)


def read_infos(root: Path) -> dict[str, ReferentielInfo]:
    """Tous les référentiels de la vue (brouillon compris), pour choisir un lien."""
    return {name: read_info(root, name) for name in list_referentiels(root)}


# --- Vérifier un lien ----------------------------------------------------------------------


def check_links(
    slug: str,
    type_properties: Mapping[str, Any],
    links: list[ReferentielLink],
    infos: Mapping[str, ReferentielInfo],
) -> list[Problem]:
    """Les problèmes des liens d'un type (`type_properties` : propriétés du type,
    attributs ArchiMate compris). Rangés sous le fichier d'extension, avec la
    propriété du type en cause."""
    where = extend_path(slug)
    problems: list[Problem] = []
    seen: set[str] = set()
    for link in links:
        info = infos.get(link.referentiel)
        for prop in link.properties:
            if prop in seen:
                problems.append(
                    Problem(where, "referentiel_link_taken", link.referentiel, field=prop)
                )
            seen.add(prop)
            if prop not in type_properties:
                problems.append(
                    Problem(where, "referentiel_link_field", link.referentiel, field=prop)
                )
        if info is None or info.error is not None:
            first = next(iter(link.properties))
            problems.append(
                Problem(where, "referentiel_link_unknown", link.referentiel, field=first)
            )
            continue
        targeted = list(link.properties.values())
        if sorted(targeted) != sorted(info.key) or len(set(targeted)) != len(targeted):
            first = next(iter(link.properties))
            detail = f"{link.referentiel} : {'+'.join(info.key)}"
            problems.append(Problem(where, "referentiel_link_incomplete", detail, field=first))
            continue
        for prop, target in link.properties.items():
            if prop not in type_properties:
                continue
            kind = property_kind(type_properties[prop])
            if kind is None or kind != info.kinds.get(target):
                detail = f"{link.referentiel}.{target}"
                problems.append(
                    Problem(where, "referentiel_link_type_mismatch", detail, field=prop)
                )
    return sorted(set(problems))


# --- Les valeurs ---------------------------------------------------------------------------


class KeyIndex:
    """Les clés des valeurs des référentiels liés, lues une fois chacune (à la
    demande). None : le référentiel ne se lit pas (rien n'est vérifié contre lui)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._keys: dict[str, set[tuple[Any, ...]] | None] = {}
        self._values: dict[str, list[dict[str, Any]]] = {}
        self._key: dict[str, list[str]] = {}

    def _load(self, name: str) -> None:
        if name in self._keys:
            return
        try:
            ref = read_referentiel(self.root, name)
        except ReferentielError:
            self._keys[name] = None
            return
        key = model_key(ref.model)
        values = [v for v in ref.values if isinstance(v, dict)]
        self._key[name] = key
        self._values[name] = values
        self._keys[name] = {k for v in values if (k := key_of(v, key)) is not None}

    def keys(self, name: str) -> set[tuple[Any, ...]] | None:
        self._load(name)
        return self._keys[name]

    def values(self, name: str) -> list[dict[str, Any]]:
        self._load(name)
        return self._values.get(name, [])

    def key(self, name: str) -> list[str]:
        self._load(name)
        return self._key.get(name, [])


def _ordered(link: ReferentielLink, key: list[str]) -> list[str]:
    """Les propriétés du type du lien, dans l'ordre de la clé du référentiel."""
    by_target = {target: prop for prop, target in link.properties.items()}
    return [by_target[k] for k in key if k in by_target]


def linked_value(
    properties: Mapping[str, Any], link: ReferentielLink, key: list[str]
) -> tuple[Any, ...] | None:
    """La clé que l'objet désigne par ce lien ; None quand il n'en donne aucune
    partie (propriétés vides : rien à vérifier)."""
    props = _ordered(link, key)
    if len(props) != len(key) or all(is_empty(properties.get(p)) for p in props):
        return None
    return tuple(None if is_empty(properties.get(p)) else properties.get(p) for p in props)


def shown(value: tuple[Any, ...]) -> str:
    """« FR » ; « FR+LYON » pour une clé composée (forme des référentiels)."""
    return "+".join("" if v is None else str(v) for v in value)


@dataclass(frozen=True)
class Absent:
    """Une valeur d'un objet absente du référentiel lié."""

    link: ReferentielLink
    properties: list[str]  # propriétés du type, dans l'ordre de la clé
    value: tuple[Any, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "referentiel": self.link.referentiel,
            "properties": list(self.properties),
            "value": shown(self.value),
        }


def absent_values(
    properties: Mapping[str, Any], links: list[ReferentielLink], index: KeyIndex
) -> list[Absent]:
    """Les valeurs que ces propriétés donnent et que leurs référentiels n'ont pas.
    Un référentiel illisible ne fait rien refuser (son lien est signalé cassé)."""
    found: list[Absent] = []
    for link in links:
        keys = index.keys(link.referentiel)
        if keys is None:
            continue
        key = index.key(link.referentiel)
        value = linked_value(properties, link, key)
        if value is not None and value not in keys:
            found.append(Absent(link, _ordered(link, key), value))
    return found


def absent_problems(
    obj: CartoObject,
    path: str,
    links: list[ReferentielLink],
    index: KeyIndex,
    before: CartoObject | None = None,
) -> list[Problem]:
    """`property_referentiel_unknown` sur la première propriété de chaque lien dont
    la valeur manque au référentiel ; détail « <référentiel> : <valeur> ».
    `before` : l'objet avant modification ; une valeur absente qu'il avait déjà
    n'est pas comptée (elle est signalée à la lecture, pas refusée)."""
    out: list[Problem] = []
    for a in absent_values(obj.properties, links, index):
        if before is not None:
            key = index.key(a.link.referentiel)
            if linked_value(before.properties, a.link, key) == a.value:
                continue
        detail = f"{a.link.referentiel} : {shown(a.value)}"
        out.append(Problem(path, "property_referentiel_unknown", detail, field=a.properties[0]))
    return out


def object_links(
    obj: CartoObject,
    path: str,
    links: list[ReferentielLink],
    infos: Mapping[str, ReferentielInfo],
    problems: list[Problem],
) -> list[dict[str, Any]]:
    """Pour la fiche d'un objet : chaque lien de son type, la valeur que l'objet y
    donne et son état — `empty` (rien à vérifier), `found`, `absent` (manque au
    référentiel, signalé), `broken` (le lien lui-même est cassé)."""
    absent = {
        p.field for p in problems if p.path == path and p.code == "property_referentiel_unknown"
    }
    broken = {
        p.field
        for p in problems
        if p.path == extend_path(obj.type) and p.code.startswith("referentiel_link_")
    }
    out = []
    for link in links:
        info = infos.get(link.referentiel)
        key = (
            info.key if info is not None and info.error is None else list(link.properties.values())
        )
        props = _ordered(link, key) or list(link.properties)
        value = linked_value(obj.properties, link, key) if info is not None else None
        mine = set(props)
        if value is None:
            status = "empty"
        elif mine & broken:
            status = "broken"
        elif mine & absent:
            status = "absent"
        else:
            status = "found"
        out.append(
            {
                "referentiel": link.referentiel,
                "title": (info.title if info is not None and info.title else link.referentiel),
                "properties": props,
                "value": shown(value) if value is not None else None,
                "parts": [None if v is None else v for v in value] if value is not None else [],
                "status": status,
            }
        )
    return out
