"""Primitives MCP communes d'écriture : `write_documents` et `patch_document` (epic
« Primitives MCP — écriture commune par genre », ticket 10cc4ed6).

Une seule façon d'écrire dans un espace, quel que soit le domaine (Gael : « n'explosons
pas la liste des API ») : chaque document porte son **genre** et son **identité** ; le
registre (`kinds.py`) en déduit l'emplacement — l'agent n'a pas son mot à dire — et
désigne le validateur, c'est-à-dire **le service de l'écran**. Évolution des deux
primitives de la cartographie du ticket d4723be3, généralisées à tous les genres.

- `write_documents` : une liste de documents, appliquée **en tout ou rien**, en un seul
  enregistrement dans le brouillon de la personne (jamais publié). Document absent →
  création (sans version) ; présent → remplacement complet (version obligatoire) ;
  `delete: true` → suppression (version obligatoire). L'ordre est libre : une relation
  peut viser un objet créé plus loin dans la liste, une suppression accompagner celle
  des relations qui utilisent l'objet (les refusés sont rejoués tant qu'un passage en
  fait aboutir un). Un document identique à l'existant ne change rien (rejeu).
- `patch_document` : des opérations `set` / `add` / `remove` sur un document, par
  JSONPath (YAML/JSON) ou XPath (BPMN), selon la syntaxe que déclare son genre ; le
  document modifié est revalidé en entier par le service.

Règles communes : réservées à un agent connecté pour une personne (`person_only`),
dans son brouillon, `branch` absent ou `draft`. Chaque document exige la capacité de
son genre (celle de l'écran), sur l'espace visé. **Aucune règle de domaine n'est
écrite ici** : tout passe par les validateurs du registre. Au moindre refus, rien
n'est écrit et toutes les erreurs sont rendues (`write_refused`), chacune avec son
JSONPath dans la requête et, pour une erreur de forme, `aide`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.cartographie.space import read_cartographie
from ossie_studio.mcp_surface.branch import BranchArgs, BranchView, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError, agent_name
from ossie_studio.mcp_surface.doc_paths import Operation, PathError, apply_json, apply_xml
from ossie_studio.mcp_surface.kind_base import KindError, Location
from ossie_studio.mcp_surface.kinds import WriteKind, write_kind
from ossie_studio.mcp_surface.write_errors import (
    DOMAIN_ERRORS,
    Refusal,
    Refused,
    aide_standard,
    jsonpath,
    translate,
)
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.spaces.workdir import WorkdirError

MAX_DOCUMENTS = 500
MAX_OPERATIONS = 200

# --- Arguments --------------------------------------------------------------------------


class DocumentArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(description="Kind of the document (list_write_kinds), e.g. 'carto_objet'.")
    identity: dict[str, Any] = Field(
        description=(
            "Identity fields the kind requires, e.g. {type: 'application', key: 'APP-CAISSE'} "
            "for carto_objet. The system computes the document's location from them."
        )
    )
    path: str | None = Field(
        default=None,
        description=(
            "Never needed: the location is computed from kind and identity. Refused for a "
            "kind whose path is fixed (all kinds today)."
        ),
    )
    version: str | None = Field(
        default=None,
        description=(
            "Version of the existing document (read_document): required to replace or "
            "delete it, absent to create it."
        ),
    )
    content: Any = Field(
        default=None,
        description=(
            "The whole document as it is stored: an object (YAML/JSON) or, for a BPMN "
            "process, the XML text. Its format: describe_standard."
        ),
    )
    delete: bool = Field(default=False, description="True to delete the document (destructive).")


class WriteDocumentsArgs(BranchArgs):
    message: str = Field(
        min_length=1, max_length=200, description="What this change is, for the draft history."
    )
    documents: list[DocumentArg] = Field(
        min_length=1,
        max_length=MAX_DOCUMENTS,
        description="Documents to create, replace or delete, applied all together or not at all.",
    )


class OperationArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["set", "add", "remove"] = Field(
        description=(
            "set: replace an existing node; add: add an absent member, insert in an array, add "
            "an XML child element or absent attribute; remove: remove an existing node."
        )
    )
    path: str = Field(
        description=(
            "JSONPath (RFC 9535 singular query, e.g. '$.proprietes.volume_jour') or, for a "
            "BPMN process, XPath (e.g. \"//bpmn:task[@id='Task_1']/@name\"). Exactly one node."
        )
    )
    value: Any = Field(
        default=None,
        description="New value; for XPath: an XML fragment for an element, a text otherwise.",
    )


class PatchDocumentArgs(BranchArgs):
    kind: str = Field(description="Kind of the document (list_write_kinds).")
    identity: dict[str, Any] = Field(description="Identity fields the kind requires.")
    path: str | None = Field(default=None, description="Never needed (see write_documents).")
    version: str = Field(description="Version of the document, from read_document.")
    operations: list[OperationArg] = Field(min_length=1, max_length=MAX_OPERATIONS)
    message: str | None = Field(
        default=None, max_length=200, description="What this change is, for the draft history."
    )


# --- Droits -----------------------------------------------------------------------------


async def _capability_refusals(
    ctx: PrimitiveContext, space_id: Any, entries: list[tuple[str, str]]
) -> dict[str, Refusal]:
    """Les entrées (chemin dans la requête, genre) dont le genre est inconnu ou dont la
    personne n'a pas la capacité (celle de l'écran, sur l'espace visé)."""
    assert ctx.user is not None
    scoped = await load_capabilities(ctx.conn, ctx.user, space_id)
    unscoped = await load_capabilities(ctx.conn, ctx.user, None)
    out: dict[str, Refusal] = {}
    for base, name in entries:
        try:
            kind = write_kind(name)
        except KindError as exc:
            out[base] = translate(exc, base=base, content_path=base, content=None, standard="")[0]
            continue
        caps = scoped if kind.capability in SPACE_SCOPED else unscoped
        if kind.capability not in caps:
            out[base] = Refusal(
                jsonpath(base, "kind"),
                "primitive_forbidden",
                f"Écrire un document « {kind.label} » demande la capacité {kind.capability}, "
                "que la personne n'a pas sur cet espace.",
                extra={"capability": kind.capability},
            )
    return out


