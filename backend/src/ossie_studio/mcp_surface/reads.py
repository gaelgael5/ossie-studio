"""Primitives de lecture : ce que l'agent peut consulter pour aider la personne.

Une primitive ne vérifie pas les droits de l'appelant : le point de contrôle
(`authz.authorize`) l'a fait avant, espace compris. Elle respecte en revanche
les règles des espaces (espace, branche ou source introuvable, accès au dépôt
perdu…) et les signale par une `PrimitiveError` nommée.

Les lectures d'une branche passent par `branch.open_branch` (vue `draft` ou
`published`, marque `unpublished` de ce qui vient du brouillon).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ossie_studio.api.publish import publish_readiness
from ossie_studio.drafts.preparation import changed_upstream
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.governance.repo import (
    CERTIFICATIONS_DIR,
    CLASSIFICATIONS_DIR,
    TAGS_DIR,
    read_certs,
    read_schemes,
    read_tags,
)
from ossie_studio.mcp_surface.branch import (
    BranchArgs,
    BranchView,
    ObjectArgs,
    SourceArgs,
    ViewArgs,
    branch_of,
    open_branch,
    source_dir,
)
from ossie_studio.mcp_surface.core import NoArgs, PrimitiveContext, PrimitiveError
from ossie_studio.models import SpaceBranch, User
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.sources.asset_files import extend_name
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import (
    CurationObject,
    ReviewResult,
    build_review,
    get_object_detail,
    is_schema_file,
    list_curation,
    published_raw,
    read_enrichment,
    read_source_trees,
    read_tri_rules,
    read_tri_rules_raw,
)
from ossie_studio.sources.paths import ASSETS_DIR, is_valid_source_slug, source_rel_dir
from ossie_studio.sources.service import SourceEntry, read_sources_tree
from ossie_studio.spaces.branch_service import db_list_branches
from ossie_studio.spaces.service import db_list_spaces, list_visible_spaces
from ossie_studio.spaces.workdir import WorkdirError

# Au-delà, un schéma brut n'est pas rendu d'un bloc (réponse trop lourde pour un agent).
_RAW_SCHEMA_MAX_BYTES = 1_000_000


def person_of(ctx: PrimitiveContext) -> User:
    """Primitives propres au brouillon : il faut une personne (connexion OAuth)."""
    if ctx.user is None:
        raise PrimitiveError("draft_requires_person")
    return ctx.user


def person_workdir(ctx: PrimitiveContext, user: User, branch: SpaceBranch) -> DraftWorkdir:
    return DraftWorkdir(
        ctx.state.workdir_manager,
        ctx.state.draft_views,
        ctx.state.pool,
        user.id,
        branch.id,
        conn=ctx.conn,
    )


def _source_exists(root: Path, directory: str) -> bool:
    return (root / directory).is_dir()


# --- Se repérer -----------------------------------------------------------------------


async def list_spaces(ctx: PrimitiveContext, _args: NoArgs) -> dict[str, Any]:
    # Clé d'API : tous les espaces. Connexion OAuth : ceux que la personne voit.
    if ctx.user is None:
        spaces = await db_list_spaces(ctx.conn)
    else:
        spaces = await list_visible_spaces(ctx.conn, ctx.user)
    out: list[dict[str, Any]] = []
    for space in spaces:
        branches = await db_list_branches(ctx.conn, space.id)
        out.append(
            {
                "id": str(space.id),
                "name": space.name,
                "slug": space.slug,
                "branches": [
                    {"id": str(b.id), "name": b.git_ref, "kind": b.kind} for b in branches
                ],
            }
        )
    return {"spaces": out}


async def list_versions(ctx: PrimitiveContext, args: BranchArgs) -> dict[str, Any]:
    space, branch, username, token = await branch_of(ctx, args.space_id, args.branch_id)
    try:
        versions = await ctx.state.workdir_manager.list_versions(
            space.id, branch.git_ref, space.repo_url, username, token
        )
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {"versions": [asdict(v) for v in versions]}


# --- Le brouillon de la personne ------------------------------------------------------


async def get_my_draft(ctx: PrimitiveContext, args: BranchArgs) -> dict[str, Any]:
    user = person_of(ctx)
    space, branch, username, token = await branch_of(ctx, args.space_id, args.branch_id)
    draft = await db_get_draft(ctx.conn, user.id, branch.id)
    if draft is None or draft.space_id != space.id:
        return {"count": 0, "objects": []}
    objects = await db_list_objects(ctx.conn, draft.id)
    try:
        current = await person_workdir(ctx, user, branch).published_blobs(
            space.id, branch.git_ref, space.repo_url, username, token, [o.path for o in objects]
        )
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return {
        "count": len(objects),
        "revision": draft.revision,
        "objects": [
            {
                "path": o.path,
                "state": o.state,
                "changed_upstream": changed_upstream(o, current.get(o.path)),
            }
            for o in objects
        ],
    }


async def get_publish_readiness(ctx: PrimitiveContext, args: BranchArgs) -> dict[str, Any]:
    user = person_of(ctx)
    space, branch, _username, _token = await branch_of(ctx, args.space_id, args.branch_id)
    try:
        ready = await publish_readiness(
            ctx.state.pool,
            ctx.conn,
            ctx.state.crypto,
            person_workdir(ctx, user, branch),
            user,
            space.id,
            branch.id,
        )
    except ApiError as exc:
        raise PrimitiveError(exc.code) from None
    return {"ok": ready.ok, "reasons": ready.reasons, "conflicts": ready.conflicts}


# --- Sources et schémas bruts ---------------------------------------------------------


def _source_payload(entry: SourceEntry, bv: BranchView) -> dict[str, Any]:
    changes = (
        bv.unpublished_under(source_rel_dir(entry.slug)) if is_valid_source_slug(entry.slug) else 0
    )
    base: dict[str, Any] = {"slug": entry.slug, "unpublished_changes": changes}
    if entry.manifest is None:
        return {**base, "ok": False, "error": entry.error}
    manifest = entry.manifest
    return {
        **base,
        "ok": True,
        "label": manifest.label,
        "type": manifest.type,
        "connection": manifest.connection,
        "parameters": manifest.parameters,
    }


async def list_sources(ctx: PrimitiveContext, args: ViewArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args, args.view)
    entries = await bv.read(read_sources_tree)
    return {"view": bv.view, "sources": [_source_payload(e, bv) for e in entries]}


async def _curation_objects(
    bv: BranchView, slug: str, directory: str, *, with_rules: bool
) -> list[CurationObject]:
    def read(root: Path) -> list[CurationObject] | None:
        if not _source_exists(root, directory):
            return None
        rules = read_tri_rules(root, slug) if with_rules else ()
        return list_curation(root, slug, rules, True)

    objects = await bv.read(read)
    if objects is None:
        raise PrimitiveError("source_not_found", source=slug)
    return objects


async def list_raw_schemas(ctx: PrimitiveContext, args: SourceArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    bv = await open_branch(ctx, args, args.view)
    objects = await _curation_objects(bv, args.source, directory, with_rules=False)
    return {
        "view": bv.view,
        "source": args.source,
        "schemas": [
            {
                "path": o.path,
                "title": o.title,
                "kind": o.kind,
                "fields": o.fields,
                "retained": o.retained,
                "unpublished": bv.unpublished(f"{directory}/{RAW_DIR}/{o.path}"),
            }
            for o in objects
            if not o.orphan
        ],
    }


async def read_raw_schema(ctx: PrimitiveContext, args: ObjectArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    if not is_schema_file(args.path):
        raise PrimitiveError("schema_not_found", path=args.path)
    bv = await open_branch(ctx, args, args.view)

    def read(root: Path) -> tuple[str, Any]:
        if not _source_exists(root, directory):
            return "source_not_found", None
        file = root / directory / RAW_DIR / args.path
        if not file.is_file():
            return "schema_not_found", None
        if file.stat().st_size > _RAW_SCHEMA_MAX_BYTES:
            return "schema_too_large", None
        try:
            return "ok", load_schema(file.read_text(encoding="utf-8"))
        except (SchemaReadError, UnicodeDecodeError):
            return "schema_invalid", None

    outcome, schema = await bv.read(read)
    if outcome != "ok":
        raise PrimitiveError(outcome, source=args.source, path=args.path)
    return {
        "view": bv.view,
        "source": args.source,
        "path": args.path,
        "unpublished": bv.unpublished(f"{directory}/{RAW_DIR}/{args.path}"),
        "schema": schema,
    }


# --- Assets (curation) ----------------------------------------------------------------


def _asset_unpublished(bv: BranchView, directory: str, path: str) -> bool:
    """Un asset est non publié si son schéma **ou** son `.extend.yaml` (travail
    humain, ticket 7a284e55) l'est."""
    base = f"{directory}/{ASSETS_DIR}"
    return bv.unpublished(f"{base}/{path}") or bv.unpublished(f"{base}/{extend_name(path)}")


