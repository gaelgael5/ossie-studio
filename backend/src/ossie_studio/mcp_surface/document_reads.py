"""Primitives MCP communes de lecture : `read_document` (epic « Primitives MCP —
écriture commune par type de document », ticket 69be2ef9) et `list_documents` (ticket 2113e7bc).

Un document se lit comme il s'écrit : par son **type de document** et son **identité** ; le
registre (`document_types.py`) en déduit l'emplacement et désigne le service qui le lit. La
réponse rend le contenu brut (objet pour le YAML/JSON, texte XML pour un processus
BPMN — aucune primitive propre au BPMN), le fichier réel et la **version** à donner
telle quelle à `write_documents` / `patch_document`. Pour une valeur de référentiel,
la seule valeur demandée, jamais tout `valeurs.json`.

- `branch` absent : la branche publiée ; `draft` : avec le brouillon de la personne
  (là où écrivent `write_documents` et `patch_document` — relire en `draft` avant de
  modifier ce qu'on vient d'écrire).
- Les référentiels se lisent sur la branche donnée, comme ils s'écrivent (écran et
  `write_documents`) : la version rendue est celle qu'attend `patch_document`.
- `list_documents` (Gael : « il faut aligner les API de lecture de la même manière »
  que l'écriture) : la liste d'un type de document, par son lister dans le registre
  (`document_type_lists.py`, le service de l'écran). L'identité sert de filtre partiel, la
  recherche texte ignore casse et accents, chaque type de document déclare ses propres filtres
  (un filtre non déclaré est refusé en le nommant) ; paginée. Chaque document porte
  son identité, son nom, son fichier, sa version et sa synthèse. Elle remplace les
  onze lectures par domaine (cartographie, modèles, référentiels, gouvernance).
- **Droits** : les deux primitives supposent `spaces:read` ; pour une connexion OAuth, la
  capacité de lecture du type de document (celle de l'écran) est vérifiée sur l'espace visé.
  Une clé d'API n'a pas de personne : son profil agent, qui lui donne
  `read_document`, suffit (comme `read_schema`).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from pydantic import Field

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.mcp_surface.branch import BranchArgs, BranchView, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.document_type_base import (
    Document,
    DocumentError,
    Item,
    ListFilter,
    ListQuery,
    Location,
)
from ossie_studio.mcp_surface.document_types import DocumentType, get_document_type
from ossie_studio.mcp_surface.write_errors import DOMAIN_ERRORS, DomainError, translate
from ossie_studio.profiles.service import load_capabilities


class ReadDocumentArgs(BranchArgs):
    document_type: str = Field(
        description="Document type of the document (list_document_types), e.g. 'carto_objet'."
    )
    identity: dict[str, Any] = Field(
        description=(
            "Identity fields the document type requires, e.g. {type: 'application', key: "
            "'APP-CAISSE'} "
            "for carto_objet, {referentiel: 'pays', key: 'FR'} for referentiel_valeur."
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


def document_error(exc: DomainError, standard: str = "") -> PrimitiveError:
    """Un refus du registre ou d'un service, pour une primitive de lecture : le code,
    le JSONPath de l'argument en cause, le message et ce qui l'accompagne."""
    [refusal, *_] = translate(exc, base="$", content_path="$", content=None, standard=standard)
    detail = {k: v for k, v in refusal.payload().items() if k != "code"}
    return PrimitiveError(refusal.code, **detail)


def resolve_document_type(name: str) -> DocumentType:
    try:
        return get_document_type(name)
    except DocumentError as exc:
        raise document_error(exc) from None


async def require_read(
    ctx: PrimitiveContext, space_id: Any, doc_type: DocumentType, primitive: str
) -> None:
    """La personne derrière une connexion OAuth doit pouvoir lire ce type de document à l'écran,
    sur l'espace visé ; une clé d'API n'a que son profil agent (déjà vérifié)."""
    if ctx.user is None:
        return
    capability = doc_type.read_capability
    scope = space_id if capability in SPACE_SCOPED else None
    if capability not in await load_capabilities(ctx.conn, ctx.user, scope):
        raise PrimitiveError(
            "primitive_forbidden",
            primitive=primitive,
            capability=capability,
            document_type=doc_type.name,
        )


async def read_document(ctx: PrimitiveContext, args: ReadDocumentArgs) -> dict[str, Any]:
    doc_type = resolve_document_type(args.document_type)
    await require_read(ctx, args.space_id, doc_type, "read_document")
    bv = await open_branch(ctx, args)

    def read(root: Path) -> tuple[Location, Document | None]:
        try:
            location = doc_type.locate(root, args.identity, args.path)
            return location, doc_type.store.read(root, location)
        except DOMAIN_ERRORS as exc:
            raise document_error(exc, doc_type.standard) from None

    location, found = await bv.read(read)
    if found is None:
        raise PrimitiveError(
            "document_not_found",
            document_type=doc_type.name,
            identity=location.identity,
            message=(
                f"Aucun document « {doc_type.label} » de cette identité dans la vue "
                f"{bv.view} (list_documents, ou branch='draft' pour un document du brouillon)."
            ),
            **({"expected_path": location.path} if location.path else {}),
        )
    return {
        "view": bv.view,
        "document_type": doc_type.name,
        "identity": location.identity,
        "path": found.path,
        "version": found.version,
        "unpublished": bv.unpublished(found.path),
        "content": found.content,
    }


