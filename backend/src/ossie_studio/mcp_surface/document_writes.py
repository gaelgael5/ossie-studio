"""Primitives MCP communes d'écriture : `write_documents` et `patch_document` (epic
« Primitives MCP — écriture commune par type de document », ticket 10cc4ed6).

Une seule façon d'écrire dans un espace, quel que soit le domaine (Gael : « n'explosons
pas la liste des API ») : chaque document porte son **type de document** et son **identité** ; le
registre (`document_types.py`) en déduit l'emplacement — l'agent n'a pas son mot à dire — et
désigne le validateur, c'est-à-dire **le service de l'écran**. Évolution des deux
primitives de la cartographie du ticket d4723be3, généralisées à tous les types de document.

- `write_documents` : une liste de documents, appliquée **en tout ou rien**, en un seul
  enregistrement dans le brouillon de la personne (jamais publié). Document absent →
  création (sans version) ; présent → remplacement complet (version obligatoire) ;
  `delete: true` → suppression (version obligatoire). L'ordre est libre : une relation
  peut viser un objet créé plus loin dans la liste, une suppression accompagner celle
  des relations qui utilisent l'objet (les refusés sont rejoués tant qu'un passage en
  fait aboutir un). Un document identique à l'existant ne change rien (rejeu).
- `patch_document` : des opérations `set` / `add` / `remove` sur un document, par
  JSONPath (YAML/JSON) ou XPath (BPMN), selon la syntaxe que déclare son type de document ; le
  document modifié est revalidé en entier par le service.

Règles communes : réservées à un agent connecté pour une personne (`person_only`),
dans son brouillon, `branch` absent ou `draft`. Chaque document exige la capacité de
son type de document (celle de l'écran), sur l'espace visé. **Aucune règle de domaine n'est
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

from ossie_studio.app_settings.service import document_max_bytes
from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.cartographie.space import read_cartographie
from ossie_studio.mcp_surface.branch import BranchArgs, BranchView, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError, agent_name
from ossie_studio.mcp_surface.doc_paths import Operation, PathError, apply_json, apply_xml
from ossie_studio.mcp_surface.document_type_base import DocumentError, Location
from ossie_studio.mcp_surface.document_type_stores import read_only
from ossie_studio.mcp_surface.document_types import DocumentType, get_document_type
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
    document_type: str = Field(
        description="Document type of the document (list_document_types), e.g. 'carto_objet'."
    )
    identity: dict[str, Any] = Field(
        description=(
            "Identity fields the document type requires, e.g. {type: 'application', key: "
            "'APP-CAISSE'} "
            "for carto_objet. The system computes the document's location from them."
        )
    )
    path: str | None = Field(
        default=None,
        description=(
            "Never needed: the location is computed from the document type and identity. Refused "
            "for a "
            "document type whose path is fixed (all document types today)."
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
    document_type: str = Field(description="Document type of the document (list_document_types).")
    identity: dict[str, Any] = Field(description="Identity fields the document type requires.")
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
    """Les entrées (chemin dans la requête, type de document) dont le type est inconnu ou dont la
    personne n'a pas la capacité (celle de l'écran, sur l'espace visé)."""
    assert ctx.user is not None
    scoped = await load_capabilities(ctx.conn, ctx.user, space_id)
    unscoped = await load_capabilities(ctx.conn, ctx.user, None)
    out: dict[str, Refusal] = {}
    for base, name in entries:
        try:
            doc_type = get_document_type(name)
        except DocumentError as exc:
            out[base] = translate(exc, base=base, content_path=base, content=None, standard="")[0]
            continue
        if not doc_type.writable:
            # Type de document en lecture seule (ticket 56f3784a) : le refus nomme l'action qui le
            # produit.
            [refusal] = translate(
                read_only(doc_type.label, doc_type.produced_by),
                base=base,
                content_path=base,
                content=None,
                standard=doc_type.standard,
            )
            refusal.aide = aide_standard(doc_type.standard)
            out[base] = refusal
            continue
        caps = scoped if doc_type.capability in SPACE_SCOPED else unscoped
        if doc_type.capability not in caps:
            out[base] = Refusal(
                jsonpath(base, "document_type"),
                "document_type_forbidden",
                f"Écrire un document « {doc_type.label} » demande la capacité "
                f"{doc_type.capability}, "
                "que la personne n'a pas sur cet espace : tout le lot est refusé.",
                extra={"capability": doc_type.capability},
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


def _not_found(where: str, doc_type: DocumentType, location: Location, hint: str = "") -> Refusal:
    return Refusal(
        where,
        "write_not_found",
        f"Aucun document « {doc_type.label} » d'identité "
        f"{json.dumps(location.identity, ensure_ascii=False)}{hint}.",
    )


def _same_content(current: Any, wanted: Any) -> bool:
    return bool(current == wanted)


def _size(root: Path, doc_type: DocumentType, identity: dict[str, Any]) -> int:
    """La taille d'un document tel qu'il est écrit : son fichier, ou pour une valeur de
    référentiel la valeur seule (jamais le fichier des valeurs entier)."""
    location = doc_type.locate(root, identity)
    found = doc_type.store.read(root, location)
    if found is None:
        return 0
    if location.entry is not None:
        text = json.dumps(found.content, ensure_ascii=False, sort_keys=True, indent=2)
        return len(text.encode("utf-8"))
    return len((root / found.path).read_bytes())


def _too_large(where: str, doc_type: DocumentType, size: int, limit: int) -> Refusal:
    return Refusal(
        where,
        "write_document_too_large",
        f"Ce document « {doc_type.label} » fait {size} octets une fois écrit ; la limite est de "
        f"{limit} octets par document.",
        aide=aide_standard(doc_type.standard),
        extra={"size": size, "limit": limit},
    )


# --- write_documents --------------------------------------------------------------------


@dataclass
class _Step:
    index: int
    doc_type: DocumentType
    location: Location
    content: Any  # None : suppression
    action: Literal["created", "replaced", "deleted", "unchanged"]
    warnings: list[str]

    @property
    def base(self) -> str:
        return f"$.documents[{self.index}]"


class _StepRefused(Exception):
    def __init__(self, refusals: list[Refusal], *, final: bool = False) -> None:
        super().__init__("refused")
        self.refusals = refusals
        self.final = final  # rien à attendre d'un nouveau passage (document trop grand)


def _identity_key(doc: DocumentArg) -> str:
    return json.dumps(
        [doc.document_type, doc.identity], sort_keys=True, default=str, ensure_ascii=False
    )


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


def _play(root: Path, index: int, doc: DocumentArg, limit: int) -> _Step:
    """Prépare et joue un document : emplacement, état actuel, version, puis le
    validateur. Lève _StepRefused (refus de forme ou de version) ou un refus de
    service (DOMAIN_ERRORS)."""
    base = f"$.documents[{index}]"
    doc_type = get_document_type(doc.document_type)
    location = doc_type.locate(root, doc.identity, doc.path)
    current = doc_type.store.read(root, location)
    refusals: list[Refusal] = []
    step = _decide(doc_type, location, current, doc, base, refusals)
    if step is None:
        raise _StepRefused(refusals)
    step.index = index
    if step.action == "deleted":
        doc_type.store.delete(root, location)
    elif step.action != "unchanged":
        step.warnings = doc_type.store.write(
            root, location, step.content, create=step.action == "created"
        )
        size = _size(root, doc_type, location.identity)
        if size > limit:
            raise _StepRefused([_too_large(base, doc_type, size, limit)], final=True)
    return step


def _decide(
    doc_type: DocumentType,
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
            refusals.append(_not_found(jsonpath(base, "identity"), doc_type, location))
        elif doc.version != current.version:
            refusals.append(_stale(jsonpath(base, "version"), doc.version, current.version))
        else:
            return _Step(0, doc_type, location, None, "deleted", [])
        return None
    if doc.content is None:
        refusals.append(
            Refusal(
                jsonpath(base, "content"),
                "write_content_invalid",
                "Le contenu du document est obligatoire (ou delete: true pour le supprimer).",
                aide=aide_standard(doc_type.standard),
            )
        )
        return None
    if current is None:
        if doc.version:
            refusals.append(
                _not_found(
                    jsonpath(base, "version"),
                    doc_type,
                    location,
                    " : une version est donnée mais le document n'existe pas (sans version, il est"
                    " créé)",
                )
            )
            return None
        return _Step(0, doc_type, location, doc.content, "created", [])
    if _same_content(current.content, doc.content):
        return _Step(0, doc_type, location, doc.content, "unchanged", [])
    if doc.version != current.version:
        refusals.append(_stale(jsonpath(base, "version"), doc.version, current.version))
        return None
    return _Step(0, doc_type, location, doc.content, "replaced", [])


def _apply(root: Path, args: WriteDocumentsArgs, skipped: set[int], limit: int) -> list[_Step]:
    """Joue les documents ; rejoue les refusés tant qu'un passage en fait aboutir un
    (ordre libre dans la liste : une relation avant l'objet qu'elle vise, une valeur
    avant le modèle de son référentiel). Ce qui reste refusé est traduit, et rien
    n'est écrit."""
    pending = [i for i in range(len(args.documents)) if i not in skipped]
    done: dict[int, _Step] = {}
    failures: dict[int, Exception] = {}
    final: list[int] = []
    while pending:
        progressed = False
        still: list[int] = []
        for i in pending:
            try:
                done[i] = _play(root, i, args.documents[i], limit)
            except _StepRefused as exc:
                failures[i] = exc
                if exc.final:
                    final.append(i)
                else:
                    still.append(i)
            except DOMAIN_ERRORS as exc:
                failures[i] = exc
                still.append(i)
            else:
                failures.pop(i, None)
                progressed = True
        pending = still
        if not progressed:
            break
    if pending or final:
        space = read_cartographie(root)
        refusals: list[Refusal] = []
        for i in sorted([*pending, *final]):
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
                    standard=_standard(doc.document_type),
                    space=space,
                )
            )
        raise Refused(refusals)
    return [done[i] for i in sorted(done)]