async def list_assets(ctx: PrimitiveContext, args: SourceArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    bv = await open_branch(ctx, args, args.view)
    objects = await _curation_objects(bv, args.source, directory, with_rules=True)
    return {
        "view": bv.view,
        "source": args.source,
        "assets": [
            {
                "path": o.path,
                "title": o.title,
                "kind": o.kind,
                "fields": o.fields,
                "retained": o.retained,
                "orphan": o.orphan,
                "excluded_by": o.excluded_by,
                "unpublished": _asset_unpublished(bv, directory, o.path),
            }
            for o in objects
            if o.retained != "none"
        ],
    }


def _enrichment(scope: Any) -> dict[str, Any]:
    out = {
        "business_name": scope.business_name,
        "description": scope.description,
        "concept": scope.concept,
        "tags": scope.tags,
    }
    # Lien déclaré vers une propriété de référentiel (ticket 8269c642).
    if scope.referentiel:
        out["referentiel"] = scope.referentiel
    return out


async def read_asset(ctx: PrimitiveContext, args: ObjectArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    if not is_schema_file(args.path):
        raise PrimitiveError("asset_not_retained", path=args.path)
    bv = await open_branch(ctx, args, args.view)

    def read(root: Path) -> Any:
        if not _source_exists(root, directory):
            return None
        rules = read_tri_rules(root, args.source)
        return (
            get_object_detail(root, args.source, args.path, rules),
            read_enrichment(root, args.source, args.path),
        )

    found = await bv.read(read)
    if found is None:
        raise PrimitiveError("source_not_found", source=args.source)
    detail, enrichment = found
    if enrichment is None:
        raise PrimitiveError("asset_not_retained", path=args.path)
    by_name = {f.name: f for f in enrichment.fields}
    if detail is not None:
        fields = [
            {
                "name": f.name,
                "type": f.type,
                "retained": f.retained,
                "ref": f.ref,
                "missing": f.missing,
                **(_enrichment(by_name[f.name]) if f.name in by_name else {}),
            }
            for f in detail.fields
        ]
    else:  # orphelin : le brut a disparu, l'asset reste
        fields = [{"name": f.name, "retained": True, **_enrichment(f)} for f in enrichment.fields]
    return {
        "view": bv.view,
        "source": args.source,
        "path": args.path,
        "title": enrichment.title,
        "orphan": detail is None,
        "unpublished": _asset_unpublished(bv, directory, args.path),
        "object": _enrichment(enrichment.object),
        "fields": fields,
    }


async def get_tri_rules(ctx: PrimitiveContext, args: SourceArgs) -> dict[str, Any]:
    directory = source_dir(args.source)
    bv = await open_branch(ctx, args, args.view)

    def read(root: Path) -> list[dict[str, Any]] | None:
        return read_tri_rules_raw(root, args.source) if _source_exists(root, directory) else None

    rules = await bv.read(read)
    if rules is None:
        raise PrimitiveError("source_not_found", source=args.source)
    return {"view": bv.view, "source": args.source, "rules": rules}


async def compute_review(bv: BranchView, slug: str) -> tuple[str, ReviewResult]:
    """(tête publiée, revue) d'une source, dans la vue lue. La base est la branche
    publiée : ce que mon brouillon ajoute, change ou laisse en écart avec mes
    assets (ticket docflow aad1d3c6, plus de point de reprise). Calcul partagé
    par la lecture `get_review` et l'écriture `apply_review` : l'agent applique
    ses décisions à exactement ce qu'il a lu."""
    directory = source_dir(slug)
    try:
        head_sha, published_texts, bundle = await bv.workdir.review_inputs(
            bv.space.id,
            bv.branch.git_ref,
            bv.space.repo_url,
            bv.username,
            bv.token,
            f"{directory}/{RAW_DIR}",
            lambda root: (read_source_trees(root, slug), read_tri_rules(root, slug)),
        )
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    (raw_view, assets), rules = bundle
    review = build_review(published_raw(raw_view, published_texts), raw_view, assets, rules)
    return head_sha, review


def review_payload(head_sha: str, review: ReviewResult) -> dict[str, Any]:
    # `base_sha` et `up_to_date` (point de reprise) ont disparu du contrat : la
    # base est toujours la tête publiée, `head_sha`.
    return {
        "head_sha": head_sha,
        "objects": [asdict(o) for o in review.objects],
    }


async def get_review(ctx: PrimitiveContext, args: SourceArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args, args.view)
    head_sha, review = await compute_review(bv, args.source)
    return {"view": bv.view, "source": args.source, **review_payload(head_sha, review)}


# --- Gouvernance ----------------------------------------------------------------------


async def list_tags(ctx: PrimitiveContext, args: ViewArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args, args.view)
    tags = await bv.read(read_tags)
    return {
        "view": bv.view,
        "unpublished_changes": bv.unpublished_under(TAGS_DIR),
        "tags": [asdict(t) for t in tags],
    }


async def list_classifications(ctx: PrimitiveContext, args: ViewArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args, args.view)
    schemes = await bv.read(read_schemes)
    return {
        "view": bv.view,
        "unpublished_changes": bv.unpublished_under(CLASSIFICATIONS_DIR),
        "schemes": [asdict(s) for s in schemes],
    }


async def list_certifications(ctx: PrimitiveContext, args: ViewArgs) -> dict[str, Any]:
    bv = await open_branch(ctx, args, args.view)
    certs = await bv.read(read_certs)
    return {
        "view": bv.view,
        "unpublished_changes": bv.unpublished_under(CERTIFICATIONS_DIR),
        "certifications": [asdict(c) for c in certs],
    }