# --- Commun -----------------------------------------------------------------------------


async def _open(ctx: PrimitiveContext, args: BranchArgs) -> BranchView:
    if ctx.user is None:  # seconde barrière derrière le catalogue
        raise PrimitiveError("draft_requires_person")
    return await open_branch(ctx, args, write=True)


async def _commit(
    ctx: PrimitiveContext, bv: BranchView, message: str, fn: Callable[[Path], Any]
) -> tuple[Any, str | None]:
    assert ctx.user is not None
    results: list[Any] = []
    try:
        revision = await bv.workdir.commit_to_branch(
            bv.space.id,
            bv.branch.git_ref,
            bv.space.repo_url,
            bv.username,
            bv.token,
            ctx.user.email,
            ctx.user.email,
            f"{message} (via l'agent {agent_name(ctx)})",
            lambda root: results.append(fn(root)),
        )
    except Refused as exc:
        raise PrimitiveError(
            "write_refused",
            written=False,
            errors=[r.payload() for r in exc.refusals],
            warnings=exc.warnings,
        ) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return results[-1], revision


def _stale(where: str, given: str | None, current: str) -> Refusal:
    if not given:
        return Refusal(
            where,
            "write_exists",
            "Ce document existe déjà : donner sa version (read_document) pour le remplacer.",
            extra={"current_version": current},
        )
    return Refusal(
        where,
        "write_version_stale",
        f"La version {given} n'est plus celle du document ({current}) : il a changé depuis la "
        "lecture. Le relire avant de l'écrire.",
        expected=current,
        received=given,
        extra={"current_version": current},
    )


def _not_found(where: str, kind: WriteKind, location: Location, hint: str = "") -> Refusal:
    return Refusal(
        where,
        "write_not_found",
        f"Aucun document « {kind.label} » d'identité "
        f"{json.dumps(location.identity, ensure_ascii=False)}{hint}.",
    )


def _same_content(current: Any, wanted: Any) -> bool:
    return bool(current == wanted)


# --- write_documents --------------------------------------------------------------------


@dataclass
class _Step:
    index: int
    kind: WriteKind
    location: Location
    content: Any  # None : suppression
    action: Literal["created", "replaced", "deleted", "unchanged"]
    warnings: list[str]

    @property
    def base(self) -> str:
        return f"$.documents[{self.index}]"


class _StepRefused(Exception):
    def __init__(self, refusals: list[Refusal]) -> None:
        super().__init__("refused")
        self.refusals = refusals


def _identity_key(doc: DocumentArg) -> str:
    return json.dumps([doc.kind, doc.identity], sort_keys=True, default=str, ensure_ascii=False)


def _duplicates(args: WriteDocumentsArgs, skipped: set[int]) -> list[Refusal]:
    seen: dict[str, int] = {}
    out = []
    for i, doc in enumerate(args.documents):
        if i in skipped:
            continue
        key = _identity_key(doc)
        if key in seen:
            out.append(
                Refusal(
                    f"$.documents[{i}]",
                    "write_document_duplicate",
                    f"Ce document apparaît déjà en $.documents[{seen[key]}] ; un document ne "
                    "figure qu'une fois dans la liste.",
                )
            )
        else:
            seen[key] = i
    return out


