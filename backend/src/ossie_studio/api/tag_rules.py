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
    TagRule,
    delete_tag_rule,
    is_valid_rule_id,
    parse_rule,
    read_tag_rules,
    rule_document,
    write_tag_rule,
)

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
