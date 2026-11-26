"""Modèles de la cartographie : liste en vignettes, créer, supprimer (epic
« Cartographie — modèles et vues », ticket 5a05c7bb) ; ouvrir dans le composant
`urban-archimate` : modèle hydraté, boîte à outils, délégué de pose (ticket f5421cc7).

Un modèle, c'est trois fichiers (voir `cartographie.diagram`) : le modèle et son
graphique, créés et supprimés ensemble, et un fichier de règles, partagé entre
modèles, jamais supprimé avec eux. Tout s'écrit dans le brouillon de la personne,
sur toute branche, comme les types et les objets. Les dates viennent de l'historique
Git de la branche ; un modèle du seul brouillon n'en a pas encore.
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
from ossie_studio.cartographie.diagram import (
    MODELS_DIR,
    RULES_SUFFIX,
    STANDARD_RULES,
    Diagram,
    DiagramRead,
    Graphic,
    View,
    delete_diagram,
    drop,
    graphic_path,
    hydrate,
    list_diagrams,
    list_rules,
    preview,
    read_diagram,
    rules_path,
    save_diagram,
    toolbox,
)
from ossie_studio.cartographie.errors import CartographieError, problem_payload
from ossie_studio.cartographie.history import FileDates
from ossie_studio.cartographie.model import CARTO_DIR
from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.slugs import slugify
from ossie_studio.spaces.workdir import WorkdirError

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/cartographie/modeles")

# Liste des modèles : lue une fois par empreinte de `cartographie/` (les aperçus
# portent les noms des objets).
_MODELS_INDEX: ListIndex[dict[str, Any]] = ListIndex()
FIRST_VIEW = "Vue principale"


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    slug: str = ""
    description: str = ""
    rules: str = STANDARD_RULES  # slug du fichier de règles
    view: str = ""  # nom de la première vue
    viewpoint: str = ""  # son point de vue ArchiMate (vide : aucun)


def _rules_name(path: str) -> str:
    """`cartographie/regles/standard.rules.yaml` → `standard`."""
    return path.rsplit("/", 1)[-1].removesuffix(RULES_SUFFIX)


def _summary(read: DiagramRead, space: Cartographie) -> dict[str, Any]:
    """Ce que montre la vignette d'un modèle (sans dates ni état du brouillon)."""
    diagram = read.diagram
    base: dict[str, Any] = {
        "slug": read.slug,
        "path": read.path,
        "graphic": graphic_path(read.slug),
        "errors": len(read.errors),
    }
    if diagram is None:  # document illisible : la vignette le dit
        return {
            **base,
            "id": "",
            "name": read.slug,
            "description": "",
            "rules": "",
            "rules_name": "",
            "views": [],
            "elements": 0,
            "relationships": 0,
            "keys": [],
            "unused_documents": 0,
            "preview": {"boxes": [], "links": []},
        }
    return {
        **base,
        "id": diagram.id,
        "name": diagram.name or diagram.id,
        "description": diagram.description,
        "rules": diagram.rules,
        "rules_name": _rules_name(diagram.rules),
        "views": [{"id": v.id, "name": v.name, "viewpoint": v.viewpoint} for v in diagram.views],
        "elements": len(diagram.elements),
        "relationships": len(diagram.relationships),
        "keys": [*diagram.elements, *diagram.relationships],
        "unused_documents": sum(1 for p in read.problems if p.code == "model_document_unused"),
        "preview": preview(diagram, read.graphic, space),
    }


def _read_models(root: Path) -> dict[str, Any]:
    space = read_cartographie(root)
    models = [_summary(read_diagram(root, slug, space), space) for slug in list_diagrams(root)]
    return {"models": models, "rules": list_rules(root)}


def _dates(item: dict[str, Any], dates: dict[str, FileDates]) -> dict[str, str] | None:
    """La création est celle du modèle ; la dernière modification, celle du modèle ou
    de son graphique (déplacer un nœud ne touche que le graphique)."""
    model, graphic = dates.get(str(item["path"])), dates.get(str(item["graphic"]))
    if model is None:
        return dates_payload(graphic)
    if graphic is not None and graphic.updated_at > model.updated_at:
        model = FileDates(
            model.created_at, model.created_by, graphic.updated_at, graphic.updated_by
        )
    return dates_payload(model)


def _state(item: dict[str, Any], states: dict[str, str]) -> str | None:
    """« created » si le modèle est créé dans le brouillon, « modified » si lui ou son
    graphique y est modifié."""
    model, graphic = states.get(str(item["path"])), states.get(str(item["graphic"]))
    if model == "created":
        return "created"
    return "modified" if "modified" in (model, graphic) or graphic == "created" else None


def _decorate(
    item: dict[str, Any], dates: dict[str, FileDates], states: dict[str, str]
) -> dict[str, Any]:
    return {**item, "dates": _dates(item, dates), "state": _state(item, states)}


