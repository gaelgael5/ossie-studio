"""Profils de relations virtuelles d'un espace (`governance/relation-profiles/`
dans son dépôt) — ticket b5f333b4. Lecture : la branche vue ; écriture : le
brouillon. Le profil fourni par Ossie (préfixe `ossie-`) se modifie en
écrivant une version de l'espace sous le même identifiant ; la supprimer rétablit
l'original (ticket 1bce7eb9). Le profil fourni lui-même ne se supprime pas.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import GovernanceRead, GovernanceWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep, gov_ctx
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.governance.relation_profiles import (
    RelationProfile,
    builtin_profiles,
    delete_profile,
    is_builtin_id,
    parse_profile,
    profile_document,
    read_profile_files,
    write_profile,
)
from ossie_studio.governance.tag_rules import BUILTIN_PREFIX, is_valid_rule_id

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/spaces/{space_id}/branches/{branch_id}/governance/relation-profiles"
)


class RelationRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = ""
    table: str = ""
    target_column: str = ""
    kinds: list[str] = []


class RelationProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    description: str = ""
    rules: list[RelationRuleBody] = []


def parse_body(profile_id: str, body: RelationProfileBody) -> RelationProfile:
    data = body.model_dump()
    data["rules"] = [{k: v for k, v in r.items() if v not in ([], "")} for r in data["rules"]]
    profile, errors = parse_profile(profile_id, {"id": profile_id, **data})
    if profile is None:
        raise ApiError("relation_profile_invalid", status_code=400, fields=errors)
    return profile


def _payload(
    profile: RelationProfile | None, pid: str, rel: str, errors: tuple[str, ...]
) -> dict[str, object]:
    pid = profile.id if profile else pid
    return {
        "id": pid,
        "path": rel,
        "profile": profile_document(profile) if profile else None,
        "errors": list(errors),
        "builtin": is_builtin_id(pid),
        # Profil fourni remplacé par une version de l'espace.
        "customized": bool(rel) and is_builtin_id(pid),
    }


@router.get("")
async def list_relation_profiles(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    files = await ctx.read(read_profile_files)
    own: dict[str, dict[str, object] | None] = {}
    for f in files:
        stem = f.rel.rsplit("/", 1)[-1][:-5]
        own[stem] = _payload(f.profile, stem, f.rel, f.errors)
    # Un profil fourni n'apparaît qu'une fois : sa version de l'espace s'il en a une.
    provided = [own.pop(p.id, None) or _payload(p, p.id, "", ()) for p in builtin_profiles()]
    return {"profiles": provided + [p for p in own.values() if p is not None]}


@router.put("/{profile_id}")
async def save_relation_profile(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    profile_id: str,
    body: RelationProfileBody,
) -> dict[str, object]:
    profile = parse_body(profile_id, body)

    def write(root: Path) -> None:
        write_profile(root, profile)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"profil de relations {profile_id}", write)
    log.info("relation_profile_saved", profile_id=profile_id, rules=len(profile.rules))
    return _payload(profile, profile_id, f"governance/relation-profiles/{profile_id}.yaml", ())


@router.delete("/{profile_id}", status_code=204)
async def remove_relation_profile(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    profile_id: str,
) -> None:
    if not is_valid_rule_id(profile_id) or (
        profile_id.startswith(BUILTIN_PREFIX) and not is_builtin_id(profile_id)
    ):
        raise ApiError("relation_profile_not_found", status_code=404, profile_id=profile_id)

    def write(root: Path) -> None:
        if not delete_profile(root, profile_id):
            raise ApiError("relation_profile_not_found", status_code=404, profile_id=profile_id)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime le profil de relations {profile_id}", write)
