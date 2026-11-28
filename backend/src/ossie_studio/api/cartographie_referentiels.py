"""Écran d'un type de cartographie : « Liens vers les référentiels » (ticket 43478978).

Un bloc de l'écran du type lie des propriétés du type à la clé d'un référentiel ;
le lien s'écrit dans le fichier d'extension du type
(`cartographie/types/<slug>.extend.yaml`), dans le brouillon de la personne. Les
règles et le format sont ceux de `cartographie/referentiel_links.py`, jamais refaits
ici. La fiche d'un objet prend ses choix de valeurs ici (`…/referentiels/valeurs`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.cartographie import api_error, cartographie_target, draft_states
from ossie_studio.api.common import CartographieRead, CartographieWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.cartographie.errors import CartographieError, problem_payload
from ossie_studio.cartographie.referentiel_links import (
    KeyIndex,
    ReferentielInfo,
    ReferentielLink,
    extend_path,
    property_kind,
    read_infos,
    shown,
)
from ossie_studio.cartographie.space import Cartographie, read_cartographie, save_links
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.referentiels.values import key_of
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/cartographie")

# Valeurs proposées au plus par lien sur la fiche d'un objet (au-delà : `truncated`).
MAX_CHOICES = 5000
# Une propriété qui décrit la valeur, montrée à côté de sa clé dans la liste.
_HINT_NAMES = ("libelle", "label", "nom", "name", "title", "titre", "description")


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    referentiel: str = Field(min_length=1, max_length=200)
    proprietes: dict[str, str]


class LinksBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    links: list[LinkBody] = Field(default_factory=list, max_length=100)


def _type_or_404(space: Cartographie, slug: str) -> None:
    if slug not in space.types:
        raise ApiError("carto_type_not_found", status_code=404, slug=slug)


def _payload(
    space: Cartographie,
    slug: str,
    infos: dict[str, ReferentielInfo],
    states: dict[str, str],
) -> dict[str, Any]:
    """Les liens du type (avec leur référentiel, leurs problèmes et le nombre d'objets
    dont la valeur manque), les référentiels qu'on peut viser, et les propriétés
    simples du type qu'on peut lier."""
    path = extend_path(slug)
    objects = {p for p, _o in space.objects_of(slug)}
    problems = [p for p in space.problems if p.path == path]
    absent = [
        p for p in space.problems if p.code == "property_referentiel_unknown" and p.path in objects
    ]
    links = []
    for link in space.links.get(slug, []):
        info = infos.get(link.referentiel) or ReferentielInfo(link.referentiel, error="missing")
        mine = set(link.properties)
        links.append(
            {
                **link.payload(),
                "target": info.payload(),
                "problems": [problem_payload(p) for p in problems if p.field in mine],
                "absent": sum(1 for p in absent if p.field in mine),
            }
        )
    props = space.type_properties(slug)
    state = states.get(path)
    return {
        "slug": slug,
        "path": path,
        "state": state if state in ("created", "modified") else None,
        "links": links,
        # Problèmes du fichier entier (illisible) ou d'aucun lien en particulier.
        "problems": [problem_payload(p) for p in problems if not p.field],
        "referentiels": [
            i.payload() for i in sorted(infos.values(), key=lambda i: (i.title.casefold(), i.name))
        ],
        "properties": [
            {
                "name": name,
                "title": str(schema.get("title") or name) if isinstance(schema, dict) else name,
                "type": kind,
            }
            for name, schema in props.items()
            if (kind := property_kind(schema)) is not None
        ],
    }


def _read(root: Path, slug: str) -> tuple[Cartographie, dict[str, ReferentielInfo]]:
    space = read_cartographie(root)
    _type_or_404(space, slug)
    return space, read_infos(root)


@router.get("/types/{slug}/referentiels")
async def get_links(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Les liens du type vers des référentiels, et de quoi en ajouter."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        space, infos = await target.read(lambda root: _read(root, slug))
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return _payload(space, slug, infos, await draft_states(conn, workdir, branch_id))


@router.put("/types/{slug}/referentiels")
async def save_links_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    body: LinksBody,
) -> dict[str, object]:
    """Remplace les liens du type (fichier d'extension, dans le brouillon). Chaque
    lien couvre toute la clé du référentiel, de même type ; une propriété n'est liée
    qu'une fois. Refus `carto_type_links_invalid` avec les problèmes."""
    links = [ReferentielLink(b.referentiel, dict(b.proprietes)) for b in body.links]
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _saved, revision = await target.write(
            f"liens du type {slug} vers les référentiels",
            lambda root: save_links(root, slug, links),
        )
        space, infos = await target.read(lambda root: _read(root, slug))
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    states = await draft_states(conn, workdir, branch_id)
    return {**_payload(space, slug, infos, states), "revision": revision}


def _hint(value: dict[str, Any], key: list[str]) -> str:
    for name in _HINT_NAMES:
        if name not in key and isinstance(value.get(name), str) and value[name]:
            return str(value[name])
    for name, part in value.items():
        if name not in key and isinstance(part, str) and part:
            return part
    return ""


def _choices(root: Path, slug: str) -> list[dict[str, Any]]:
    space = read_cartographie(root)
    _type_or_404(space, slug)
    index = KeyIndex(root)
    out = []
    for link in space.links.get(slug, []):
        key = index.key(link.referentiel)
        by_target = {target: prop for prop, target in link.properties.items()}
        values = index.values(link.referentiel)
        choices = []
        for value in values[:MAX_CHOICES]:
            parts = key_of(value, key)
            if parts is None or any(k not in by_target for k in key):
                continue
            choices.append(
                {
                    "value": {by_target[k]: part for k, part in zip(key, parts, strict=True)},
                    "label": shown(parts),
                    "hint": _hint(value, key),
                }
            )
        out.append(
            {
                **link.payload(),
                # Les propriétés du type, dans l'ordre de la clé du référentiel.
                "properties": [by_target[k] for k in key if k in by_target],
                "readable": index.keys(link.referentiel) is not None,
                "choices": choices,
                "truncated": len(values) > MAX_CHOICES,
            }
        )
    return out


@router.get("/types/{slug}/referentiels/valeurs")
async def get_link_choices(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Pour la fiche d'un objet du type : chaque lien et les valeurs de son
    référentiel (la clé, rangée sous les propriétés du type, et un libellé)."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        links = await target.read(lambda root: _choices(root, slug))
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"slug": slug, "links": links}
