"""Primitive MCP commune de lecture d'un document : `read_document` (epic « Primitives
MCP — écriture commune par genre », ticket 69be2ef9).

Un document se lit comme il s'écrit : par son **genre** et son **identité** ; le
registre (`kinds.py`) en déduit l'emplacement et désigne le service qui le lit. La
réponse rend le contenu brut (objet pour le YAML/JSON, texte XML pour un processus
BPMN — aucune primitive propre au BPMN), le fichier réel et la **version** à donner
telle quelle à `write_documents` / `patch_document`. Pour une valeur de référentiel,
la seule valeur demandée, jamais tout `valeurs.json`.

- `branch` absent : la branche publiée ; `draft` : avec le brouillon de la personne
  (là où écrivent `write_documents` et `patch_document` — relire en `draft` avant de
  modifier ce qu'on vient d'écrire).
- Les référentiels se lisent sur la branche donnée, comme ils s'écrivent (écran et
  `write_documents`) : la version rendue est celle qu'attend `patch_document`.
- **Droits** : la primitive suppose `spaces:read` ; pour une connexion OAuth, la
  capacité de lecture du genre (celle de l'écran) est vérifiée sur l'espace visé.
  Une clé d'API n'a pas de personne : son profil agent, qui lui donne
  `read_document`, suffit (comme `read_schema`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.mcp_surface.branch import BranchArgs, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.kind_base import Document, KindError, Location
from ossie_studio.mcp_surface.kinds import WriteKind, write_kind
from ossie_studio.mcp_surface.write_errors import DOMAIN_ERRORS, DomainError, translate
from ossie_studio.profiles.service import load_capabilities


class ReadDocumentArgs(BranchArgs):
    kind: str = Field(description="Kind of the document (list_write_kinds), e.g. 'carto_objet'.")
    identity: dict[str, Any] = Field(
        description=(
            "Identity fields the kind requires, e.g. {type: 'application', key: 'APP-CAISSE'} "
            "for carto_objet, {referentiel: 'pays', key: 'FR'} for referentiel_valeur."
        )
    )
    path: str | None = Field(
        default=None,
        description=(
            "Never needed: the location is computed from kind and identity. Refused for a "
            "kind whose path is fixed (all kinds today)."
        ),
    )


def kind_error(exc: DomainError, standard: str = "") -> PrimitiveError:
    """Un refus du registre ou d'un service, pour une primitive de lecture : le code,
    le JSONPath de l'argument en cause, le message et ce qui l'accompagne."""
    [refusal, *_] = translate(exc, base="$", content_path="$", content=None, standard=standard)
    detail = {k: v for k, v in refusal.payload().items() if k != "code"}
    return PrimitiveError(refusal.code, **detail)


def resolve_kind(name: str) -> WriteKind:
    try:
        return write_kind(name)
    except KindError as exc:
        raise kind_error(exc) from None


async def require_read(
    ctx: PrimitiveContext, space_id: Any, kind: WriteKind, primitive: str
) -> None:
    """La personne derrière une connexion OAuth doit pouvoir lire ce genre à l'écran,
    sur l'espace visé ; une clé d'API n'a que son profil agent (déjà vérifié)."""
    if ctx.user is None:
        return
    capability = kind.read_capability
    scope = space_id if capability in SPACE_SCOPED else None
    if capability not in await load_capabilities(ctx.conn, ctx.user, scope):
        raise PrimitiveError(
            "primitive_forbidden", primitive=primitive, capability=capability, kind=kind.name
        )


async def read_document(ctx: PrimitiveContext, args: ReadDocumentArgs) -> dict[str, Any]:
    kind = resolve_kind(args.kind)
    await require_read(ctx, args.space_id, kind, "read_document")
    bv = await open_branch(ctx, args)

    def read(root: Path) -> tuple[Location, Document | None]:
        try:
            location = kind.locate(root, args.identity, args.path)
            return location, kind.store.read(root, location)
        except DOMAIN_ERRORS as exc:
            raise kind_error(exc, kind.standard) from None

    location, found = await bv.read(read)
    if found is None:
        raise PrimitiveError(
            "document_not_found",
            kind=kind.name,
            identity=location.identity,
            message=(
                f"Aucun document « {kind.label} » de cette identité dans la vue "
                f"{bv.view} (list_documents, ou branch='draft' pour un document du brouillon)."
            ),
            **({"expected_path": location.path} if location.path else {}),
        )
    return {
        "view": bv.view,
        "kind": kind.name,
        "identity": location.identity,
        "path": found.path,
        "version": found.version,
        "unpublished": bv.unpublished(found.path),
        "content": found.content,
    }