def _play(root: Path, index: int, doc: DocumentArg) -> _Step:
    """Prépare et joue un document : emplacement, état actuel, version, puis le
    validateur. Lève _StepRefused (refus de forme ou de version) ou un refus de
    service (DOMAIN_ERRORS)."""
    base = f"$.documents[{index}]"
    kind = write_kind(doc.kind)
    location = kind.locate(root, doc.identity, doc.path)
    current = kind.store.read(root, location)
    refusals: list[Refusal] = []
    step = _decide(kind, location, current, doc, base, refusals)
    if step is None:
        raise _StepRefused(refusals)
    step.index = index
    if step.action == "deleted":
        kind.store.delete(root, location)
    elif step.action != "unchanged":
        step.warnings = kind.store.write(
            root, location, step.content, create=step.action == "created"
        )
    return step


def _decide(
    kind: WriteKind,
    location: Location,
    current: Any,
    doc: DocumentArg,
    base: str,
    refusals: list[Refusal],
) -> _Step | None:
    if doc.delete:
        if doc.content is not None:
            refusals.append(
                Refusal(
                    jsonpath(base, "content"),
                    "write_content_invalid",
                    "Une suppression ne porte pas de contenu : seulement l'identité et la version.",
                )
            )
        elif current is None:
            refusals.append(_not_found(jsonpath(base, "identity"), kind, location))
        elif doc.version != current.version:
            refusals.append(_stale(jsonpath(base, "version"), doc.version, current.version))
        else:
            return _Step(0, kind, location, None, "deleted", [])
        return None
    if doc.content is None:
        refusals.append(
            Refusal(
                jsonpath(base, "content"),
                "write_content_invalid",
                "Le contenu du document est obligatoire (ou delete: true pour le supprimer).",
                aide=aide_standard(kind.standard),
            )
        )
        return None
    if current is None:
        if doc.version:
            refusals.append(
                _not_found(
                    jsonpath(base, "version"),
                    kind,
                    location,
                    " : une version est donnée mais le document n'existe pas (sans version, il est"
                    " créé)",
                )
            )
            return None
        return _Step(0, kind, location, doc.content, "created", [])
    if _same_content(current.content, doc.content):
        return _Step(0, kind, location, doc.content, "unchanged", [])
    if doc.version != current.version:
        refusals.append(_stale(jsonpath(base, "version"), doc.version, current.version))
        return None
    return _Step(0, kind, location, doc.content, "replaced", [])


def _apply(root: Path, args: WriteDocumentsArgs, skipped: set[int]) -> list[_Step]:
    """Joue les documents ; rejoue les refusés tant qu'un passage en fait aboutir un
    (ordre libre dans la liste : une relation avant l'objet qu'elle vise, une valeur
    avant le modèle de son référentiel). Ce qui reste refusé est traduit, et rien
    n'est écrit."""
    pending = [i for i in range(len(args.documents)) if i not in skipped]
    done: dict[int, _Step] = {}
    failures: dict[int, Exception] = {}
    while pending:
        progressed = False
        still: list[int] = []
        for i in pending:
            try:
                done[i] = _play(root, i, args.documents[i])
            except (_StepRefused, *DOMAIN_ERRORS) as exc:
                failures[i] = exc
                still.append(i)
            else:
                failures.pop(i, None)
                progressed = True
        pending = still
        if not progressed:
            break
    if pending:
        space = read_cartographie(root)
        refusals: list[Refusal] = []
        for i in pending:
            failure = failures[i]
            if isinstance(failure, _StepRefused):
                refusals.extend(failure.refusals)
                continue
            doc = args.documents[i]
            base = f"$.documents[{i}]"
            refusals.extend(
                translate(
                    failure,  # type: ignore[arg-type]
                    base=base,
                    content_path=f"{base}.content",
                    content=doc.content,
                    standard=_standard(doc.kind),
                    space=space,
                )
            )
        raise Refused(refusals)
    return [done[i] for i in sorted(done)]


def _standard(kind_name: str) -> str:
    try:
        return write_kind(kind_name).standard
    except KindError:
        return ""


async def write_documents(ctx: PrimitiveContext, args: WriteDocumentsArgs) -> dict[str, Any]:
    entries = [(f"$.documents[{i}]", d.kind) for i, d in enumerate(args.documents)]
    forbidden = await _capability_refusals(ctx, args.space_id, entries)
    bv = await _open(ctx, args)
    skipped = {i for i, (base, _k) in enumerate(entries) if base in forbidden}

    def write(root: Path) -> dict[str, Any]:
        refusals = list(forbidden.values()) + _duplicates(args, skipped)
        if refusals:
            raise Refused(sorted(refusals, key=lambda r: _order(r.path)))
        steps = _apply(root, args, skipped)
        documents = []
        warnings = []
        for step in steps:
            item: dict[str, Any] = {
                "kind": step.kind.name,
                "identity": step.location.identity,
                "action": step.action,
            }
            if step.action == "deleted":
                item["path"] = step.location.path
            else:
                location = step.kind.locate(root, step.location.identity)
                found = step.kind.store.read(root, location)
                item["path"] = location.path
                item["version"] = found.version if found else None
            documents.append(item)
            warnings += [{"document": step.base, "message": w} for w in step.warnings]
        return {"documents": documents, "warnings": warnings}

    result, revision = await _commit(ctx, bv, args.message.strip(), write)
    changed = [d["path"] for d in result["documents"] if d["action"] != "unchanged"]
    return {
        "written": True,
        "revision": revision,
        "documents": result["documents"],
        "warnings": result["warnings"],
        "changed_paths": sorted({p for p in changed if p}) if revision else [],
    }


