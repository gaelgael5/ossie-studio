"""Préparer la publication d'un brouillon : fusion avec le publié, impacts,
journal de validation.

Service commun à l'écran (routes `api/drafts.py`), à la publication
(`api/publish.py`) et à l'agent (primitives MCP, `mcp_surface/preparation.py`) :
une seule règle métier. Les refus sont ceux de l'écran (`ApiError`, code
nommé et statut).

Fusion : pour chaque objet du brouillon dont la version publiée a bougé, fusion
à trois versions (base, publiée, la sienne). Ce qui se fusionne seul (`auto`,
`text`) est fusionné d'office ; le reste attend une décision de la personne
(choix par propriété, ou sort d'un objet supprimé d'un côté). Une fusion
s'écrit sous le verrou du brouillon.

Décisions (impact vérifié, avertissement accepté) : elles valent tant que les
objets concernés ne changent plus et partent dans le message du commit ; `via`
nomme l'agent qui les a prises pour la personne (None : la personne elle-même).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg
import yaml

from ossie_studio.api.branches import space_credentials
from ossie_studio.crypto import CryptoService
from ossie_studio.drafts.impacts import Impact, compute_impacts, fingerprint
from ossie_studio.drafts.merge import (
    MISSING,
    Conflict,
    ObjectMerge,
    is_structured,
    merge_object,
    parse,
    serialize,
    set_pointer,
)
from ossie_studio.drafts.store import (
    Draft,
    DraftObject,
    db_decisions,
    db_get_draft,
    db_list_objects,
    db_rebase_object,
    db_set_decision,
)
from ossie_studio.drafts.validation import LoggedFinding, run_rules, validation_log
from ossie_studio.drafts.workdir import DraftWorkdir, ObjectVersions
from ossie_studio.errors import ApiError
from ossie_studio.sources.draft_changes import is_collected_raw
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

# Fusions qui se font seules ; toute autre sorte attend une décision.
AUTO_KINDS = ("auto", "text")
DELETION_KINDS = ("deleted_upstream", "deleted_in_draft")


def changed_upstream(obj: DraftObject, current: str | None) -> bool:
    """La version publiée de l'objet a-t-elle bougé depuis le début du
    brouillon ? Comparaison **objet par objet** (empreinte de base contre blob
    actuel du même chemin), jamais la tête de branche."""
    return obj.base_blob != current


async def branch_slug(conn: asyncpg.Connection, space_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    return branch.git_ref


# --- Fusion ---------------------------------------------------------------------------


async def merge_context(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[Draft, list[DraftObject], dict[str, ObjectVersions]]:
    """Le brouillon, ses objets, et pour chacun ses trois versions."""
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None or draft.space_id != space_id:
        raise ApiError("draft_object_not_found", status_code=404)
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    objects = await db_list_objects(conn, draft.id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        versions = await workdir.object_versions(
            space_id, branch.git_ref, space.repo_url, username, token, objects
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return draft, objects, versions


def find_object(objects: list[DraftObject], path: str) -> DraftObject:
    for obj in objects:
        if obj.path == path:
            return obj
    raise ApiError("draft_object_not_found", status_code=404, path=path)


def _json(value: Any) -> Any:
    return None if value is MISSING else value


def conflicts_payload(merged: ObjectMerge) -> list[dict[str, Any]]:
    return [
        {
            "pointer": c.pointer,
            "base": _json(c.base),
            "theirs": _json(c.theirs),
            "mine": _json(c.mine),
        }
        for c in merged.conflicts
    ]


def merge_detail(obj: DraftObject, v: ObjectVersions) -> dict[str, Any]:
    """Les trois versions d'un objet du brouillon et la fusion proposée. Sans
    changement côté publié : lecture seule (correspondance des modifications)."""
    upstream = changed_upstream(obj, v.current_blob)
    collected = is_collected_raw(obj.path)
    mine = obj.content
    merged = merge_object(obj.path, v.base, v.theirs, mine) if upstream else None
    shown = merged or merge_object(obj.path, v.base, v.base, mine)
    proposed: Any = None
    if merged is not None and merged.result is not None:
        proposed = parse(obj.path, merged.result) if merged.structured else merged.result.decode()
    return {
        "path": obj.path,
        "state": obj.state,
        "changed_upstream": upstream,
        "readonly": not upstream or collected,
        # Objet raw : lecture seule même en conflit (ticket 173c6ed6).
        "collected": collected,
        "kind": merged.kind if merged else "none",
        "structured": shown.structured,
        "base": _json(shown.base),
        "theirs": _json(merged.theirs if merged else shown.base),
        "mine": _json(shown.mine),
        "theirs_absent": v.theirs is None,
        "mine_absent": mine is None,
        "proposed": _json(proposed),
        "conflicts": conflicts_payload(merged) if merged else [],
    }


@dataclass(frozen=True)
class PendingMerge:
    """Un objet changé côté publié : sa fusion, et si elle attend une décision."""

    obj: DraftObject
    merged: ObjectMerge

    @property
    def needs_decision(self) -> bool:
        return self.merged.kind not in AUTO_KINDS


def pending_merges(
    objects: list[DraftObject], versions: Mapping[str, ObjectVersions]
) -> list[PendingMerge]:
    """Les objets du brouillon dont la version publiée a bougé, avec leur fusion."""
    pending: list[PendingMerge] = []
    for obj in objects:
        v = versions[obj.path]
        if changed_upstream(obj, v.current_blob):
            pending.append(PendingMerge(obj, merge_object(obj.path, v.base, v.theirs, obj.content)))
    return pending


def _choice(choice: Any, conflict: Conflict) -> Any:
    if choice == "theirs":
        return conflict.theirs
    if choice == "mine":
        return conflict.mine
    if isinstance(choice, dict) and "value" in choice:
        return choice["value"]
    return None


def resolved_content(
    path: str,
    merged: ObjectMerge,
    resolutions: Mapping[str, Any],
    delete: bool | None,
    theirs: bytes | None,
) -> bytes | None:
    """Contenu final de l'objet après les choix (None = supprimé). Lève
    merge_unresolved si un conflit reste sans choix."""
    if merged.kind in DELETION_KINDS:
        if delete is None:
            raise ApiError("merge_unresolved", status_code=400, pointers=[""])
        if delete:
            return None
        return merged.result if merged.kind == "deleted_upstream" else theirs
    if merged.kind in ("text", "text_conflict"):
        choice = resolutions.get("")
        if choice == "theirs":
            return theirs
        if choice == "mine":
            mine = merged.mine if isinstance(merged.mine, str) else None
            if merged.kind == "text_conflict" or mine is None:
                return merged.result
            return mine.encode()
        if isinstance(choice, dict) and isinstance(choice.get("value"), str):
            text = str(choice["value"])
            _refuse_unreadable(path, text)
            return text.encode("utf-8")
        if merged.kind == "text":
            return merged.result  # fusion automatique gardée telle quelle
        raise ApiError("merge_unresolved", status_code=400, pointers=[""])
    if merged.kind in ("conflict", "auto") and merged.structured:
        missing = [c.pointer for c in merged.conflicts if c.pointer not in resolutions]
        if missing:
            raise ApiError("merge_unresolved", status_code=400, pointers=missing)
        if not resolutions:
            return merged.result
        document = parse(path, merged.result)
        conflicts = {c.pointer: c for c in merged.conflicts}
        # Fenêtre en trois colonnes (feature d727680a) : une flèche ou une retouche
        # peut porter sur n'importe quel bloc, pas seulement un conflit — reprendre
        # un bloc fusionné automatiquement, retirer un bloc ajouté d'un seul côté
        # (`{"remove": true}`). Le calcul de la fusion, lui, ne change pas.
        for pointer, choice in sorted(resolutions.items()):
            if isinstance(choice, dict) and choice.get("remove") is True:
                value: Any = MISSING
            elif pointer in conflicts:
                value = _choice(choice, conflicts[pointer])
            elif isinstance(choice, dict) and "value" in choice:
                value = choice["value"]
            else:
                raise ApiError("merge_choice_invalid", status_code=400, pointer=pointer)
            document = set_pointer(document, pointer, value)
        # Les choix s'écrivent sur le résultat proposé (lui-même écrit sur le
        # brouillon) : seules les lignes tranchées changent (14bb8eb8).
        return serialize(path, document, like=merged.result)
    return merged.result


def _refuse_unreadable(path: str, text: str) -> None:
    """Un résultat retouché à la main doit rester lisible (feature edaddc6d) :
    jamais de YAML ou de JSON cassé dans le brouillon."""
    if not is_structured(path):
        return
    try:
        parse(path, text.encode("utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        raise ApiError("merge_value_invalid", status_code=400, path=path) from exc


def _decision_pointers(merged: ObjectMerge) -> set[str]:
    """Les pointeurs sur lesquels une décision est attendue (« "" » = l'objet entier)."""
    if merged.kind == "conflict":
        return {c.pointer for c in merged.conflicts}
    if merged.kind == "text_conflict":
        return {""}
    return set()


def _same(path: str, content: bytes | None, theirs: bytes | None) -> bool:
    """Même contenu que le publié ? Par valeur pour un objet structuré (la mise
    en forme peut différer), octet par octet sinon."""
    if content is None or theirs is None:
        return content is theirs
    if content == theirs:
        return True
    if not is_structured(path):
        return False
    try:
        return bool(parse(path, content) == parse(path, theirs))
    except (ValueError, yaml.YAMLError):
        return False


async def rebase(
    conn: asyncpg.Connection,
    draft: Draft,
    path: str,
    current_blob: str | None,
    theirs: bytes | None,
    content: bytes | None,
) -> None:
    """La version réconciliée remplace celle du brouillon ; sa base devient la
    version publiée actuelle. Résultat identique au publié : l'objet sort du
    brouillon."""
    if _same(path, content, theirs):
        await db_rebase_object(conn, draft.id, path, None, None)
    else:
        await db_rebase_object(conn, draft.id, path, current_blob, content)


async def resolve_merge(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    path: str,
    resolutions: Mapping[str, Any],
    delete: bool | None,
    *,
    strict: bool = False,
) -> tuple[Draft, ObjectMerge]:
    """Enregistre la version réconciliée d'un objet, sous le verrou du
    brouillon : une écriture concurrente ne peut pas écraser la fusion.

    Mode strict (agent) : l'objet doit avoir changé côté publié
    (`merge_not_needed`) et chaque choix doit porter sur un conflit
    (`merge_pointer_not_found`)."""
    async with workdir.draft_lock(space_id, await branch_slug(conn, space_id, branch_id)):
        draft, objects, versions = await merge_context(conn, crypto, workdir, space_id, branch_id)
        obj = find_object(objects, path)
        v = versions[path]
        if strict and not changed_upstream(obj, v.current_blob):
            raise ApiError("merge_not_needed", status_code=409, path=path)
        if is_collected_raw(path):
            # La fenêtre est en lecture seule ; le serveur ne s'y fie pas.
            raise ApiError("merge_collected_readonly", status_code=409, path=path)
        merged = merge_object(path, v.base, v.theirs, obj.content)
        if strict:
            unknown = sorted(set(resolutions) - _decision_pointers(merged))
            if unknown:
                raise ApiError("merge_pointer_not_found", status_code=400, pointers=unknown)
        content = resolved_content(path, merged, resolutions, delete, v.theirs)
        await rebase(conn, draft, path, v.current_blob, v.theirs, content)
    return draft, merged


async def auto_merge(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, list[str]]:
    """Fusionne d'office tout objet changé côté publié sans conflit ; rend les
    chemins fusionnés et ceux qui demandent une décision. À appeler sous le
    verrou du brouillon (`auto_merge_locked` le prend)."""
    draft, objects, versions = await merge_context(conn, crypto, workdir, space_id, branch_id)
    merged_paths: list[str] = []
    conflicts: list[str] = []
    for pending in pending_merges(objects, versions):
        path = pending.obj.path
        if pending.needs_decision:
            conflicts.append(path)
            continue
        v = versions[path]
        await rebase(conn, draft, path, v.current_blob, v.theirs, pending.merged.result)
        merged_paths.append(path)
    return {"merged": merged_paths, "conflicts": conflicts}


async def auto_merge_locked(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> dict[str, list[str]]:
    async with workdir.draft_lock(space_id, await branch_slug(conn, space_id, branch_id)):
        return await auto_merge(conn, crypto, workdir, space_id, branch_id)


# --- Impacts ----------------------------------------------------------------------------


async def draft_impacts(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[Draft | None, list[DraftObject], list[Impact]]:
    """Impacts du brouillon de la personne (vide sans brouillon). La publication
    refuse tant qu'un impact n'est pas traité."""
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    if draft is None or draft.space_id != space_id:
        return None, [], []
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    objects = await db_list_objects(conn, draft.id)
    verified = await db_decisions(conn, draft.id, "impact_verified")
    space, username, token = await space_credentials(conn, crypto, space_id)
    try:
        impacts = await workdir.read_branch(
            space_id,
            branch.git_ref,
            space.repo_url,
            username,
            token,
            lambda view: compute_impacts(view, objects, verified),
        )
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    return draft, objects, impacts


async def verify_impact(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    path: str,
    *,
    via: str | None = None,
) -> None:
    """« Vérifié, rien à corriger » : la décision vaut tant que les objets lus ne
    changent plus ; elle partira dans le message du commit."""
    draft, objects, impacts = await draft_impacts(conn, crypto, workdir, space_id, branch_id)
    impact = next((i for i in impacts if i.path == path), None)
    if draft is None or impact is None:
        raise ApiError("impact_not_found", status_code=404, path=path)
    await db_set_decision(
        conn, draft.id, "impact_verified", path, fingerprint(impact.reads, objects), via=via
    )


# --- Journal de validation ----------------------------------------------------------------


async def draft_validation(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> tuple[Draft | None, list[LoggedFinding]]:
    """Journal de la branche vue par la personne. Sans brouillon : celui de la
    branche publiée (rien n'y est « lié au brouillon »)."""
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    draft = await db_get_draft(conn, workdir.user_id, branch_id)
    space, username, token = await space_credentials(conn, crypto, space_id)
    args = (space_id, branch.git_ref, space.repo_url, username, token, run_rules)
    try:
        published = await workdir.read_branch(*args, published=True)
        merged = await workdir.read_branch(*args) if draft is not None else published
    except WorkdirError as exc:
        raise ApiError(exc.code, status_code=502) from None
    accepted = await db_decisions(conn, draft.id, "warning_accepted") if draft else {}
    return draft, validation_log(merged, published, accepted)


async def accept_warning(
    conn: asyncpg.Connection,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    rule: str,
    path: str,
    message_code: str,
    *,
    via: str | None = None,
) -> None:
    """Accepter un avertissement, jamais une erreur
    (`validation_error_not_acceptable`) : vaut tant que l'objet ne change plus ;
    part dans le message du commit."""
    draft, log_entries = await draft_validation(conn, crypto, workdir, space_id, branch_id)
    key = f"{rule}:{path}:{message_code}"
    entry = next((e for e in log_entries if e.finding.key == key), None)
    if draft is None or entry is None:
        raise ApiError("warning_not_found", status_code=404, path=path)
    if entry.finding.level != "warning":
        raise ApiError("validation_error_not_acceptable", status_code=409, path=path)
    await db_set_decision(
        conn, draft.id, "warning_accepted", key, entry.finding.fingerprint, via=via
    )
