"""Processus BPMN de la cartographie : liste en vignettes, créer, importer un fichier
BPMN, rattacher à un processus métier, supprimer (epic « Cartographie — processus
BPMN », ticket 7e061c3e).

Un processus = un fichier BPMN gardé tel quel (voir `cartographie.process`), écrit dans
le brouillon de la personne, sur toute branche. Les dates viennent de l'historique Git
de la branche ; un processus du seul brouillon n'en a pas encore.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.branches import WorkdirDep
from ossie_studio.api.cartographie import api_error, cartographie_target, draft_states
from ossie_studio.api.cartographie_objects import carto_history, dates_payload
from ossie_studio.api.common import CartographieRead, CartographieWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.api.list_index import ListIndex
from ossie_studio.cartographie.errors import CartographieError, problem_payload
from ossie_studio.cartographie.model import CARTO_DIR
from ossie_studio.cartographie.process import (
    PROCESSES_DIR,
    Process,
    ProcessRead,
    delete_process,
    list_processes,
    new_process_xml,
    parse_process,
    preview,
    process_path,
    read_process,
    referenced_objects,
    save_process,
    with_detail,
    with_process_name,
)
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.models import User
from ossie_studio.slugs import slugify
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/cartographie/processus")

_PROCESSES_INDEX: ListIndex[list[dict[str, Any]]] = ListIndex()
# Un fichier BPMN d'atelier pèse quelques dizaines de Ko ; au-delà, c'est une erreur.
MAX_IMPORT_BYTES = 5_000_000


class ProcessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = ""
    name: str = ""
    detail_of: str | None = None  # clé du processus métier détaillé (facultatif)


class ImportBody(ProcessBody):
    content: str = ""  # le fichier BPMN, tel quel


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = ""
    filename: str = ""


class AttachBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detail_of: str | None = None


def _end(space: Cartographie, key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    found = space.objects.get(key)
    return {"key": key, "name": found[1].name if found else None, "exists": found is not None}


def _pools(process: Process, space: Cartographie) -> list[str]:
    """Les noms des participants (piscines) : celui de l'objet pour un participant
    rattaché, sinon celui du fichier."""
    by_element = {r.element: r for r in process.references}
    names = []
    for shape in process.shapes:
        if shape.kind != "participant":
            continue
        ref = by_element.get(shape.element)
        found = space.objects.get(ref.key) if ref else None
        names.append(found[1].name if found else shape.name)
    return [n for n in names if n]


def _summary(read: ProcessRead, space: Cartographie) -> dict[str, Any]:
    """Ce que montre la vignette d'un processus (sans dates ni état du brouillon)."""
    process = read.process
    base: dict[str, Any] = {
        "key": read.key,
        "path": read.path,
        "errors": len(read.problems),
    }
    if process is None:
        return {
            **base,
            "name": read.key,
            "readable": False,
            "detail_of": None,
            "participants": 0,
            "lanes": 0,
            "attached": 0,
            "pools": [],
            "preview": {"boxes": [], "links": []},
        }
    return {
        **base,
        "name": process.name or read.key,
        "readable": True,
        "detail_of": _end(space, process.detail_of),
        "participants": process.participants,
        "lanes": process.lanes,
        "attached": len(process.attached()),
        "pools": _pools(process, space),
        "preview": preview(process, space),
    }


def _read_all(root: Path) -> list[dict[str, Any]]:
    space = read_cartographie(root)
    return [_summary(read_process(root, key, space), space) for key in list_processes(root)]


def _decorate(
    item: dict[str, Any], dates: dict[str, Any], states: dict[str, str]
) -> dict[str, Any]:
    state = states.get(str(item["path"]))
    return {
        **item,
        "dates": dates_payload(dates.get(str(item["path"]))),
        "state": state if state in ("created", "modified") else None,
    }


@router.get("")
async def list_processes_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Les processus de l'espace (vue de la personne), avec leur aperçu, leurs dates
    de publication et leur état dans le brouillon."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        signature, _paths = await target.signature(f"{CARTO_DIR}/")
        key = (space_id, branch_id, workdir.user_id, workdir.published_only)
        items = _PROCESSES_INDEX.get(key, signature)
        if items is None:
            items = await target.read(_read_all)
            _PROCESSES_INDEX.put(key, signature, items)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, PROCESSES_DIR)
    states = await draft_states(conn, workdir, branch_id)
    return {"readonly": False, "processes": [_decorate(i, dates, states) for i in items]}


def _checked(content: str) -> str:
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise CartographieError("process_too_large", limit=MAX_IMPORT_BYTES)
    return content


