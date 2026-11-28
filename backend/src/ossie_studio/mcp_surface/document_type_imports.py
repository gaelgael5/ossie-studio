"""L'import écrit par le registre des types de document (epic « Primitives MCP — écriture commune
par type de document », ticket 1612cce4).

Gael : « il faut que le validateur appelle la même méthode ». La cible d'un import est
un type de document du registre (`carto_objet`, `referentiel_valeur`) : chaque ligne produite par
le template devient un document de ce type (identité + contenu), validé par la
méthode `check` du validateur du type de document — celle que `write_documents` appelle pour
chaque document —, et ses refus sont traduits par la même fonction (`translate`) :
même code, même message que par MCP. Ce qui reste propre à l'import (lecture du
fichier, JSLT, fusion ou remplacement, essai, tout ou rien, journal, compte rendu)
ne change pas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ossie_studio.cartographie.space import Cartographie
from ossie_studio.mcp_surface.document_type_base import CheckingStore, DocumentError, Location
from ossie_studio.mcp_surface.document_types import DocumentType, get_document_type
from ossie_studio.mcp_surface.write_errors import DOMAIN_ERRORS, translate


@dataclass(frozen=True)
class ImportRefusal:
    """Un refus d'une ligne : son rang dans la liste produite, le code et le message
    (ceux de MCP), et le JSONPath du champ en cause dans le document."""

    index: int
    code: str
    message: str
    path: str


def checking_store(doc_type: DocumentType) -> CheckingStore:
    store = doc_type.store
    if not isinstance(store, CheckingStore):
        raise TypeError(f"le type de document {doc_type.name} n'est pas une cible d'import")
    return store


def _location(doc_type: DocumentType, identity: Mapping[str, Any]) -> Location:
    """L'emplacement d'une ligne. Une ligne qui ne porte pas son identité (clé
    absente) est validée quand même : c'est son contenu qui dit ce qui manque."""
    if any(value is None or value == "" for value in identity.values()):
        return Location(doc_type.name, dict(identity), None)
    return doc_type.locate(None, identity)


def check_import(
    type_name: str,
    scope: Any,
    entries: Sequence[tuple[Mapping[str, Any], Any]],
    space: Cartographie | None = None,
) -> list[ImportRefusal]:
    """Les refus des lignes (identité, contenu) d'un import, par le validateur du
    type de document ; vide : toutes valides. Rien n'est écrit."""
    doc_type = get_document_type(type_name)
    store = checking_store(doc_type)
    refusals: list[tuple[int, Exception, Any]] = []
    located: list[tuple[Location, Any]] = []
    ranks: list[int] = []
    for rank, (identity, content) in enumerate(entries):
        try:
            located.append((_location(doc_type, identity), content))
            ranks.append(rank)
        except DocumentError as exc:
            refusals.append((rank, exc, content))
    for index, failure in store.check(scope, located):
        refusals.append((ranks[index], failure, located[index][1]))
    out = []
    for rank, problem, content in refusals:
        if not isinstance(problem, DOMAIN_ERRORS):
            raise problem
        for refusal in translate(
            problem,
            base="$",
            content_path="$",
            content=content,
            standard=doc_type.standard,
            space=space,
        ):
            out.append(ImportRefusal(rank, refusal.code, refusal.message, refusal.path))
    return sorted(out, key=lambda r: (r.index, r.path, r.code))