def _order(path: str) -> tuple[int, str]:
    """Les erreurs dans l'ordre de la liste."""
    if path.startswith("$.documents["):
        try:
            return int(path[len("$.documents[") : path.index("]")]), path
        except ValueError:
            pass
    return -1, path


# --- patch_document ---------------------------------------------------------------------


def _operation_refusals(kind: WriteKind, args: PatchDocumentArgs) -> list[Refusal]:
    """Forme des opérations, avant de les jouer : JSONPath lisible, champ figé."""
    from ossie_studio.mcp_surface.doc_paths import parse_jsonpath

    refusals = []
    for i, op in enumerate(args.operations):
        where = f"$.operations[{i}].path"
        if kind.syntax != "jsonpath":
            continue
        try:
            segments = parse_jsonpath(op.path)
        except PathError as exc:
            refusals.append(
                Refusal(
                    where, exc.code, exc.message, received=op.path, aide=aide_standard("jsonpath")
                )
            )
            continue
        if f"$.{segments[0]}" in kind.frozen:
            refusals.append(
                Refusal(
                    where,
                    "write_field_frozen",
                    f"{segments[0]} est figé pour un document « {kind.label} » : "
                    f"{', '.join(kind.frozen)} ne changent pas.",
                    received=op.path,
                )
            )
    return refusals


async def patch_document(ctx: PrimitiveContext, args: PatchDocumentArgs) -> dict[str, Any]:
    forbidden = await _capability_refusals(ctx, args.space_id, [("$", args.kind)])
    if forbidden:
        raise PrimitiveError(
            "write_refused",
            written=False,
            errors=[r.payload() for r in forbidden.values()],
            warnings=[],
        )
    kind = write_kind(args.kind)
    bv = await _open(ctx, args)
    operations = [Operation(o.op, o.path, o.value) for o in args.operations]

    def write(root: Path) -> dict[str, Any]:
        try:
            location = kind.locate(root, args.identity, args.path)
            current = kind.store.read(root, location)
        except DOMAIN_ERRORS as exc:
            raise Refused(
                translate(exc, base="$", content_path="$", content=None, standard=kind.standard)
            ) from None
        if current is None:
            raise Refused([_not_found("$.identity", kind, location)])
        if args.version != current.version:
            raise Refused([_stale("$.version", args.version, current.version)])
        refusals = _operation_refusals(kind, args)
        if refusals:
            raise Refused(refusals)
        try:
            changed = (
                apply_xml(current.content, operations)
                if kind.syntax == "xpath"
                else apply_json(current.content, operations)
            )
        except PathError as exc:
            standard = "xpath" if kind.syntax == "xpath" else "jsonpath"
            raise Refused(
                [
                    Refusal(
                        f"$.operations[{exc.index}].path" if exc.index >= 0 else "$",
                        exc.code,
                        exc.message,
                        received=args.operations[exc.index].path if exc.index >= 0 else None,
                        aide=aide_standard(standard),
                    )
                ]
            ) from None
        if _same_content(current.content, changed):
            return {
                "path": location.path,
                "version": current.version,
                "warnings": [],
                "changed": False,
            }
        try:
            warnings = kind.store.write(root, location, changed, create=False)
        except DOMAIN_ERRORS as exc:
            space = read_cartographie(root)
            raise Refused(
                translate(
                    exc,
                    base="$",
                    content_path="$",
                    content=changed,
                    standard=kind.standard,
                    space=space,
                )
            ) from None
        found = kind.store.read(root, kind.locate(root, args.identity))
        return {
            "path": location.path,
            "version": found.version if found else None,
            "warnings": [{"document": "$", "message": w} for w in warnings],
            "changed": True,
        }

    label = json.dumps(args.identity, ensure_ascii=False)
    message = (args.message or "").strip() or f"modifie {kind.label} {label}"
    result, revision = await _commit(ctx, bv, message, write)
    return {
        "written": True,
        "revision": revision,
        "document": {
            "kind": kind.name,
            "identity": args.identity,
            "path": result["path"],
            "version": result["version"],
        },
        "warnings": result["warnings"],
        "changed_paths": [result["path"]] if revision and result["changed"] else [],
    }