def _standard(type_name: str) -> str:
    try:
        return get_document_type(type_name).standard
    except DocumentError:
        return ""


async def write_documents(ctx: PrimitiveContext, args: WriteDocumentsArgs) -> dict[str, Any]:
    entries = [(f"$.documents[{i}]", d.document_type) for i, d in enumerate(args.documents)]
    forbidden = await _capability_refusals(ctx, args.space_id, entries)
    limit = await document_max_bytes(ctx.conn, ctx.state.settings)
    bv = await _open(ctx, args)
    skipped = {i for i, (base, _k) in enumerate(entries) if base in forbidden}

    def write(root: Path) -> dict[str, Any]:
        refusals = list(forbidden.values()) + _duplicates(args, skipped)
        if refusals:
            raise Refused(sorted(refusals, key=lambda r: _order(r.path)))
        steps = _apply(root, args, skipped, limit)
        documents = []
        warnings = []
        for step in steps:
            item: dict[str, Any] = {
                "document_type": step.doc_type.name,
                "identity": step.location.identity,
                "action": step.action,
            }
            if step.action == "deleted":
                item["path"] = step.location.path
            else:
                location = step.doc_type.locate(root, step.location.identity)
                found = step.doc_type.store.read(root, location)
                # Le fichier où le document se trouve après l'écriture (un dataset passé
                # en non consultable change de fichier).
                item["path"] = found.path if found else location.path
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