@router.get("")
async def list_models(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    """Les modèles de l'espace (vue de la personne), avec leur aperçu, leurs dates de
    publication et leur état dans le brouillon ; et les fichiers de règles."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        signature, _paths = await target.signature(f"{CARTO_DIR}/")
        key = (space_id, branch_id, workdir.user_id, workdir.published_only)
        found = _MODELS_INDEX.get(key, signature)
        if found is None:
            found = await target.read(_read_models)
            _MODELS_INDEX.put(key, signature, found)
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, MODELS_DIR)
    states = await draft_states(conn, workdir, branch_id)
    return {
        "readonly": False,
        "models": [_decorate(item, dates, states) for item in found["models"]],
        "rules": found["rules"],
    }


def _first_view(name: str, viewpoint: str) -> View:
    """La première vue : son identifiant se déduit de son nom (« Vue principale » →
    `VUE-PRINCIPALE`)."""
    stem = slugify(name, fallback="principale").removeprefix("vue-")
    return View(f"VUE-{(stem if stem != 'vue' else 'principale').upper()}", name, viewpoint)


@router.post("", status_code=201)
async def create_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: ModelBody,
) -> dict[str, object]:
    """Crée un modèle et son graphique dans le brouillon, avec une première vue (son
    point de vue est facultatif). Un fichier de règles absent est créé vide : la
    règle par défaut suffit."""
    slug, name = body.slug.strip(), body.label.strip()
    if not name:
        raise api_error(CartographieError("model_name_missing"))
    diagram = Diagram(
        id=f"MOD-{slug.upper()}",
        rules=rules_path(body.rules.strip() or STANDARD_RULES),
        graphic=graphic_path(slug),
        name=name,
        description=body.description.strip(),
        views=[_first_view(body.view.strip() or FIRST_VIEW, body.viewpoint.strip())],
    )

    def write(root: Path) -> dict[str, Any]:
        save_diagram(root, slug, diagram, Graphic(), create=True)
        space = read_cartographie(root)
        return _summary(read_diagram(root, slug, space), space)

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        item, revision = await target.write(f"crée le modèle {slug}", write)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, MODELS_DIR)
    states = await draft_states(conn, workdir, branch_id)
    return {**_decorate(item, dates, states), "revision": revision}


@router.get("/{slug}")
async def get_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    manager: WorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Un modèle : ce que montre sa vignette, et les problèmes trouvés à la lecture."""

    def read(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        found = read_diagram(root, slug, space)
        problems = [problem_payload(p) for p in found.problems]
        return {**_summary(found, space), "problems": problems}

    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        item = await target.read(read)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    dates = await carto_history(conn, crypto, manager, space_id, branch_id, MODELS_DIR)
    return _decorate(item, dates, await draft_states(conn, workdir, branch_id))


@router.delete("/{slug}")
async def delete_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, object]:
    """Supprime le modèle et son graphique dans le brouillon ; jamais les objets, ni le
    fichier de règles."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        _none, revision = await target.write(
            f"supprime le modèle {slug}", lambda root: delete_diagram(root, slug)
        )
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return {"deleted": slug, "revision": revision}


@router.get("/{slug}/hydrate")
async def hydrated_model(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
) -> dict[str, Any]:
    """Le modèle hydraté que reçoit le composant : le document du modèle, chaque
    référence complétée par son contenu (règles avec le bloc `defaut`, graphique,
    schémas des types présents, objets). Un modèle incohérent est refusé
    (« carto_model_invalid », avec les problèmes)."""

    def read(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        return hydrate(read_diagram(root, slug, space), space)

    return await _read(conn, crypto, workdir, space_id, branch_id, user, read)


@router.get("/{slug}/outils")
async def model_toolbox(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    point_de_vue: str = "",
) -> dict[str, Any]:
    """La boîte à outils de la vue courante (délégué `toolbox`) : les objets et les
    relations existants de l'espace, filtrés par le point de vue de la vue."""

    def read(root: Path) -> dict[str, Any]:
        read_diagram(root, slug)  # le modèle doit exister
        return {"groupes": toolbox(read_cartographie(root), point_de_vue)}

    return await _read(conn, crypto, workdir, space_id, branch_id, user, read)


@router.get("/{slug}/pose/{key}")
async def model_drop(
    conn: ConnDep,
    crypto: CryptoDep,
    user: CartographieRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    slug: str,
    key: str,
) -> dict[str, Any]:
    """Le délégué de pose : l'objet posé, le schéma de son type et son bloc de règles."""

    def read(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        return drop(read_diagram(root, slug, space), space, key)

    return await _read(conn, crypto, workdir, space_id, branch_id, user, read)


async def _read(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: CartographieRead,
    fn: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Une lecture de la branche (vue de la personne), refus nommés compris."""
    target = await cartographie_target(conn, crypto, workdir, space_id, branch_id, user)
    try:
        result: dict[str, Any] = await target.read(fn)
    except CartographieError as exc:
        raise api_error(exc) from None
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return result
