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

La validation est celle du service de la cartographie (`space.check_batch`) :
aucune règle n'est réécrite ici.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, is_empty
from ossie_studio.cartographie.space import Cartographie, check_batch, save_object
from ossie_studio.imports.target import ItemProblem

JOURNAL_TYPE = "carto_import"
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

    def check(self, produced: list[Any]) -> list[ItemProblem]:
        problems: list[ItemProblem] = []
        objects: list[CartoObject] = []
        ranks: list[int] = []
        for index, item in enumerate(produced):
            key = str(item.get("cle") or "") if isinstance(item, dict) else ""
            if not isinstance(item, dict):
                problems.append(ItemProblem(index, "object_not_mapping", "", "", key))
                continue
            unknown = sorted(set(item) - set(FIELDS))
            if unknown:
                problems.append(
                    ItemProblem(index, "object_field_unknown", ", ".join(unknown), unknown[0], key)
                )
                continue
            if item.get("type") not in (None, self.slug):
                problems.append(
                    ItemProblem(index, "object_type_frozen", str(item["type"]), "type", key)
                )
                continue
            obj = self._object(item)
            if obj is None:
                problems.append(ItemProblem(index, "object_field_invalid", "", "", key))
                continue
            objects.append(obj)
            ranks.append(index)
        for p in check_batch(self.space, objects, self.cat):
            problems.append(
                ItemProblem(ranks[p.index], p.code, p.detail, p.field, objects[p.index].key)
            )
        return sorted(problems, key=lambda p: (p.index, p.code, p.field))

    def key(self, item: Any) -> Hashable | None:
        key = item.get("cle") if isinstance(item, dict) else None
        return key if isinstance(key, str) and key else None

    def label(self, item: Any, index: int) -> str:
        key = self.key(item)
        return str(key) if key is not None else f"#{index + 1}"

    def write(self, root: Path, items: list[Any]) -> None:
        """Crée ou modifie, par le service, les objets qui changent ; aucun n'est
        supprimé (fusion seulement)."""
        before = {d["cle"]: d for d in self.current()}
        pending = []
        for item in items:
            if before.get(item.get("cle")) == item:
                continue
            obj = self._object(item)
            assert obj is not None  # validé avant
            pending.append(obj)
        # Le lot est validé comme un tout ; à l'écriture, une relation peut viser un
        # objet (ou une relation) écrit plus loin : on rejoue tant qu'un passage
        # avance, puis on laisse le dernier refus remonter (tout ou rien).
        while pending:
            failed: list[CartoObject] = []
            last: CartographieError | None = None
            for obj in pending:
                try:
                    save_object(root, obj, create=obj.key not in self.space.objects, cat=self.cat)
                except CartographieError as exc:
                    failed.append(obj)
                    last = exc
            if last is not None and len(failed) == len(pending):
                raise last
            pending = failed


def target_of(space: Cartographie, slug: str) -> CartoImportTarget:
    return CartoImportTarget(space, slug, catalog())
