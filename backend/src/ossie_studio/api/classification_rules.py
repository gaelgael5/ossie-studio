"""Règles de classification d'un espace (governance/classification-rules/ dans
son dépôt, ticket docflow cfc33d51). Lecture : la branche vue par la personne ;
écriture : son brouillon. Les champs classés (usage, aperçu) se lisent dans
l'index de l'explorateur raw (`api/raw_explorer.py`)."""

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
from ossie_studio.governance.classification_rules import (
    MATCH_ALL,
    ClassificationRule,
    delete_classification_rule,
    effect_errors,
    is_valid_rule_id,
    parse_rule,
    read_classification_rules,
    rule_document,
    write_classification_rule,
)
from ossie_studio.governance.repo import Scheme, read_schemes

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/spaces/{space_id}/branches/{branch_id}/governance/classification-rules"
)


class ClassificationRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    description: str = ""
    enabled: bool = True
    files: list[str] = []
    match: str = MATCH_ALL
    when: dict[str, list[str]] = {}
    scheme: str = ""
    values: list[str] = []


def parse_body(rule_id: str, body: ClassificationRuleBody) -> ClassificationRule:
    rule, errors = parse_rule(rule_id, {"id": rule_id, **body.model_dump()})
    if rule is None:
        raise ApiError("invalid_request", status_code=400, fields=errors)
    return rule


def _payload(
    rel: str, rule: ClassificationRule | None, errors: list[str], rule_id: str
) -> dict[str, object]:
    return {
        "id": rule.id if rule else rule_id,
        "path": rel,
        "rule": rule_document(rule) if rule else None,
        # Erreurs de lecture, ou effet qui ne correspond plus aux schémas : la
        # règle ne classe rien tant qu'elle n'est pas corrigée.
        "errors": errors,
    }


@router.get("")
async def list_classification_rules(
    conn: ConnDep,
    crypto: CryptoDep,
    _: GovernanceRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, object]:
    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    files, schemes = await ctx.read(
        lambda root: (read_classification_rules(root), read_schemes(root))
    )
    rows = []
    for f in files:
        errors = list(f.errors) or (effect_errors(f.rule, schemes) if f.rule else [])
        rows.append(_payload(f.rel, f.rule, errors, f.rel.rsplit("/", 1)[-1][:-5]))
    return {"rules": rows}


def _check_effect(rule: ClassificationRule, schemes: list[Scheme]) -> None:
    errors = effect_errors(rule, schemes)
    if errors:
        raise ApiError("invalid_request", status_code=400, fields=errors)


@router.put("/{rule_id}")
async def save_classification_rule(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    rule_id: str,
    body: ClassificationRuleBody,
) -> dict[str, object]:
    """Crée ou remplace une règle (l'id est le nom du fichier). Le schéma et la
    valeur doivent exister au moment de l'enregistrement."""
    rule = parse_body(rule_id, body)

    def write(root: Path) -> None:
        _check_effect(rule, read_schemes(root))
        write_classification_rule(root, rule)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"règle de classification {rule_id}", write)
    log.info("classification_rule_saved", rule_id=rule_id, scheme=rule.scheme)
    return _payload(f"governance/classification-rules/{rule_id}.yaml", rule, [], rule_id)


@router.delete("/{rule_id}", status_code=204)
async def remove_classification_rule(
    conn: ConnDep,
    crypto: CryptoDep,
    user: GovernanceWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    rule_id: str,
) -> None:
    if not is_valid_rule_id(rule_id):
        raise ApiError("classification_rule_not_found", status_code=404, rule_id=rule_id)

    def write(root: Path) -> None:
        if not delete_classification_rule(root, rule_id):
            raise ApiError("classification_rule_not_found", status_code=404, rule_id=rule_id)

    ctx = await gov_ctx(conn, crypto, workdir, space_id, branch_id)
    await ctx.write(user.email, f"supprime la règle de classification {rule_id}", write)
    log.info("classification_rule_deleted", rule_id=rule_id)
