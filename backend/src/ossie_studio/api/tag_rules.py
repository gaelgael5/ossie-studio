"""Règles de tags d'un espace (governance/tag-rules/ dans son dépôt).

Lecture : la branche vue par la personne ; écriture : son brouillon. Enregistrer
une règle **crée automatiquement** les tags gouvernés qu'elle cite et qui
n'existent pas encore — décision de Gael (cadrage du 22/11/2026) : on les ajoute
sans demander, dans le même commit de brouillon que la règle.
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
from ossie_studio.governance.repo import Tag, read_tags, write_tag
from ossie_studio.governance.tag_rules import (
    MATCH_ANY,
    TagRemoval,
    TagRule,
    delete_tag_rule,
    is_valid_rule_id,
    parse_rule,
    read_tag_removals,
    read_tag_rules,
    rule_document,
    write_tag_removals,
    write_tag_rule,
)
from ossie_studio.governance.tags import is_valid_tag_id, normalize_tag_id

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/governance/tag-rules")


class TagRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    description: str = ""
    enabled: bool = True
    files: list[str] = []
    match: str = MATCH_ANY
    when: dict[str, list[str]] = {}
    tags: list[str] = []
    inherit_tags: bool = False


def _payload(
    rel: str, rule: TagRule | None, errors: tuple[str, ...], rule_id: str
) -> dict[str, object]:
    return {
        "id": rule.id if rule else rule_id,
        "path": rel,
        "rule": rule_document(rule) if rule else None,
        "errors": list(errors),
    }


@router.get("")
async def list_tag_rules(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    files = await ctx.read(read_tag_rules)
    return {
        "rules": [_payload(f.rel, f.rule, f.errors, f.rel.rsplit("/", 1)[-1][:-5]) for f in files]
    }


def ensure_tags(root: Path, rule: TagRule) -> list[str]:
    """Crée les tags de la règle absents du vocabulaire ; rend ceux créés."""
    known = {t.id for t in read_tags(root)}
    created = [t for t in rule.tags if t not in known]
    for tag_id in created:
        write_tag(root, Tag(tag_id, description=f"Créé par la règle de tags « {rule.name} »."))
    return created


@router.put("/{rule_id}")
async def save_tag_rule(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    rule_id: str,
    body: TagRuleBody,
) -> dict[str, object]:
    """Crée ou remplace une règle (l'id est le nom du fichier)."""
    rule, errors = parse_rule(rule_id, {"id": rule_id, **body.model_dump()})
    if rule is None:
        raise ApiError("tag_rule_invalid", status_code=400, fields=errors)
    created: list[str] = []

    def write(root: Path) -> None:
        write_tag_rule(root, rule)
        created.extend(ensure_tags(root, rule))

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"règle de tags {rule_id}", write)
    log.info("tag_rule_saved", rule_id=rule_id, created_tags=created)
    return {
        **_payload(f"governance/tag-rules/{rule_id}.yaml", rule, (), rule_id),
        "created_tags": created,
    }


@router.delete("/{rule_id}", status_code=204)
async def remove_tag_rule(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    rule_id: str,
) -> None:
    if not is_valid_rule_id(rule_id):
        raise ApiError("tag_rule_not_found", status_code=404, rule_id=rule_id)

    def write(root: Path) -> None:
        if not delete_tag_rule(root, rule_id):
            raise ApiError("tag_rule_not_found", status_code=404, rule_id=rule_id)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime la règle de tags {rule_id}", write)


# --- Retraits ------------------------------------------------------------------------

removals_router = APIRouter(
    prefix="/api/spaces/{space_id}/branches/{branch_id}/governance/tag-removals"
)


class TagRemovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object: str  # « source/fichier » de l'objet raw
    tag: str
    title: str = ""


def _removal_key(body: TagRemovalBody) -> tuple[str, str]:
    tag = normalize_tag_id(body.tag)
    slug, _, file = body.object.partition("/")
    if not slug or not file or "/" in file or not is_valid_tag_id(tag):
        raise ApiError("invalid_request", status_code=400, fields=["tag_removal_invalid"])
    return body.object, tag


@removals_router.get("")
async def list_tag_removals(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    removals = await ctx.read(read_tag_removals)
    return {"removals": [{"object": r.object, "tag": r.tag, "title": r.title} for r in removals]}


@removals_router.post("")
async def remove_tag_from_object(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: TagRemovalBody,
) -> dict[str, object]:
    """Retire un tag posé par règle d'un objet (idempotent : un second retrait ne
    duplique rien)."""
    obj, tag = _removal_key(body)

    def write(root: Path) -> None:
        current = read_tag_removals(root)
        if any(r.object == obj and r.tag == tag for r in current):
            return
        write_tag_removals(root, [*current, TagRemoval(obj, tag, body.title.strip())])

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"retire le tag {tag} de {obj}", write)
    log.info("tag_removed", object=obj, tag=tag)
    return {"object": obj, "tag": tag}


@removals_router.post("/restore")
async def restore_tag_on_object(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: TagRemovalBody,
) -> dict[str, object]:
    """Annule un retrait (idempotent : remettre un tag non retiré ne fait rien)."""
    obj, tag = _removal_key(body)

    def write(root: Path) -> None:
        current = read_tag_removals(root)
        kept = [r for r in current if not (r.object == obj and r.tag == tag)]
        if len(kept) != len(current):
            write_tag_removals(root, kept)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"remet le tag {tag} sur {obj}", write)
    log.info("tag_restored", object=obj, tag=tag)
    return {"object": obj, "tag": tag}
