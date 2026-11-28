"""Un type de cartographie, cible du module commun d'import (ticket 49952be0).

Gael : « Il faut la possibilité de pouvoir mettre un système d'intégration pour
alimenter les objets cartographiques. On reprend le même système que pour les
référentiels. » **Une ligne = un objet** du type : le template rend `cle`, `nom`,
`documentation` (facultative), `proprietes` — et, pour un type de relation,
`source` et `cible`. Le `type` est celui du type visé (pas à écrire dans le
template ; s'il y est, il doit être le même).

Décisions de Gael : comparaison par la **clé**, **fusion seulement** (un objet
absent est créé, un objet présent modifié — jamais sa clé, son type, ni la source
ou la cible d'une relation) ; **aucune suppression** (« On voit les suppressions
après. C'est tout un sujet qu'on traitera d'un bloc. ») ; écriture dans le
brouillon, tout ou rien, un seul enregistrement.

**Validation et écriture** (epic « Primitives MCP — écriture commune par genre »,
ticket 1612cce4) : la cible est le genre `carto_objet` du registre. Chaque ligne est
validée par la méthode du validateur de ce genre — celle de `write_documents`, qui
applique les règles du service (`space.check_batch`) — et écrite par lui ; ses refus
portent le même code et le même message que par MCP. Aucune validation n'est propre
à l'import.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.model import CartoObject, is_empty
from ossie_studio.cartographie.space import Cartographie
from ossie_studio.imports.target import ItemProblem
from ossie_studio.mcp_surface.kind_imports import check_import, checking_store
from ossie_studio.mcp_surface.kind_stores import CartoObjectStore
from ossie_studio.mcp_surface.kinds import write_kind

JOURNAL_TYPE = "carto_import"
KIND = "carto_objet"
IMPORTS_DIR = "cartographie/imports"
# Ce qu'une ligne rend (format du fichier d'un objet, sans le type).
FIELDS = ("cle", "type", "source", "cible", "nom", "documentation", "proprietes")


def imports_dir(slug: str) -> str:
    return f"{IMPORTS_DIR}/{slug}"


def _document(obj: CartoObject) -> dict[str, Any]:
    doc = obj.document()
    doc.pop("type", None)
    return doc


@dataclass(frozen=True)
class CartoImportTarget:
    space: Cartographie
    slug: str
    cat: Catalog

    def current(self) -> list[dict[str, Any]]:
        return [_document(o) for _p, o in self.space.objects_of(self.slug)]

    def normalize(self, item: Any) -> Any:
        """Forme canonique, celle qu'écrirait le fichier : sans documentation ni
        propriétés vides, pour qu'une ligne inchangée se compare égale."""
        if not isinstance(item, dict) or set(item) - set(FIELDS):
            return item
        obj = self._object(item)
        return item if obj is None else _document(obj)

    def _object(self, item: dict[str, Any]) -> CartoObject | None:
        texts = ("cle", "nom", "documentation", "source", "cible", "type")
        if any(item.get(k) is not None and not isinstance(item[k], str) for k in texts):
            return None
        properties = item.get("proprietes")
        if properties is not None and not isinstance(properties, dict):
            return None
        return CartoObject(
            key=str(item.get("cle") or "").strip(),
            type=self.slug,
            name=str(item.get("nom") or "").strip(),
            documentation=str(item.get("documentation") or ""),
            properties={k: v for k, v in (properties or {}).items() if not is_empty(v)},
            source=item.get("source"),
            target=item.get("cible"),
        )

    def _identity(self, item: Any) -> dict[str, Any]:
        key = item.get("cle") if isinstance(item, dict) else None
        return {"type": self.slug, "key": key}

    def check(self, produced: list[Any]) -> list[ItemProblem]:
        entries = [(self._identity(item), item) for item in produced]
        return [
            ItemProblem(r.index, r.code, r.message, r.path, self.label(produced[r.index], r.index))
            for r in check_import(KIND, self.space, entries, space=self.space)
        ]

    def key(self, item: Any) -> Hashable | None:
        key = item.get("cle") if isinstance(item, dict) else None
        return key if isinstance(key, str) and key else None

    def label(self, item: Any, index: int) -> str:
        key = self.key(item)
        return str(key) if key is not None else f"#{index + 1}"

    def write(self, root: Path, items: list[Any]) -> None:
        """Crée ou modifie, par le validateur du genre (celui de write_documents), les
        objets qui changent ; aucun n'est supprimé (fusion seulement). Le lot est
        écrit d'un bloc (ticket eed2dd0d) : la cartographie est lue une fois, et
        une relation qui vise un objet (ou une relation) du lot passe quel que soit
        l'ordre des lignes — l'écriture objet par objet relisait tout trois fois
        par objet et rejouait les refus d'ordre. Tout ou rien."""
        kind = write_kind(KIND)
        store = checking_store(kind)
        if not isinstance(store, CartoObjectStore):
            raise TypeError(f"le genre {kind.name} n'écrit pas d'objets de cartographie")
        before = {d["cle"]: d for d in self.current()}
        pending = [item for item in items if before.get(item.get("cle")) != item]
        if not pending:
            return
        # `locate(None, …)` : sans relire la cartographie pour vérifier le type,
        # `save_objects` refuse de toute façon un type inconnu (check_object).
        store.write_batch(
            root,
            [
                (
                    kind.locate(None, self._identity(item)),
                    item,
                    item["cle"] not in self.space.objects,
                )
                for item in pending
            ],
        )


def target_of(space: Cartographie, slug: str) -> CartoImportTarget:
    return CartoImportTarget(space, slug, catalog())
