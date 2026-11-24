"""Écran « Référentiels » : modéliser un référentiel (epic « Référentiels »,
ticket « Page Référentiels et onglet Modèle »).

Lectures : la vue de la personne (publié + son brouillon) ; écritures : son
brouillon, par la brique « Format d'un référentiel » (`referentiels/`), jamais
autrement, y compris sur la branche racine (bug e7deb2b9). `readonly` reste
dans les réponses pour l'écran ; il est toujours faux aujourd'hui.

Enregistrer un modèle retire des valeurs les propriétés retirées du modèle ;
les valeurs obtenues doivent être valides pour le nouveau modèle (clé unique
comprise), sinon refus avec les problèmes. L'aperçu le dit avant d'enregistrer.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import WorkdirDep, space_credentials
from ossie_studio.api.common import ReferentielsRead, ReferentielsWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.deps import ConnDep
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.models import User
from ossie_studio.referentiels import published
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import dump_model
from ossie_studio.referentiels.model_change import adapt_values, preview
from ossie_studio.referentiels.repo import (
    REFERENTIELS_DIR,
    Referentiel,
    model_path,
    read_referentiel,
    referentiel_dir,
    write_referentiel,
)
from ossie_studio.referentiels.service import ReferentielTarget
from ossie_studio.referentiels.values import check_values, sorted_values
from ossie_studio.slugs import is_valid_slug
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/referentiels")

HISTORY_LIMIT = 50

# Refus de lecture d'un référentiel illisible : un seul code pour l'écran.
_UNREADABLE = {"model_unreadable", "values_unreadable", "import_unreadable"}


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    slug: str = ""


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: dict[str, Any]


class ValuesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: list[Any]


def api_error(exc: ReferentielError) -> ApiError:
    code = "referentiel_unreadable" if exc.code in _UNREADABLE else exc.code
    extra: dict[str, object] = dict(exc.detail)
    if exc.problems:
        extra["problems"] = [p.payload() for p in exc.problems]
    return ApiError(code, status_code=exc.status if code == exc.code else 409, **extra)


async def referentiel_target(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: User,
) -> ReferentielTarget:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    return ReferentielTarget(workdir, space, branch.git_ref, username, token, user.email)


async def _unpublished(conn: ConnDep, workdir: DraftWorkdir, branch_id: uuid.UUID) -> set[str]:
    """Les référentiels que le brouillon de la personne touche."""
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None:
        return set()
    prefix = f"{REFERENTIELS_DIR}/"
    return {
        o.path.split("/")[1]
        for o in await db_list_objects(conn, draft.id)
        if o.path.startswith(prefix) and o.path.count("/") >= 2
    }


async def read_in[T](target: ReferentielTarget, fn: Any) -> T:
    try:
        result: T = await target.read(fn)
    except ReferentielError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return result


async def write_in[T](target: ReferentielTarget, intent: str, fn: Any) -> tuple[T, str | None]:
    try:
        result: tuple[T, str | None] = await target.write(intent, fn)
    except ReferentielError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return result


def _detail(ref: Referentiel, unpublished: bool, readonly: bool) -> dict[str, object]:
    return {
        "name": ref.name,
        "title": str(ref.model.get("title") or ref.name),
        "model": ref.model,
        "model_yaml": dump_model(ref.model),
        "model_path": model_path(ref.name),
        "count": len(ref.values),
        "unpublished": unpublished,
        "readonly": readonly,
    }


def _starter_model(label: str) -> dict[str, Any]:
    """Un nouveau référentiel : une propriété `code`, qui en est la clé (un
    modèle valide demande au moins une propriété et une clé)."""
    return {
        "title": label,
        "type": "object",
        "x-key": ["code"],
        "required": ["code"],
        "properties": {"code": {"type": "string", "title": "Code"}},
    }


@router.get("")
async def list_referentiels(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    items: list[dict[str, Any]] = await read_in(target, published.summaries)
    touched = await _unpublished(conn, workdir, branch_id)
    return {
        "readonly": False,
        "referentiels": [{**item, "unpublished": item["name"] in touched} for item in items],
    }


@router.post("", status_code=201)
async def create_referentiel(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CreateBody,
) -> dict[str, object]:
    label, slug = body.label.strip(), body.slug.strip()
    fields = [
        f
        for f, bad in (("label_required", not label), ("slug_invalid", not is_valid_slug(slug)))
        if bad
    ]
    if fields:
        raise ApiError("invalid_request", status_code=400, fields=fields)
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel
    ref, _rev = await write_in(
        target,
        f"crée {slug}",
        lambda root: write_referentiel(root, slug, _starter_model(label), [], create=True),
    )
    return _detail(ref, True, False)


@router.get("/{name}")
async def get_referentiel(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> dict[str, object]:
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel = await read_in(target, lambda root: read_referentiel(root, name))
    touched = await _unpublished(conn, workdir, branch_id)
    return _detail(ref, name in touched, False)


@router.post("/{name}/modele/apercu")
async def preview_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ModelBody,
) -> dict[str, object]:
    """Ce que deviendraient les valeurs existantes avec ce modèle (rien n'est écrit)."""
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel = await read_in(target, lambda root: read_referentiel(root, name))
    return preview(body.model, ref.values).payload()


@router.put("/{name}/modele")
async def save_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ModelBody,
) -> dict[str, object]:
    """Enregistre le modèle dans le brouillon ; les propriétés retirées le sont
    aussi des valeurs, qui doivent rester valides (clé unique comprise)."""
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)

    def write(root: Path) -> Referentiel:
        current = read_referentiel(root, name)
        values, _removed = adapt_values(body.model, current.values)
        return write_referentiel(root, name, body.model, values, create=False)

    ref: Referentiel
    ref, revision = await write_in(target, f"modèle de {name}", write)
    return {**_detail(ref, True, False), "revision": revision}


