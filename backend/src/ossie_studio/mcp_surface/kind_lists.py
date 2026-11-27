"""Ce que rend la liste de chaque genre (`list_documents`, epic « Primitives MCP —
écriture commune par genre », ticket 2113e7bc).

Un lister par genre lit l'espace **par le service de l'écran** (cartographie,
modèles, processus, référentiels, gouvernance) et rend ses documents triés, avec
leur identité, leur nom, leur fichier et les champs de synthèse que rendaient les
lectures par domaine qu'il remplace (`list_carto_types`, `list_carto_objects`,
`list_carto_models`, `list_referentiels`, `search_referentiel_values`,
`list_tags`, `list_classifications`, `list_certifications`). Il applique les
filtres propres à son genre (déclarés dans le registre) ; l'identité partielle, la
recherche texte, la pagination et les versions sont communes (`document_reads`).
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.catalog import ArchimateType, catalog
from ossie_studio.cartographie.diagram import (
    diagram_path,
    graphic_path,
    list_diagrams,
    list_rules,
    read_diagram,
    rules_path,
)
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import type_path
from ossie_studio.cartographie.process import list_processes, parse_process, process_path
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.governance.repo import (
    cert_path,
    read_certs,
    read_schemes,
    read_tags,
    scheme_path,
    tag_path,
)
from ossie_studio.mcp_surface.kind_base import Item, KindError, ListFilter, ListQuery
from ossie_studio.mcp_surface.kind_stores import value_version
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import list_referentiels, model_path, values_path
from ossie_studio.referentiels.values import key_of
from ossie_studio.sources.asset_files import extend_name
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import list_curation, read_tri_rules
from ossie_studio.sources.paths import ASSETS_DIR, source_rel_dir
from ossie_studio.sources.service import read_sources_tree

# --- Les filtres déclarés -----------------------------------------------------------------

NATURE = ListFilter(
    "nature",
    "'object' or 'relation' (a relationship is an object of a relationship type).",
    choices=("object", "relation"),
)
ARCHIMATE = ListFilter("archimate", "ArchiMate type, e.g. 'ApplicationComponent'.")
SOURCE = ListFilter("source", "Key of an object: the relationships that start from it.")
CIBLE = ListFilter("cible", "Key of an object: the relationships that end on it.")
CONTAINS = ListFilter("contains", "Key of an object or relationship: the models that keep it.")
DETAIL_OF = ListFilter(
    "detail_of", "Key of a business process of the map: the BPMN processes that detail it."
)
EQUALS = ListFilter(
    "equals",
    "Equality on simple top-level properties of the values, e.g. {pays: 'FR'}; all must "
    "match. Needs identity.referentiel.",
    type="object",
)


def _nature(base: ArchimateType | None) -> str:
    return "relation" if base is not None and base.is_relation else "object"


def _given(query: ListQuery, name: str) -> Any:
    return query.filters.get(name)


# --- Cartographie -------------------------------------------------------------------------


def carto_types(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    cat = catalog()
    counts: dict[str, int] = {}
    for _path, obj in space.objects.values():
        counts[obj.type] = counts.get(obj.type, 0) + 1
    out = []
    for ctype in sorted(space.types.values(), key=lambda t: (t.title.casefold(), t.slug)):
        base = cat.get(ctype.archimate)
        nature = _nature(base)
        if _given(query, "nature") not in (None, nature):
            continue
        if _given(query, "archimate") not in (None, ctype.archimate):
            continue
        out.append(
            Item(
                {"slug": ctype.slug},
                ctype.title,
                type_path(ctype.slug),
                {
                    "archimate": ctype.archimate,
                    "layer": base.layer if base and not base.is_relation else None,
                    "nature": nature,
                    "objects": counts.get(ctype.slug, 0),
                },
            )
        )
    return out


def _end(space: Cartographie, key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    found = space.objects.get(key)
    if found is None:
        return {"key": key, "name": None, "type": None, "exists": False}
    return {
        "key": key,
        "name": found[1].name,
        "type": found[1].type,
        "exists": True,
        "path": found[0],
    }


def carto_objects(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    cat = catalog()
    out = []
    for path, obj in space.objects.values():
        ctype = space.types.get(obj.type)
        archimate = ctype.archimate if ctype else None
        nature = _nature(cat.get(archimate or ""))
        wanted = {
            "nature": nature,
            "archimate": archimate,
            "source": obj.source,
            "cible": obj.target,
        }
        if any(_given(query, f) not in (None, value) for f, value in wanted.items()):
            continue
        summary: dict[str, Any] = {"archimate": archimate, "nature": nature}
        if obj.is_relation:
            summary["source"] = _end(space, obj.source)
            summary["target"] = _end(space, obj.target)
        out.append(Item({"type": obj.type, "key": obj.key}, obj.name, path, summary))
    out.sort(key=lambda i: (i.name.casefold(), i.identity["key"]))
    return out


def carto_models(root: Path, query: ListQuery) -> list[Item]:
    space = read_cartographie(root)
    wanted = _given(query, "contains")
    out = []
    for slug in list_diagrams(root):
        read = read_diagram(root, slug, space)
        diagram = read.diagram
        problems = [{"code": p.code, "detail": p.detail, "field": p.field} for p in read.errors]
        related = (graphic_path(slug),)
        if diagram is None:  # illisible : nommé, sans contenu
            if not wanted:
                summary = {"readable": False, "problems": problems}
                out.append(Item({"slug": slug}, slug, read.path, summary, related=related))
            continue
        if wanted and wanted not in diagram.elements and wanted not in diagram.relationships:
            continue
        summary = {
            "readable": True,
            "id": diagram.id,
            "description": diagram.description,
            "objects": len(diagram.elements),
            "relations": len(diagram.relationships),
            "views": [
                {"id": v.id, "name": v.name, "viewpoint": v.viewpoint or None}
                for v in diagram.views
            ],
            "rules": diagram.rules,
            "problems": problems,
        }
        name = diagram.name or diagram.id
        out.append(Item({"slug": slug}, name, read.path, summary, related=related))
    return out


def carto_graphics(root: Path, query: ListQuery) -> list[Item]:
    return [
        Item({"slug": slug}, slug, graphic_path(slug), {"model": diagram_path(slug)})
        for slug in list_diagrams(root)
        if (root / graphic_path(slug)).is_file()
    ]


def carto_rules(root: Path, query: ListQuery) -> list[Item]:
    return [Item({"slug": slug}, slug, rules_path(slug)) for slug in list_rules(root)]


def processes(root: Path, query: ListQuery) -> list[Item]:
    wanted = _given(query, "detail_of")
    out = []
    for key in list_processes(root):
        path = process_path(key)
        try:
            process = parse_process((root / path).read_text(encoding="utf-8"))
        except (CartographieError, UnicodeDecodeError):
            if not wanted:
                out.append(Item({"key": key}, key, path, {"readable": False}))
            continue
        if wanted and process.detail_of != wanted:
            continue
        summary = {
            "readable": True,
            "detail_of": process.detail_of,
            "has_diagram": process.has_diagram,
            "elements": process.elements,
        }
        out.append(Item({"key": key}, process.name or key, path, summary))
    return out


# --- Référentiels -------------------------------------------------------------------------


def referentiel_models(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for summary in published.summaries(root):
        name = summary.pop("name")
        title = summary.get("title") or name
        out.append(Item({"referentiel": name}, title, model_path(name), summary))
    return out


def _scalars(value: dict[str, Any]) -> str:
    return " ".join(str(v) for v in value.values() if isinstance(v, str | int | float))


def referentiel_values(root: Path, query: ListQuery) -> list[Item]:
    """Les valeurs, triées par clé comme `search_referentiel_values` ; `equals` filtre
    par égalité exactement comme elle (même fonction du service)."""
    name = query.identity.get("referentiel")
    equals = _given(query, "equals") or {}
    if equals and not name:
        raise KindError(
            "list_filter_invalid",
            "$.filters.equals",
            "« equals » filtre les valeurs d'un référentiel : donner aussi identity.referentiel.",
        )
    names = [str(name)] if name else list_referentiels(root)
    out = []
    for ref_name in names:
        try:
            found = published.search_values(root, ref_name, equals, sys.maxsize, 0)
        except ReferentielError as exc:
            if exc.code == "referentiel_filter_unknown":
                prop = exc.detail.get("property")
                raise KindError(
                    "list_filter_invalid",
                    f"$.filters.equals.{prop}",
                    f"« {prop} » n'est pas une propriété simple de premier niveau du "
                    f"référentiel « {ref_name} » : elle ne peut pas servir de filtre.",
                    received=prop,
                ) from None
            if name:
                raise
            continue  # un référentiel illisible ne casse pas la liste de tous
        fields = model_key(published.model_of(root, ref_name))
        for value in found["values"]:
            parts = key_of(value, fields) or ()
            single = len(fields) == 1 and parts
            key: Any = parts[0] if single else dict(zip(fields, parts, strict=False))
            out.append(
                Item(
                    {"referentiel": ref_name, "key": key},
                    " / ".join(str(p) for p in parts),
                    values_path(ref_name),
                    {"value": value},
                    text=_scalars(value),
                    version=value_version(value),
                )
            )
    return out


# --- Schémas raw et d'assets, en lecture seule (ticket 56f3784a) --------------------------


def _sources(root: Path, query: ListQuery) -> list[str]:
    """La source demandée (refus nommé si elle n'existe pas), ou toutes."""
    wanted = query.identity.get("source")
    known = [entry.slug for entry in read_sources_tree(root)]
    if wanted is None:
        return known
    if wanted not in known:
        raise KindError(
            "write_identity_invalid",
            "source",
            f"La source « {wanted} » n'existe pas dans l'espace (list_sources).",
            received=wanted,
        )
    return [str(wanted)]


def raw_schemas(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_raw_schemas` : chaque schéma collecté, son nombre de champs
    et s'il est retenu en asset."""
    out = []
    for source in _sources(root, query):
        directory = source_rel_dir(source)
        for obj in list_curation(root, source, (), True):
            if obj.orphan:
                continue
            summary = {"title": obj.title, "kind": obj.kind, "fields": obj.fields}
            out.append(
                Item(
                    {"source": source, "name": obj.path},
                    obj.title or obj.path,
                    f"{directory}/{RAW_DIR}/{obj.path}",
                    {**summary, "retained": obj.retained},
                )
            )
    return out


def asset_schemas(root: Path, query: ListQuery) -> list[Item]:
    """Ce que rendait `list_assets` : chaque asset retenu, orphelin ou non (son raw a
    disparu), et la règle de tri qui l'écarte."""
    out = []
    for source in _sources(root, query):
        directory = source_rel_dir(source)
        for obj in list_curation(root, source, read_tri_rules(root, source), True):
            if obj.retained == "none":
                continue
            base = f"{directory}/{ASSETS_DIR}"
            summary = {
                "title": obj.title,
                "kind": obj.kind,
                "fields": obj.fields,
                "retained": obj.retained,
                "orphan": obj.orphan,
                "excluded_by": obj.excluded_by,
            }
            out.append(
                Item(
                    {"source": source, "name": obj.path},
                    obj.title or obj.path,
                    f"{base}/{obj.path}",
                    summary,
                    # Le travail humain sur l'asset (non publié s'il l'est).
                    related=(f"{base}/{extend_name(obj.path)}",),
                )
            )
    return out


# --- Gouvernance --------------------------------------------------------------------------


def tags(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for tag in read_tags(root):
        summary = {k: v for k, v in asdict(tag).items() if k != "id"}
        out.append(Item({"id": tag.id}, tag.id, tag_path(tag.id), summary, text=tag.description))
    return out


def classifications(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for scheme in read_schemes(root):
        summary = {k: v for k, v in asdict(scheme).items() if k not in ("id", "name")}
        out.append(
            Item(
                {"id": scheme.id},
                scheme.name,
                scheme_path(scheme.id),
                summary,
                text=scheme.description,
            )
        )
    return out


def certifications(root: Path, query: ListQuery) -> list[Item]:
    out = []
    for cert in read_certs(root):
        summary = {k: v for k, v in asdict(cert).items() if k not in ("slug", "name")}
        out.append(
            Item({"slug": cert.slug}, cert.name, cert_path(cert.id), summary, text=cert.description)
        )
    return out
