"""Primitives d'écriture de la gouvernance : tags gouvernés, schémas de
classification et leurs niveaux, certifications.

Même service que l'écran (`governance/actions.py`), mêmes règles et mêmes refus
nommés ; toujours dans le brouillon de la personne (`writes.open_for_write`).

Modifications partielles : seuls les champs donnés changent. Un champ omis (ou
null) garde sa valeur ; une chaîne vide efface un texte ; `links` et
`properties` remplacent la table entière. L'écran, lui, remplace l'objet.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.governance import actions, repo
from ossie_studio.governance.actions import GovernanceRefused
from ossie_studio.governance.repo import InvalidGovernanceId
from ossie_studio.mcp_surface.branch import BranchArgs
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.writes import open_for_write, write_target
from ossie_studio.spaces.workdir import WorkdirError

Scalar = str | int | float | bool

# --- Arguments : tags ------------------------------------------------------------------------


class TagArgs(BranchArgs):
    tag_id: str = Field(
        description=(
            "Tag identifier: lowercase segments of letters, digits, '-' or '_', separated by "
            "'/' (hierarchy), e.g. 'gouvernance/pii'."
        )
    )


class CreateTagArgs(TagArgs):
    owner: str = Field(default="", description="Who answers for this tag.")
    description: str = ""
    links: dict[str, str] = Field(default_factory=dict, description="Named links: name -> URL.")
    properties: dict[str, Scalar] = Field(
        default_factory=dict, description="Free properties: key -> simple value."
    )


class UpdateTagArgs(TagArgs):
    owner: str | None = None
    description: str | None = None
    links: dict[str, str] | None = Field(
        default=None, description="Replaces all the links when given."
    )
    properties: dict[str, Scalar] | None = Field(
        default=None, description="Replaces all the properties when given."
    )


# --- Arguments : classifications et niveaux ----------------------------------------------------


class LevelValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Level name: the value stored on assets (no comma).")
    short_name: str = Field(description="Short name shown on badges (12 characters at most).")
    label: str = ""
    rank: int = 0
    description: str = ""
    criteria: str = Field(default="", description="When an asset gets this level.")
    color: str = ""
    icon: str = ""


class CreateClassificationArgs(BranchArgs):
    name: str = Field(description="Scheme name, e.g. 'Sensitivity'.")
    scheme_id: str = Field(
        default="",
        description="Scheme identifier; omit to derive it from the name. Fixed afterwards.",
    )
    description: str = ""
    badge_prefix: str = ""
    multi_value: bool = Field(default=False, description="An asset may carry several levels.")
    is_primary: bool = Field(
        default=False, description="The primary scheme (a scale); cannot be multi-value."
    )
    levels: list[LevelValues] = Field(default_factory=list, description="Levels, in order.")


class SchemeArgs(BranchArgs):
    scheme_id: str = Field(description="Scheme identifier (from list_classifications).")


class UpdateClassificationArgs(SchemeArgs):
    name: str | None = None
    description: str | None = None
    badge_prefix: str | None = None
    multi_value: bool | None = None
    is_primary: bool | None = None
    display_order: int | None = Field(default=None, description="Position among the schemes.")


class CreateLevelArgs(SchemeArgs, LevelValues):
    pass


class LevelArgs(SchemeArgs):
    level_id: str = Field(description="Level id (from list_classifications).")


class UpdateLevelArgs(LevelArgs):
    name: str | None = None
    short_name: str | None = None
    label: str | None = None
    rank: int | None = None
    description: str | None = None
    criteria: str | None = None
    color: str | None = None
    icon: str | None = None


class ReorderLevelsArgs(SchemeArgs):
    order: list[str] = Field(
        description="All the level ids of the scheme, each exactly once, in the new order."
    )


# --- Arguments : certifications ----------------------------------------------------------------


class CreateCertificationArgs(BranchArgs):
    name: str = Field(description="Certification name, e.g. 'Gold'.")
    tag: str = Field(
        description="Tag that links the certification to assets, e.g. 'certification/gold'."
    )
    rank: int = Field(default=1, description="Order, 1 first (highest).")
    slug: str = Field(
        default="", description="Stable short name; omit to derive it from the name. Fixed."
    )
    description: str = ""
    criteria: str = ""
    icon: str = ""
    restricted: bool = Field(
        default=False,
        description="Only people with the 'certify' capability may grant it.",
    )


class CertificationArgs(BranchArgs):
    certification_id: str = Field(description="Certification id (from list_certifications).")


class UpdateCertificationArgs(CertificationArgs):
    name: str | None = None
    tag: str | None = None
    rank: int | None = None
    description: str | None = None
    criteria: str | None = None
    icon: str | None = None
    restricted: bool | None = None


# --- Mécanique commune ----------------------------------------------------------------------

# Champs d'un nouveau schéma (son rang parmi les schémas est fixé par le service).
_NEW_SCHEME_FIELDS = ("name", "description", "badge_prefix", "multi_value", "is_primary")


def _changes(args: BaseModel, fields: Sequence[str]) -> dict[str, Any]:
    """Les seuls champs donnés (non null) : ce qui doit changer."""
    return {name: value for name in fields if (value := getattr(args, name)) is not None}


async def _apply[T](
    ctx: PrimitiveContext,
    args: BranchArgs,
    intent: str,
    fn: Callable[[Path], T],
    path_of: Callable[[T], str],
) -> tuple[T, dict[str, Any]]:
    """Exécute le geste dans le brouillon de la personne ; rend son résultat et
    ce qui a été écrit. Les refus du service deviennent des refus nommés."""
    bv, user = await open_for_write(ctx, args)
    target = write_target(ctx, bv, user.email)
    results: list[T] = []
    try:
        revision = await target.write(intent, lambda root: results.append(fn(root)))
    except GovernanceRefused as exc:
        raise PrimitiveError(exc.code, **exc.detail) from None
    except InvalidGovernanceId:
        raise PrimitiveError("not_found") from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    value = results[-1]
    written = {
        "view": "draft",
        "changed": revision is not None,
        "revision": revision,
        "changed_paths": [path_of(value)] if revision else [],
    }
    return value, written


# --- Tags ------------------------------------------------------------------------------------


async def create_tag(ctx: PrimitiveContext, args: CreateTagArgs) -> dict[str, Any]:
    values = _changes(args, actions.TAG_FIELDS)
    tag, written = await _apply(
        ctx,
        args,
        f"tag {args.tag_id.strip().lower()}",
        lambda root: actions.create_tag(root, args.tag_id, values),
        lambda t: repo.tag_path(t.id),
    )
    return {**written, "tag": asdict(tag)}


async def update_tag(ctx: PrimitiveContext, args: UpdateTagArgs) -> dict[str, Any]:
    changes = _changes(args, actions.TAG_FIELDS)
    tag, written = await _apply(
        ctx,
        args,
        f"tag {args.tag_id.strip().lower()}",
        lambda root: actions.update_tag(root, args.tag_id, changes),
        lambda t: repo.tag_path(t.id),
    )
    return {**written, "tag": asdict(tag)}


async def delete_tag(ctx: PrimitiveContext, args: TagArgs) -> dict[str, Any]:
    tag_id, written = await _apply(
        ctx,
        args,
        f"supprime le tag {args.tag_id.strip().lower()}",
        lambda root: actions.delete_tag(root, args.tag_id),
        repo.tag_path,
    )
    return {**written, "deleted": tag_id}


# --- Classifications et niveaux ------------------------------------------------------------------


async def create_classification(
    ctx: PrimitiveContext, args: CreateClassificationArgs
) -> dict[str, Any]:
    values = {**_changes(args, _NEW_SCHEME_FIELDS), "id": args.scheme_id}
    levels = [level.model_dump() for level in args.levels]
    scheme, written = await _apply(
        ctx,
        args,
        f"schéma {args.scheme_id.strip() or args.name.strip()}",
        lambda root: actions.create_scheme(root, values, levels),
        lambda s: repo.scheme_path(s.id),
    )
    return {**written, "classification": asdict(scheme)}


async def update_classification(
    ctx: PrimitiveContext, args: UpdateClassificationArgs
) -> dict[str, Any]:
    changes = _changes(args, actions.SCHEME_FIELDS)
    scheme, written = await _apply(
        ctx,
        args,
        f"schéma {args.scheme_id}",
        lambda root: actions.update_scheme(root, args.scheme_id, changes),
        lambda s: repo.scheme_path(s.id),
    )
    return {**written, "classification": asdict(scheme)}


async def delete_classification(ctx: PrimitiveContext, args: SchemeArgs) -> dict[str, Any]:
    _, written = await _apply(
        ctx,
        args,
        f"supprime le schéma {args.scheme_id}",
        lambda root: actions.delete_scheme(root, args.scheme_id),
        lambda _: repo.scheme_path(args.scheme_id),
    )
    return {**written, "deleted": args.scheme_id}


async def create_level(ctx: PrimitiveContext, args: CreateLevelArgs) -> dict[str, Any]:
    values = _changes(args, actions.LEVEL_FIELDS)
    level, written = await _apply(
        ctx,
        args,
        f"niveau du schéma {args.scheme_id}",
        lambda root: actions.add_level(root, args.scheme_id, values),
        lambda _: repo.scheme_path(args.scheme_id),
    )
    return {**written, "level": asdict(level)}


async def update_level(ctx: PrimitiveContext, args: UpdateLevelArgs) -> dict[str, Any]:
    changes = _changes(args, actions.LEVEL_FIELDS)
    level, written = await _apply(
        ctx,
        args,
        f"niveau du schéma {args.scheme_id}",
        lambda root: actions.update_level(root, args.scheme_id, args.level_id, changes),
        lambda _: repo.scheme_path(args.scheme_id),
    )
    return {**written, "level": asdict(level)}


async def delete_level(ctx: PrimitiveContext, args: LevelArgs) -> dict[str, Any]:
    _, written = await _apply(
        ctx,
        args,
        f"supprime un niveau du schéma {args.scheme_id}",
        lambda root: actions.delete_level(root, args.scheme_id, args.level_id),
        lambda _: repo.scheme_path(args.scheme_id),
    )
    return {**written, "deleted": args.level_id}


async def reorder_levels(ctx: PrimitiveContext, args: ReorderLevelsArgs) -> dict[str, Any]:
    scheme, written = await _apply(
        ctx,
        args,
        f"ordre des niveaux du schéma {args.scheme_id}",
        lambda root: actions.reorder_levels(root, args.scheme_id, args.order),
        lambda s: repo.scheme_path(s.id),
    )
    return {**written, "classification": asdict(scheme)}


# --- Certifications ------------------------------------------------------------------------------


async def create_certification(
    ctx: PrimitiveContext, args: CreateCertificationArgs
) -> dict[str, Any]:
    values = _changes(args, actions.CERT_FIELDS)
    cert, written = await _apply(
        ctx,
        args,
        f"certification {args.name.strip()}",
        lambda root: actions.create_certification(root, values, args.slug),
        lambda c: repo.cert_path(c.id),
    )
    return {**written, "certification": asdict(cert)}


async def update_certification(
    ctx: PrimitiveContext, args: UpdateCertificationArgs
) -> dict[str, Any]:
    changes = _changes(args, actions.CERT_FIELDS)
    cert, written = await _apply(
        ctx,
        args,
        f"certification {args.certification_id}",
        lambda root: actions.update_certification(root, args.certification_id, changes),
        lambda c: repo.cert_path(c.id),
    )
    return {**written, "certification": asdict(cert)}


async def delete_certification(ctx: PrimitiveContext, args: CertificationArgs) -> dict[str, Any]:
    _, written = await _apply(
        ctx,
        args,
        "supprime une certification",
        lambda root: actions.delete_certification(root, args.certification_id),
        lambda _: repo.cert_path(args.certification_id),
    )
    return {**written, "deleted": args.certification_id}