@router.post("/verifier")
async def check_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CheckBody,
) -> dict[str, object]:
    """Les vérifications d'un fichier BPMN avant import (rien n'est écrit) : XML BPMN
    2.0 lisible, dessin présent, nombres, éléments rattachés, aperçu, nom et clé
    proposés."""
    stem = body.filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    try:
        process = parse_process(_checked(body.content))
    except CartographieError as exc:
        return {"valid": False, "error": {"code": exc.code, **exc.detail}}

    def read(root: Path) -> dict[str, Any]:
        return preview(process, read_cartographie(root))

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        shown = await target.read(read)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    name = process.name or stem
    return {
        "valid": True,
        "error": None,
        "has_diagram": process.has_diagram,
        "participants": process.participants,
        "lanes": process.lanes,
        "elements": process.elements,
        "flows": process.flows,
        "attached": len(process.references),
        "name": name,
        "suggested_key": f"PRC-{slugify(name or 'processus', fallback='processus').upper()}",
        "preview": shown,
    }


async def _write(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: User,
    intent: str,
    key: str,
    fn: Callable[[Path], object],
) -> dict[str, object]:
    """Écrit dans le brouillon, puis rend la vignette relue (dates, état)."""

    def write(root: Path) -> dict[str, Any]:
        fn(root)
        space = read_cartographie(root)
        return {**_summary(read_process(root, key, space), space)}

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        item, revision = await target.write(intent, write)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, PROCESSES_DIR)
    states = await draft_states(conn, workdir, branch_id)
    return {**_decorate(item, dates, states), "revision": revision}


def _detail_type(root: Path, key: str | None) -> str:
    """Le slug du type de l'objet détaillé, gardé dans l'extension (« processus-metier »)."""
    found = read_cartographie(root).objects.get(key or "")
    return found[1].type if found else ""


@router.post("", status_code=201)
async def create_process(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ProcessBody,
) -> dict[str, object]:
    """Crée un processus : un fichier BPMN avec un événement de début, rattaché (ou
    non) au processus métier dont il est le détail."""
    key, name = body.key.strip(), body.name.strip()
    if not name:
        raise api_error(CartographieError("process_name_missing"))
    detail = (body.detail_of or "").strip() or None
    return await _write(
        conn, crypto, workdir, manager, space_id, branch_id, user,
        f"crée le processus {key}", key,
        lambda root: save_process(root, key, new_process_xml(key, name, detail), create=True),
    )  # fmt: skip


@router.post("/importer", status_code=201)
async def import_process(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ImportBody,
) -> dict[str, object]:
    """Importe un fichier BPMN produit par un autre outil : gardé tel quel (éléments,
    identifiants, dessin), seuls l'`id` du document (la clé), le nom (s'il est changé)
    et le rattachement au processus métier sont retouchés. Un fichier illisible ou sans
    dessin est refusé ; rien n'est écrit."""
    key, name = body.key.strip(), body.name.strip()
    detail = (body.detail_of or "").strip() or None

    def write(root: Path) -> None:
        text = _checked(body.content)
        parse_process(text)
        if name:
            text = with_process_name(text, name)
        if detail:
            text = with_detail(text, detail, _detail_type(root, detail))
        save_process(root, key, text, create=True)

    return await _write(
        conn, crypto, workdir, manager, space_id, branch_id, user,
        f"importe le processus {key}", key, write,
    )  # fmt: skip


@router.get("/{key}")
async def get_process(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
) -> dict[str, object]:
    """Un processus : ce que montre sa vignette, et les problèmes trouvés à la lecture."""

    def read(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        found = read_process(root, key, space)
        return {**_summary(found, space), "problems": [problem_payload(p) for p in found.problems]}

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        item = await target.read(read)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, PROCESSES_DIR)
    return _decorate(item, dates, await draft_states(conn, workdir, branch_id))


@router.get("/{key}/contenu")
async def process_content(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
) -> dict[str, object]:
    """Le fichier BPMN tel quel, et les objets qu'il référence avec leur nom à jour :
    ce que reçoit le composant à l'ouverture (`references`)."""

    def read(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        found = read_process(root, key, space)
        text = (root / found.path).read_text(encoding="utf-8")
        references = referenced_objects(found.process, space) if found.process else []
        return {"key": key, "content": text, "references": references}

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        result: dict[str, object] = await target.read(read)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return result


@router.post("/{key}/rattacher")
async def attach_process(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
    body: AttachBody,
) -> dict[str, object]:
    """Rattache le processus au processus métier dont il est le détail (vide : retire
    le rattachement). Seule la référence change dans le fichier."""
    detail = (body.detail_of or "").strip() or None

    def write(root: Path) -> None:
        read_process(root, key)  # le processus doit exister
        text = (root / process_path(key)).read_text(encoding="utf-8")
        text = with_detail(text, detail, _detail_type(root, detail))
        save_process(root, key, text, create=False)

    return await _write(
        conn, crypto, workdir, manager, space_id, branch_id, user,
        f"rattache le processus {key}", key, write,
    )  # fmt: skip


@router.delete("/{key}")
async def delete_process_route(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    key: str,
) -> dict[str, object]:
    """Supprime le fichier du processus dans le brouillon ; jamais les objets rattachés."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _none, revision = await target.write(
            f"supprime le processus {key}", lambda root: delete_process(root, key)
        )
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"deleted": key, "revision": revision}