@router.get("/{name}/valeurs")
async def get_values(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> dict[str, object]:
    """Les valeurs (vue de la personne), triées par clé."""
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel = await read_in(target, lambda root: read_referentiel(root, name))
    return {"values": sorted_values(ref.model, ref.values), "count": len(ref.values)}


@router.post("/{name}/valeurs/verification")
async def verify_values(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ValuesBody,
) -> dict[str, object]:
    """Les problèmes d'une liste de valeurs pour le modèle actuel, sans rien
    écrire : chacun désigne la valeur (`index`) et le champ (`field`)."""
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel = await read_in(target, lambda root: read_referentiel(root, name))
    problems = check_values(ref.model, body.values)
    return {"ok": not problems, "problems": [p.payload() for p in problems]}


@router.put("/{name}/valeurs")
async def save_values(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ValuesBody,
) -> dict[str, object]:
    """Enregistre la liste complète des valeurs dans le brouillon, triée par clé ;
    refus (« values_invalid ») si elle n'est pas valide pour le modèle."""
    target = await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)

    def write(root: Path) -> Referentiel:
        current = read_referentiel(root, name)
        return write_referentiel(root, name, current.model, body.values, create=False)

    ref: Referentiel
    ref, revision = await write_in(target, f"valeurs de {name}", write)
    return {"values": ref.values, "count": len(ref.values), "revision": revision}


@router.get("/{name}/historique")
async def referentiel_history(
    conn: ConnDep,
    crypto: CryptoDep,
    _: ReferentielsRead,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> dict[str, object]:
    """Les commits publiés qui ont touché le référentiel (un brouillon n'a pas
    d'histoire)."""
    try:
        rel = referentiel_dir(name)
    except ReferentielError as exc:
        raise api_error(exc) from None
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        commits = await manager.path_log(
            space.id, branch.git_ref, space.repo_url, username, token, rel, HISTORY_LIMIT
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {
        "commits": [
            {"sha": c.sha, "author": c.author, "date": c.date, "message": c.message}
            for c in commits
        ]
    }