# --- list_documents -----------------------------------------------------------------------

MAX_LISTED = 200


class ListDocumentsArgs(BranchArgs):
    document_type: str = Field(
        description="Document type of the documents (list_document_types), e.g. 'carto_objet'."
    )
    identity: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Identity fields of the document type as partial filters (exact value), e.g. "
            "{type: 'application'} for carto_objet, {referentiel: 'pays'} for "
            "referentiel_valeur."
        ),
    )
    search: str | None = Field(
        default=None,
        description=(
            "Part of the name or identity (case and accents ignored); for a reference-list "
            "value, of any of its simple properties."
        ),
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The document type's own filters, as list_document_types declares them "
            "('list_filters'), "
            "e.g. {source: 'APP-CAISSE'} or {nature: 'relation'} for carto_objet."
        ),
    )
    limit: int = Field(default=50, ge=1, le=MAX_LISTED, description="Maximum documents returned.")
    offset: int = Field(default=0, ge=0, description="Documents to skip (for the next page).")


def fold(text: str) -> str:
    """Sans casse ni accents : « Écrit » et « ecrit » se rejoignent."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _unknown_filter(where: str, name: str, expected: list[str]) -> PrimitiveError:
    return PrimitiveError(
        "list_filter_unknown",
        path=where,
        message=(
            f"« {name} » n'est pas un filtre de ce type de document ; filtres possibles : "
            f"{', '.join(expected) or 'aucun'}."
        ),
        received=name,
        expected=expected,
    )


def _check_filter(declared: ListFilter, value: Any) -> None:
    where = f"$.filters.{declared.name}"
    if declared.type == "object":
        ok = isinstance(value, dict)
    else:
        ok = isinstance(value, str) and bool(value.strip())
    if ok and declared.choices and value not in declared.choices:
        ok = False
    if not ok:
        expected = list(declared.choices) or declared.type
        raise PrimitiveError(
            "list_filter_invalid",
            path=where,
            message=f"Valeur invalide pour le filtre « {declared.name} ».",
            received=value,
            expected=expected,
        )


def _query(doc_type: DocumentType, args: ListDocumentsArgs) -> ListQuery:
    """Les filtres, contrôlés : champs d'identité du type de document, filtres qu'il déclare."""
    names = [f.name for f in doc_type.identity]
    for name in args.identity:
        if name not in names:
            raise _unknown_filter(f"$.identity.{name}", name, names)
    declared = {f.name: f for f in doc_type.list_filters}
    for name, value in args.filters.items():
        if name not in declared:
            raise _unknown_filter(f"$.filters.{name}", name, list(declared))
        _check_filter(declared[name], value)
    return ListQuery(args.identity, args.filters)


def _matches(item: Item, identity: dict[str, Any], needle: str) -> bool:
    if any(item.identity.get(k) != v for k, v in identity.items()):
        return False
    if not needle:
        return True
    shown = " ".join(str(v) for v in item.identity.values())
    return needle in fold(f"{item.name} {shown} {item.text}")


def _listed(doc_type: DocumentType, bv: BranchView, root: Path, item: Item) -> dict[str, Any]:
    version = item.version
    if version is None:
        file = root / item.path
        version = git_blob_sha(file.read_bytes()) if file.is_file() else None
    out: dict[str, Any] = {
        "document_type": doc_type.name,
        "identity": item.identity,
        "name": item.name,
        "path": item.path,
        "version": version,
    }
    if any(bv.unpublished(p) for p in (item.path, *item.related)):
        out["unpublished"] = True
    if item.changes_under is not None:
        out["unpublished_changes"] = bv.unpublished_under(item.changes_under)
    return {**out, **item.summary}


async def list_documents(ctx: PrimitiveContext, args: ListDocumentsArgs) -> dict[str, Any]:
    doc_type = resolve_document_type(args.document_type)
    query = _query(doc_type, args)
    await require_read(ctx, args.space_id, doc_type, "list_documents")
    bv = await open_branch(ctx, args)
    needle = fold(args.search.strip()) if args.search else ""

    def read(root: Path) -> dict[str, Any]:
        try:
            items = doc_type.lister(root, query)
        except DOMAIN_ERRORS as exc:
            raise document_error(exc, doc_type.standard) from None
        matching = [i for i in items if _matches(i, args.identity, needle)]
        page = matching[args.offset : args.offset + args.limit]
        return {
            "total": len(matching),
            "has_more": args.offset + len(page) < len(matching),
            "documents": [_listed(doc_type, bv, root, item) for item in page],
        }

    found = await bv.read(read)
    return {"view": bv.view, "document_type": doc_type.name, **found}