def _operation_refusals(doc_type: DocumentType, args: PatchDocumentArgs) -> list[Refusal]:
    """Forme des opérations, avant de les jouer : JSONPath lisible, champ figé."""
    from ossie_studio.mcp_surface.doc_paths import parse_jsonpath

    refusals = []
    for i, op in enumerate(args.operations):
        where = f"$.operations[{i}].path"
        if doc_type.syntax != "jsonpath":
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
        top = f"$.{segments[0]}"
        if top in doc_type.identity_paths:
            refusals.append(
                Refusal(
                    where,
                    "write_identity_frozen",
                    f"{segments[0]} porte l'identité d'un document « {doc_type.label} » : une "
                    "identité ne change jamais. Pour en changer, créer le nouveau document et "
                    "supprimer l'ancien dans le même lot (write_documents).",
                    received=op.path,
                )
            )
        elif top in doc_type.frozen:
            refusals.append(
                Refusal(
                    where,
                    "write_field_frozen",
                    f"{segments[0]} est figé pour un document « {doc_type.label} » : "
                    f"{', '.join(doc_type.frozen)} ne changent pas.",
                    received=op.path,
                )
            )
    return refusals


async def patch_document(ctx: PrimitiveContext, args: PatchDocumentArgs) -> dict[str, Any]:
    forbidden = await _capability_refusals(ctx, args.space_id, [("$", args.document_type)])
    if forbidden:
        raise PrimitiveError(
            "write_refused",
            written=False,
            errors=[r.payload() for r in forbidden.values()],
            warnings=[],
        )
    doc_type = get_document_type(args.document_type)
    limit = await document_max_bytes(ctx.conn, ctx.state.settings)
    bv = await _open(ctx, args)
    operations = [Operation(o.op, o.path, o.value) for o in args.operations]

    def write(root: Path) -> dict[str, Any]:
        try:
            location = doc_type.locate(root, args.identity, args.path)
            current = doc_type.store.read(root, location)
        except DOMAIN_ERRORS as exc:
            raise Refused(
                translate(exc, base="$", content_path="$", content=None, standard=doc_type.standard)
            ) from None
        if current is None:
            raise Refused([_not_found("$.identity", doc_type, location)])
        if args.version != current.version:
            raise Refused([_stale("$.version", args.version, current.version)])
        refusals = _operation_refusals(doc_type, args)
        if refusals:
            raise Refused(refusals)
        try:
            changed = (
                apply_xml(current.content, operations)
                if doc_type.syntax == "xpath"
                else apply_json(current.content, operations)
            )
        except PathError as exc:
            standard = "xpath" if doc_type.syntax == "xpath" else "jsonpath"
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
            warnings = doc_type.store.write(root, location, changed, create=False)
        except DOMAIN_ERRORS as exc:
            space = read_cartographie(root)
            raise Refused(
                translate(
                    exc,
                    base="$",
                    content_path="$",
                    content=changed,
                    standard=doc_type.standard,
                    space=space,
                )
            ) from None
        size = _size(root, doc_type, args.identity)
        if size > limit:
            raise Refused([_too_large("$", doc_type, size, limit)])
        found = doc_type.store.read(root, doc_type.locate(root, args.identity))
        return {
            "path": found.path if found else location.path,
            "version": found.version if found else None,
            "warnings": [{"document": "$", "message": w} for w in warnings],
            "changed": True,
        }

    label = json.dumps(args.identity, ensure_ascii=False)
    message = (args.message or "").strip() or f"modifie {doc_type.label} {label}"
    result, revision = await _commit(ctx, bv, message, write)
    return {
        "written": True,
        "revision": revision,
        "document": {
            "document_type": doc_type.name,
            "identity": args.identity,
            "path": result["path"],
            "version": result["version"],
        },
        "warnings": result["warnings"],
        "changed_paths": [result["path"]] if revision and result["changed"] else [],
    }
