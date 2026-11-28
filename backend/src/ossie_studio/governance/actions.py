"""Gestes de gouvernance qui écrivent : tags gouvernés, schémas de classification
et leurs niveaux, certifications.

Service commun à l'écran (routes `api/governance.py`, `api/classifications.py`)
et à l'agent (écriture commune par type de document : `write_documents` / `patch_document`,
validateurs `mcp_surface/document_type_stores.py`) : une seule règle métier. Chaque
fonction s'exécute dans la vue de la personne (`root`), à l'intérieur du geste
d'écriture de son brouillon : elle vérifie tout **avant** d'écrire, puis rend
l'objet tel qu'il est écrit.

Modifications : seules les clés données dans `changes` changent. L'écran les
donne toutes (il remplace l'objet), comme l'agent qui écrit le document entier.
La validation porte sur l'objet complet obtenu.

Refus : `GovernanceRefused` porte le code nommé (le même à l'écran et pour
l'agent), son statut HTTP pour l'écran et ses détails.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from ossie_studio.governance import repo
from ossie_studio.governance.certifications import validate_certification
from ossie_studio.governance.classifications import (
    derive_scheme_id,
    validate_level,
    validate_scheme,
)
from ossie_studio.governance.repo import Cert, Level, Scheme, Tag
from ossie_studio.governance.tags import normalize_tag_id, validate_tag_fields
from ossie_studio.slugs import is_valid_slug, slugify

Values = Mapping[str, Any]

# Champs modifiables de chaque objet (l'identifiant et le slug sont figés).
TAG_FIELDS = ("owner", "description", "links", "properties")
SCHEME_FIELDS = (
    "name",
    "description",
    "badge_prefix",
    "multi_value",
    "is_primary",
    "display_order",
)
LEVEL_FIELDS = (
    "name",
    "label",
    "short_name",
    "rank",
    "description",
    "criteria",
    "color",
    "icon",
)
CERT_FIELDS = ("name", "rank", "description", "criteria", "tag", "icon", "restricted")


class GovernanceRefused(Exception):
    """Refus nommé d'un geste de gouvernance ; rien n'a été écrit."""

    def __init__(self, code: str, status: int, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def _invalid(fields: list[str]) -> NoReturn:
    raise GovernanceRefused("invalid_request", 400, fields=fields)


def _text(values: Values, key: str) -> str:
    return str(values.get(key) or "")


def _merged(current: object, fields: Sequence[str], changes: Values) -> dict[str, Any]:
    """L'objet courant, avec les seules clés de `changes` remplacées."""
    return {name: getattr(current, name) for name in fields} | {
        name: value for name, value in changes.items() if name in fields
    }


# --- Tags ------------------------------------------------------------------------------


def _tag(tag_id: str, values: Values) -> Tag:
    links = values.get("links") or {}
    properties = values.get("properties") or {}
    errors = validate_tag_fields(tag_id, links, properties)
    if errors:
        _invalid(errors)
    return Tag(
        tag_id,
        _text(values, "owner").strip(),
        _text(values, "description").strip(),
        dict(links),
        dict(properties),
    )


def _find_tag(root: Path, tag_id: str) -> Tag:
    tag = next((t for t in repo.read_tags(root) if t.id == tag_id), None)
    if tag is None:
        raise GovernanceRefused("tag_not_found", 404, tag_id=tag_id)
    return tag


def create_tag(root: Path, raw_id: str, values: Values) -> Tag:
    tag = _tag(normalize_tag_id(raw_id), values)
    if any(t.id == tag.id for t in repo.read_tags(root)):
        raise GovernanceRefused("tag_id_taken", 409, tag_id=tag.id)
    repo.write_tag(root, tag)
    return tag


def update_tag(
    root: Path, raw_id: str, changes: Values, rule_tags: frozenset[str] = frozenset()
) -> Tag:
    """`rule_tags` : tags posés par une règle active sans être encore dans le
    vocabulaire (bug 44931826) ; les modifier les y enregistre."""
    tag_id = normalize_tag_id(raw_id)
    errors = validate_tag_fields(tag_id, {}, {})
    if errors:
        _invalid(errors)
    current = next((t for t in repo.read_tags(root) if t.id == tag_id), None)
    if current is None and tag_id in rule_tags:
        current = Tag(tag_id)
    if current is None:
        raise GovernanceRefused("tag_not_found", 404, tag_id=tag_id)
    tag = _tag(tag_id, _merged(current, TAG_FIELDS, changes))
    repo.write_tag(root, tag)
    return tag


