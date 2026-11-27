"""Domaines d'une branche (tickets docflow a7baaaa3 et 36d35a88).

Gael, 23/11/2026 : un **domaine** = un modèle sémantique Ossie ; on y regroupe
des assets retenus, « un asset donne un dataset ». Le domaine prolonge la
Curation : lire demande `curation:read`, modifier `curation:write`. Toutes les
écritures vont dans le brouillon de la personne, par la seule brique
`domains/documents.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.branches import space_credentials
from ossie_studio.api.common import CurationRead, CurationWrite
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.domains.derive import AddPlan, Link, apply_plan, plan_add, remove_dataset
from ossie_studio.domains.documents import (
    DATATYPES,
    Dataset,
    Domain,
    DomainError,
    DomainField,
    Reference,
    Relationship,
    delete_domain,
    is_valid_domain_name,
    list_domain_names,
    list_domains,
    read_domain,
    write_domain,
)
from ossie_studio.domains.links import field_link_views, link_manual_field, unlink_manual_field
from ossie_studio.domains.manual import DatasetInput, FieldInput, Impact, save_manual_dataset
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.models import GitSpace
from ossie_studio.sources.curation_assets import read_assets
from ossie_studio.sources.explorer import DEFAULT_MODE
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.explorer_service import ensure_index, reference_graph
from ossie_studio.sources.referentiel_links import LinkRefusedError, LinkTarget, read_targets
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/domains")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
WorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


class DomainCreate(BaseModel):
    """Libellé libre et slug (déduit du libellé à l'écran, corrigeable) : règle
    commune des écrans. Le slug nomme les fichiers et le modèle Ossie."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    label: str = ""
    description: str = ""


class RelationshipBody(BaseModel):
    """Relation Ossie 0.1.1 : `from_dataset` porte la clé étrangère (côté
    plusieurs), `to_dataset` est le côté un ; deux listes de champs de même
    longueur, dans le même ordre (clé composée possible)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    from_dataset: str
    to_dataset: str
    from_columns: list[str] = Field(min_length=1)
    to_columns: list[str] = Field(min_length=1)


class ReferenceBody(BaseModel):
    """Référence d'objet (`.extend.yaml`) : un champ de `from_dataset` qui pointe
    vers un objet de `to_dataset`."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    from_dataset: str
    field: str
    to_dataset: str
    kind: Literal["field", "list", "map"] = "field"
    cardinality: str = ""


class DomainEdit(BaseModel):
    """Libellé et description seulement (ticket 82fc14f6) : le slug nomme les
    fichiers et le modèle Ossie, il ne se modifie pas ici."""

    model_config = ConfigDict(extra="forbid")
    label: str = ""
    description: str = ""


class AssetRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    path: str


class AddBody(BaseModel):
    """Assets à regrouper. `with_references` : prendre aussi les assets retenus
    qu'ils référencent, récursivement. `links` : identifiants des liens
    acceptés (None : tous les liens proposés, montrés cochés)."""

    model_config = ConfigDict(extra="forbid")
    assets: list[AssetRefBody] = []
    with_references: bool = False
    links: list[str] | None = None
    # Domaine à créer s'il n'existe pas encore (avec cette description) : la
    # fenêtre « Regrouper dans un domaine » crée et remplit en un seul commit,
    # et l'aperçu ne crée rien.
    create: bool = False
    label: str = ""
    description: str = ""


class ManualFieldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    expression: str = ""
    datatype: str = ""
    description: str = ""
    label: str = ""
    previous: str = ""  # nom avant modification : un renommage, pas un remplacement


class ManualDatasetBody(BaseModel):
    """Dataset manuel (ticket 1c68a20a). `confirm` : appliquer même si des liens
    du domaine sont touchés ; sans lui, la réponse les liste et rien n'est écrit."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    consultable: bool = True
    source: str = ""
    description: str = ""
    fields: list[ManualFieldBody] = []
    primary_key: list[str] = []
    unique_keys: list[list[str]] = []
    confirm: bool = False

    def as_input(self) -> DatasetInput:
        return DatasetInput(
            name=self.name,
            consultable=self.consultable,
            source=self.source,
            description=self.description,
            fields=[FieldInput(**f.model_dump()) for f in self.fields],
            primary_key=self.primary_key,
            unique_keys=self.unique_keys,
        )


class FieldLinkBody(BaseModel):
    """Lier des champs d'un dataset manuel (ticket 80bb56af) : même forme que pour
    un asset (`{champ : propriété du référentiel}`, tout le groupe d'une clé
    composée en une fois ; `replace` : champ dont le groupe actuel est remplacé)."""

    model_config = ConfigDict(extra="forbid")
    referentiel: str = ""
    links: dict[str, str] = {}
    replace: str | None = None


class _Ctx:
    def __init__(
        self,
        workdir: DraftWorkdir,
        space: GitSpace,
        ref: str,
        username: str,
        token: str | None,
    ) -> None:
        self.workdir, self.space, self.ref = workdir, space, ref
        self.username, self.token = username, token

    async def read(self, fn: Any) -> Any:
        try:
            return await self.workdir.read_branch(
                self.space.id, self.ref, self.space.repo_url, self.username, self.token, fn
            )
        except WorkdirError as exc:
            raise _workdir_error(exc) from None

    async def write(self, author: str, message: str, fn: Any) -> str | None:
        try:
            return await self.workdir.commit_to_branch(
                self.space.id,
                self.ref,
                self.space.repo_url,
                self.username,
                self.token,
                author,
                author,
                message,
                fn,
            )
        except WorkdirError as exc:
            raise _workdir_error(exc) from None


def _workdir_error(exc: WorkdirError) -> ApiError:
    return ApiError(exc.code, status_code=404 if exc.code == "branch_not_found" else 502)


def _domain_error(exc: DomainError) -> ApiError:
    known = {
        "domain_name_invalid": 400,
        "dataset_name_taken": 409,
        "relationship_cross_document": 400,
        "relationship_columns_mismatch": 400,
        # Relations et références à la main (ticket 4e856094).
        "relationship_dataset_missing": 400,
        "relationship_column_unknown": 400,
        "relationship_name_taken": 409,
        "relationship_not_found": 404,
        "reference_dataset_missing": 400,
        "reference_name_taken": 409,
        "reference_not_found": 404,
        # Dataset manuel (ticket 1c68a20a).
        "dataset_name_invalid": 400,
        "dataset_source_required": 400,
        "field_name_required": 400,
        "field_name_taken": 400,
        "field_expression_required": 400,
        "field_datatype_invalid": 400,
        "key_field_missing": 400,
        "dataset_derived_readonly": 409,
        "dataset_not_found": 404,
    }
    code = exc.code if exc.code in known else "domain_invalid"
    return ApiError(code, status_code=known.get(exc.code, 400), detail=exc.detail)


class _NotFound(Exception):
    pass


class _Taken(Exception):
    pass


class _NeedsConfirm(Exception):
    def __init__(self, impacts: list[Impact]) -> None:
        super().__init__("confirm")
        self.impacts = impacts


class _NotRetained(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


async def _ctx(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[_Ctx, str]:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    return _Ctx(workdir, space, branch.git_ref, username, token), branch.git_ref


async def _graph(conn: ConnDep, ctx: _Ctx, branch_id: uuid.UUID) -> ReferenceGraph:
    try:
        key, signature = await ensure_index(
            conn, ctx.workdir, ctx.space, branch_id, ctx.ref, ctx.username, ctx.token
        )
    except WorkdirError as exc:
        raise _workdir_error(exc) from None
    return await reference_graph(conn, key, DEFAULT_MODE, signature)


def _dataset_payload(
    ds: Dataset, targets: Mapping[str, LinkTarget] | None = None
) -> dict[str, object]:
    """`targets` (référentiels de la vue) : donné, chaque champ lié porte l'état de
    son lien (tickets 65235f64, 80bb56af) ; sinon le lien brut seulement."""
    views = field_link_views(ds, targets) if targets is not None else {}

    def field_payload(f: DomainField) -> dict[str, object]:
        out: dict[str, object] = asdict(f)
        view = views.get(f.name)
        out["referentiel"] = {**asdict(view), "inherited": ds.derived} if view is not None else None
        return out

    return {
        "name": ds.name,
        "source": ds.source,
        "consultable": ds.consultable,
        "derived": ds.derived,
        "asset": asdict(ds.asset) if ds.asset else None,
        "description": ds.description,
        "fields": [field_payload(f) for f in ds.fields],
        "primary_key": ds.primary_key,
        "unique_keys": ds.unique_keys,
    }


def _summary(domain: Domain) -> dict[str, object]:
    return {
        "name": domain.name,
        "label": domain.label or domain.name,
        "description": domain.description,
        "consultable": sum(1 for d in domain.datasets if d.consultable),
        "virtual": sum(1 for d in domain.datasets if not d.consultable),
        # Appartenance des assets, pour la Curation (« dans les domaines … »).
        "assets": [asdict(d.asset) for d in domain.datasets if d.asset],
        "warnings": domain.warnings,
    }


def _detail(domain: Domain, targets: Mapping[str, LinkTarget] | None = None) -> dict[str, object]:
    return {
        **_summary(domain),
        "datasets": [_dataset_payload(d, targets) for d in domain.datasets],
        "relationships": [asdict(r) for r in domain.relationships],
        "references": [asdict(r) for r in domain.references],
        # Types proposés par le formulaire du dataset manuel : servis d'ici pour que
        # l'écran n'ait pas sa propre liste (ticket 9bfa21d7 : une liste unique).
        "datatypes": list(DATATYPES),
    }


def _plan_payload(plan: AddPlan) -> dict[str, object]:
    def link(item: Link) -> dict[str, object]:
        return asdict(item)

    return {
        "added": [_dataset_payload(d) for d in plan.added],
        "already": plan.already,
        "links": [link(lk) for lk in plan.links],
        "ignored": plan.ignored,
    }


def _read_or_raise(root: Path, name: str) -> Domain:
    domain = read_domain(root, name)  # DomainError : document invalide, à l'appelant
    if domain is None:
        raise _NotFound(name)
    return domain


@router.get("")
async def list_branch_domains(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    domains: list[Domain] = await ctx.read(list_domains)
    return {"domains": [_summary(d) for d in domains]}


@router.post("", status_code=201)
async def create_branch_domain(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: DomainCreate,
) -> dict[str, object]:
    name = body.name.strip()
    if not is_valid_domain_name(name):
        raise ApiError("domain_name_invalid", status_code=400, detail=name)
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    domain = Domain(name=name, label=body.label.strip(), description=body.description.strip())

    def write(root: Path) -> None:
        if name in list_domain_names(root):
            raise _Taken(name)
        write_domain(root, domain)

    try:
        await ctx.write(user.email, f"domaine {name} : créer", write)
    except _Taken:
        raise ApiError("domain_name_taken", status_code=409, name=name) from None
    log.info("domain_created", name=name, author=user.email)
    return _summary(domain)


@router.get("/{name}")
async def get_branch_domain(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> dict[str, object]:
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    try:
        domain, targets = await ctx.read(
            lambda root: (_read_or_raise(root, name), read_targets(root))
        )
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    return _detail(domain, targets)


@router.patch("/{name}")
async def edit_branch_domain(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: DomainEdit,
) -> dict[str, object]:
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    edited: list[Domain] = []

    def write(root: Path) -> None:
        domain = _read_or_raise(root, name)
        domain.label = body.label.strip()
        domain.description = body.description.strip()
        write_domain(root, domain)
        edited.append(domain)

    try:
        await ctx.write(user.email, f"domaine {name} : libellé et description", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    log.info("domain_edited", name=name, author=user.email)
    return _summary(edited[0])


@router.delete("/{name}", status_code=204)
async def delete_branch_domain(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> None:
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)

    def write(root: Path) -> None:
        if not delete_domain(root, name):
            raise _NotFound(name)

    try:
        await ctx.write(user.email, f"domaine {name} : supprimer", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    log.info("domain_deleted", name=name, author=user.email)


@router.delete("/{name}/datasets/{dataset}", status_code=204)
async def remove_domain_dataset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    dataset: str,
) -> None:
    """Retire un dataset du domaine (ses relations et références avec lui) ;
    l'asset reste retenu dans la Curation."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)

    def write(root: Path) -> None:
        domain = _read_or_raise(root, name)
        if not remove_dataset(domain, dataset):
            raise _NotFound(dataset)
        write_domain(root, domain)

    try:
        await ctx.write(user.email, f"domaine {name} : retirer {dataset}", write)
    except _NotFound:
        raise ApiError("domain_dataset_not_found", status_code=404, name=dataset) from None
    except DomainError as exc:
        raise _domain_error(exc) from None


def _impacts(impacts: list[Impact]) -> list[dict[str, str]]:
    return [asdict(i) for i in impacts]


async def _save_manual(
    ctx: _Ctx, user_email: str, name: str, body: ManualDatasetBody, current: str | None
) -> dict[str, object]:
    result: dict[str, object] = {}

    def write(root: Path) -> None:
        domain = _read_or_raise(root, name)
        dataset, impacts = save_manual_dataset(domain, body.as_input(), current)
        if impacts and not body.confirm:
            # Rien n'est écrit : l'écran montre ce qui sera touché (critère 7).
            raise _NeedsConfirm(impacts)
        write_domain(root, domain)
        result.update(dataset=_dataset_payload(dataset), impacts=_impacts(impacts))

    verb = "créer" if current is None else "modifier"
    try:
        await ctx.write(user_email, f"domaine {name} : {verb} le dataset manuel {body.name}", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except _NeedsConfirm as exc:
        raise ApiError(
            "dataset_change_impacts", status_code=409, impacts=_impacts(exc.impacts)
        ) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    log.info("domain_manual_dataset_saved", name=name, dataset=body.name, author=user_email)
    return result


@router.post("/{name}/datasets", status_code=201)
async def create_manual_dataset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ManualDatasetBody,
) -> dict[str, object]:
    """Crée un dataset à la main, sans lien avec raw (ticket 1c68a20a)."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    return await _save_manual(ctx, user.email, name, body, None)


@router.put("/{name}/datasets/{dataset}")
async def edit_manual_dataset(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    dataset: str,
    body: ManualDatasetBody,
) -> dict[str, object]:
    """Modifie un dataset manuel ; un dataset dérivé est refusé (lecture seule)."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    return await _save_manual(ctx, user.email, name, body, dataset)


def _keys(body: AddBody) -> list[tuple[str, str]]:
    return list(dict.fromkeys((a.source, a.path) for a in body.assets))


def _plan(root: Path, name: str, body: AddBody, graph: ReferenceGraph) -> tuple[Domain, AddPlan]:
    domain = read_domain(root, name)
    if domain is None:
        if not body.create:
            raise _NotFound(name)
        if not is_valid_domain_name(name):
            raise DomainError("domain_name_invalid", name)
        domain = Domain(name=name, label=body.label.strip(), description=body.description.strip())
    assets = read_assets(root)
    for key in _keys(body):
        if key not in assets:
            raise _NotRetained(key[1])
    return domain, plan_add(domain, _keys(body), assets, graph, body.with_references)


@router.post("/{name}/preview")
async def preview_domain_assets(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    _: CurationRead,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: AddBody,
) -> dict[str, object]:
    """Ce que l'ajout ferait (datasets, liens déduits, références ignorées),
    sans rien écrire : la fenêtre de confirmation l'affiche."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    graph = await _graph(conn, ctx, branch_id)
    try:
        _domain, plan = await ctx.read(lambda root: _plan(root, name, body, graph))
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except _NotRetained as exc:
        raise ApiError("asset_not_retained", status_code=404, path=exc.path) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    return _plan_payload(plan)


@router.post("/{name}/assets")
async def add_domain_assets(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: AddBody,
) -> dict[str, object]:
    """Regroupe des assets dans le domaine : un dataset par asset, et les liens
    acceptés. Un asset déjà présent n'est pas dupliqué."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    graph = await _graph(conn, ctx, branch_id)
    result: dict[str, object] = {}

    def write(root: Path) -> None:
        domain, plan = _plan(root, name, body, graph)
        apply_plan(domain, plan, set(body.links) if body.links is not None else None)
        write_domain(root, domain)
        result.update(_plan_payload(plan))

    try:
        commit = await ctx.write(user.email, f"domaine {name} : regrouper des assets", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except _NotRetained as exc:
        raise ApiError("asset_not_retained", status_code=404, path=exc.path) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    log.info("domain_assets_added", name=name, author=user.email, commit=commit)
    return {"commit": commit, **result}


async def _change_links(
    ctx: _Ctx, user_email: str, name: str, intent: str, change: Any
) -> list[str]:
    touched: list[str] = []

    def write(root: Path) -> None:
        domain = _read_or_raise(root, name)
        touched[:] = change(root, domain)
        write_domain(root, domain)

    try:
        await ctx.write(user_email, f"domaine {name} : {intent}", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except LinkRefusedError as exc:
        raise ApiError(exc.code, status_code=exc.status, **exc.detail) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    return touched


@router.put("/{name}/datasets/{dataset}/referentiel-link")
async def link_manual_dataset_field(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    dataset: str,
    body: FieldLinkBody,
) -> dict[str, object]:
    """Lier un champ d'un dataset manuel à une propriété de référentiel (ticket
    80bb56af), mêmes règles que dans la Curation ; refusé sur un dataset dérivé."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)

    def change(root: Path, domain: Domain) -> list[str]:
        target = read_targets(root).get(body.referentiel)
        if target is None:
            raise LinkRefusedError("referentiel_not_found", 404, name=body.referentiel)
        return link_manual_field(domain, dataset, target, body.links, body.replace)

    fields = await _change_links(
        ctx, user.email, name, f"lier {dataset} à {body.referentiel}", change
    )
    log.info("domain_field_link", name=name, dataset=dataset, fields=fields, author=user.email)
    return {"ok": True, "fields": fields}


@router.delete("/{name}/datasets/{dataset}/referentiel-link")
async def unlink_manual_dataset_field(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    dataset: str,
    field: str,
) -> dict[str, object]:
    """Délier un champ d'un dataset manuel : tout son groupe est délié avec lui."""
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    fields = await _change_links(
        ctx, user.email, name, f"délier {dataset}.{field}",
        lambda _root, domain: unlink_manual_field(domain, dataset, field),
    )  # fmt: skip
    log.info("domain_field_unlink", name=name, dataset=dataset, fields=fields, author=user.email)
    return {"ok": True, "fields": fields}


# --- Relations et références à la main (ticket 4e856094) -----------------------------------
# Gael (19/11) : les relations restent modifiables au niveau du modèle, y compris
# pour les datasets dérivés. La déduction automatique se fait au regroupement
# (ticket 36d35a88) ; ici, seulement ce que la personne demande. Supprimer une
# relation déduite la retire : elle n'est reproposée qu'au prochain regroupement.


async def _change_domain(
    conn: Any,
    crypto: Any,
    workdir: Any,
    user: Any,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    intent: str,
    change: Callable[[Domain], None],
) -> dict[str, object]:
    ctx, _ref = await _ctx(conn, crypto, workdir, space_id, branch_id)
    changed: list[Domain] = []

    def write(root: Path) -> None:
        domain = _read_or_raise(root, name)
        change(domain)
        write_domain(root, domain)  # contrôle complet (check_domain, schéma 0.1.1)
        changed.append(domain)

    try:
        await ctx.write(user.email, f"domaine {name} : {intent}", write)
    except _NotFound:
        raise ApiError("domain_not_found", status_code=404, name=name) from None
    except DomainError as exc:
        raise _domain_error(exc) from None
    log.info("domain_links_edited", name=name, intent=intent, author=user.email)
    return _detail(changed[0])


def _relationship(body: RelationshipBody) -> Relationship:
    return Relationship(
        name=body.name.strip(),
        from_dataset=body.from_dataset,
        to_dataset=body.to_dataset,
        from_columns=list(body.from_columns),
        to_columns=list(body.to_columns),
    )


def _index(links: list[Any], name: str, missing: str) -> int:
    found = next((i for i, link in enumerate(links) if link.name == name), None)
    if found is None:
        raise DomainError(missing, name)
    return found


@router.post("/{name}/relationships", status_code=201)
async def create_relationship(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: RelationshipBody,
) -> dict[str, object]:
    def change(domain: Domain) -> None:
        domain.relationships.append(_relationship(body))

    return await _change_domain(
        conn, crypto, workdir, user, space_id, branch_id, name,
        f"relation {body.name.strip()}", change,
    )  # fmt: skip


@router.put("/{name}/relationships/{relationship}")
async def save_relationship(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    relationship: str,
    body: RelationshipBody,
) -> dict[str, object]:
    """Modifier une relation (son nom compris)."""

    def change(domain: Domain) -> None:
        index = _index(domain.relationships, relationship, "relationship_not_found")
        domain.relationships[index] = _relationship(body)

    return await _change_domain(
        conn, crypto, workdir, user, space_id, branch_id, name,
        f"modifie la relation {relationship}", change,
    )  # fmt: skip


@router.delete("/{name}/relationships/{relationship}")
async def delete_relationship(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    relationship: str,
) -> dict[str, object]:
    def change(domain: Domain) -> None:
        domain.relationships.pop(
            _index(domain.relationships, relationship, "relationship_not_found")
        )

    return await _change_domain(
        conn, crypto, workdir, user, space_id, branch_id, name,
        f"supprime la relation {relationship}", change,
    )  # fmt: skip


@router.post("/{name}/references", status_code=201)
async def create_reference(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: ReferenceBody,
) -> dict[str, object]:
    """Recréer une référence d'objet (après l'avoir supprimée)."""

    def change(domain: Domain) -> None:
        domain.references.append(
            Reference(
                name=body.name.strip(),
                from_dataset=body.from_dataset,
                field=body.field,
                to_dataset=body.to_dataset,
                kind=body.kind,
                cardinality=body.cardinality,
            )
        )

    return await _change_domain(
        conn, crypto, workdir, user, space_id, branch_id, name,
        f"référence {body.name.strip()}", change,
    )  # fmt: skip


@router.delete("/{name}/references/{reference}")
async def delete_reference(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: WorkdirDep,
    user: CurationWrite,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    reference: str,
) -> dict[str, object]:
    def change(domain: Domain) -> None:
        domain.references.pop(_index(domain.references, reference, "reference_not_found"))

    return await _change_domain(
        conn, crypto, workdir, user, space_id, branch_id, name,
        f"supprime la référence {reference}", change,
    )  # fmt: skip