def delete_tag(root: Path, raw_id: str) -> str:
    tag_id = normalize_tag_id(raw_id)
    if validate_tag_fields(tag_id, {}, {}):
        raise GovernanceRefused("tag_not_found", 404, tag_id=tag_id)
    _find_tag(root, tag_id)
    if not repo.delete_tag(root, tag_id):
        raise GovernanceRefused("tag_not_found", 404, tag_id=tag_id)
    return tag_id


# --- Schémas de classification et niveaux ------------------------------------------------


def find_scheme(root: Path, scheme_id: str) -> Scheme:
    scheme = next((s for s in repo.read_schemes(root) if s.id == scheme_id), None)
    if scheme is None:
        raise GovernanceRefused("scheme_not_found", 404, scheme_id=scheme_id)
    return scheme


def _level_errors(values: Values) -> list[str]:
    return validate_level(_text(values, "name"), _text(values, "short_name"))


def _level(scheme_id: str, values: Values, level_id: str, position: int) -> Level:
    errors = _level_errors(values)
    if errors:
        _invalid(errors)
    rank = values.get("rank", 0)
    return Level(
        level_id,
        scheme_id,
        _text(values, "name").strip(),
        _text(values, "label").strip(),
        _text(values, "short_name").strip(),
        rank if isinstance(rank, int) else 0,
        _text(values, "description"),
        _text(values, "criteria"),
        _text(values, "color"),
        _text(values, "icon"),
        position,
    )


def _check_level_free(scheme: Scheme, level: Level) -> None:
    """Unicité du nom et du nom court dans le schéma."""
    for other in scheme.levels:
        if other.id == level.id:
            continue
        if other.name == level.name:
            raise GovernanceRefused("level_name_taken", 409, name=level.name)
        if level.short_name and other.short_name == level.short_name:
            raise GovernanceRefused("level_short_name_taken", 409, short_name=level.short_name)


def _scheme_errors(values: Values) -> list[str]:
    return validate_scheme(
        _text(values, "name"), bool(values.get("multi_value")), bool(values.get("is_primary"))
    )


def create_scheme(root: Path, values: Values, levels: Sequence[Values]) -> Scheme:
    """Nouveau schéma (identifiant donné ou déduit du nom, figé ensuite), avec ses
    niveaux éventuels ; il se range après les schémas existants."""
    name = _text(values, "name")
    errors = _scheme_errors(values)
    for level_values in levels:
        errors.extend(_level_errors(level_values))
    given = _text(values, "id")
    scheme_id = derive_scheme_id(given if given.strip() else name)
    if not errors and not scheme_id:
        errors.append("scheme_id_invalid")
    if errors:
        _invalid(errors)
    scheme = Scheme(
        scheme_id,
        name.strip(),
        _text(values, "description").strip(),
        _text(values, "badge_prefix").strip(),
        bool(values.get("multi_value")),
        bool(values.get("is_primary")),
    )
    scheme.levels = [
        _level(scheme_id, level_values, repo.new_id(), i) for i, level_values in enumerate(levels)
    ]
    return insert_scheme(root, scheme)


def insert_scheme(root: Path, scheme: Scheme) -> Scheme:
    """Range un schéma déjà construit (création ou import) : identifiant libre,
    niveaux uniques, placé après les schémas existants."""
    existing = repo.read_schemes(root)
    if any(s.id == scheme.id for s in existing):
        raise GovernanceRefused("scheme_id_taken", 409, scheme_id=scheme.id)
    scheme.display_order = max((s.display_order for s in existing), default=-1) + 1
    for level in scheme.levels:
        _check_level_free(scheme, level)
    repo.write_scheme(root, scheme)
    return scheme


def update_scheme(root: Path, scheme_id: str, changes: Values) -> Scheme:
    scheme = find_scheme(root, scheme_id)
    merged = _merged(scheme, SCHEME_FIELDS, changes)
    errors = _scheme_errors(merged)
    if errors:
        _invalid(errors)
    scheme.name = _text(merged, "name").strip()
    scheme.description = _text(merged, "description").strip()
    scheme.badge_prefix = _text(merged, "badge_prefix").strip()
    scheme.multi_value = bool(merged["multi_value"])
    scheme.is_primary = bool(merged["is_primary"])
    order = merged["display_order"]
    scheme.display_order = order if isinstance(order, int) else scheme.display_order
    repo.write_scheme(root, scheme)
    return scheme


def delete_scheme(root: Path, scheme_id: str) -> None:
    find_scheme(root, scheme_id)
    if not repo.delete_scheme(root, scheme_id):
        raise GovernanceRefused("scheme_not_found", 404, scheme_id=scheme_id)


def _level_index(scheme: Scheme, level_id: str) -> int:
    index = next((i for i, lv in enumerate(scheme.levels) if lv.id == level_id), None)
    if index is None:
        raise GovernanceRefused("level_not_found", 404, scheme_id=scheme.id, level_id=level_id)
    return index


def add_level(root: Path, scheme_id: str, values: Values) -> Level:
    errors = _level_errors(values)
    if errors:
        _invalid(errors)
    scheme = find_scheme(root, scheme_id)
    level = _level(scheme_id, values, repo.new_id(), len(scheme.levels))
    _check_level_free(scheme, level)
    scheme.levels.append(level)
    repo.write_scheme(root, scheme)
    return level


def update_level(root: Path, scheme_id: str, level_id: str, changes: Values) -> Level:
    scheme = find_scheme(root, scheme_id)
    index = _level_index(scheme, level_id)
    level = _level(scheme_id, _merged(scheme.levels[index], LEVEL_FIELDS, changes), level_id, index)
    _check_level_free(scheme, level)
    scheme.levels[index] = level
    repo.write_scheme(root, scheme)
    return level


def delete_level(root: Path, scheme_id: str, level_id: str) -> None:
    scheme = find_scheme(root, scheme_id)
    del scheme.levels[_level_index(scheme, level_id)]
    repo.write_scheme(root, scheme)


def reorder_levels(root: Path, scheme_id: str, order: Sequence[str]) -> Scheme:
    """Nouvel ordre des niveaux : `order` contient exactement les niveaux du
    schéma, chacun une fois."""
    scheme = find_scheme(root, scheme_id)
    by_id = {lv.id: lv for lv in scheme.levels}
    if sorted(order) != sorted(by_id):
        _invalid(["level_order_invalid"])
    scheme.levels = [by_id[i] for i in order]
    for position, level in enumerate(scheme.levels):
        level.position = position
    repo.write_scheme(root, scheme)
    return scheme


# --- Certifications ------------------------------------------------------------------------


def _cert(cert_id: str, values: Values, slug: str = "") -> Cert:
    tag = normalize_tag_id(_text(values, "tag"))
    rank = values.get("rank", 1)
    errors = validate_certification(_text(values, "name"), rank, tag)
    if errors:
        _invalid(errors)
    return Cert(
        cert_id,
        _text(values, "name").strip(),
        int(rank),
        _text(values, "description").strip(),
        _text(values, "criteria"),
        tag,
        _text(values, "icon").strip(),
        bool(values.get("restricted")),
        slug,
    )


def _find_cert(certs: Sequence[Cert], cert_id: str) -> Cert:
    cert = next((c for c in certs if c.id == cert_id), None)
    if cert is None:
        raise GovernanceRefused("certification_not_found", 404, certification_id=cert_id)
    return cert


def _check_tag_free(certs: Sequence[Cert], cert: Cert) -> None:
    if any(c.tag == cert.tag and c.id != cert.id for c in certs):
        raise GovernanceRefused("certification_tag_taken", 409, tag=cert.tag)


def create_certification(root: Path, values: Values, slug: str = "") -> Cert:
    """Nouvelle certification. Slug donné : vérifié et unique ; sinon déduit du
    nom, suffixé s'il est pris (jamais d'erreur sur ce qui n'a pas été saisi)."""
    cert = _cert(repo.new_id(), values)
    given = slug.strip()
    if given and not is_valid_slug(given):
        _invalid(["certification_slug_invalid"])
    certs = repo.read_certs(root)
    taken = {c.slug for c in certs}
    if given:
        if given in taken:
            raise GovernanceRefused("certification_slug_taken", 409, slug=given)
        cert.slug = given
    else:
        base = slugify(cert.name, fallback="certification")
        cert.slug, counter = base, 2
        while cert.slug in taken:
            cert.slug, counter = f"{base}-{counter}", counter + 1
    _check_tag_free(certs, cert)
    repo.write_cert(root, cert)
    return cert


def update_certification(root: Path, cert_id: str, changes: Values) -> Cert:
    certs = repo.read_certs(root)
    current = _find_cert(certs, cert_id)
    cert = _cert(cert_id, _merged(current, CERT_FIELDS, changes), current.slug)
    _check_tag_free(certs, cert)
    repo.write_cert(root, cert)
    return cert


def delete_certification(root: Path, cert_id: str) -> None:
    _find_cert(repo.read_certs(root), cert_id)
    if not repo.delete_cert(root, cert_id):
        raise GovernanceRefused("certification_not_found", 404, certification_id=cert_id)
