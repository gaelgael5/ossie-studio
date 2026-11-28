"""Un type de cartographie, cible du module commun d'import (ticket 49952be0).

Gael : « Il faut la possibilité de pouvoir mettre un système d'intégration pour
alimenter les objets cartographiques. On reprend le même système que pour les
référentiels. » **Une ligne = un objet** du type : le template rend `cle`, `nom`,
`documentation` (facultative), `proprietes` — et, pour un type de relation,
`source` et `cible` (`type/CLÉ`, ou une clé seule si elle n'existe que dans un
type). Le `type` est celui du type visé (pas à écrire dans le template ; s'il y
est, il doit être le même).

Décisions de Gael : comparaison par la **clé** (dans le type importé : deux types
peuvent avoir la même clé, ticket 421832ee), **fusion seulement** (un objet
absent est créé, un objet présent modifié — jamais sa clé, son type, ni la source
ou la cible d'une relation) ; écriture dans le brouillon, tout ou rien, un seul
enregistrement.

**Suppressions** (ticket 6d36073b) : les objets absents du fichier suivent la
stratégie de suppression de l'import (module commun). Un objet est **encore
utilisé** quand une relation qui reste (dans l'état final) le vise, en source ou en
cible — la règle de l'écran (`object_in_use`) ; sa présence dans un modèle ne
retient rien. Les suppressions passent par le service (`space.delete_objects`).

**Validation et écriture** (epic « Primitives MCP — écriture commune par type de document »,
ticket 1612cce4) : la cible est le type de document `carto_objet` du registre. Chaque ligne est
validée par la méthode du validateur de ce type de document — celle de `write_documents`, qui
applique les règles du service (`space.check_batch`) — et écrite par lui ; ses refus
portent le même code et le même message que par MCP. Aucune validation n'est propre
à l'import.

**Valeur absente du référentiel lié** (ticket 43478978) : la ligne n'est pas refusée,
elle est **taguée** (`tags`) ; la personne choisit de la refuser avant d'importer.
L'index des clés des référentiels liés est lu une fois par import (`target_of`).
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.model import CartoObject, is_empty, object_path, object_ref
from ossie_studio.cartographie.referentiel_links import KeyIndex, absent_values, shown
from ossie_studio.cartographie.space import Cartographie, delete_objects
from ossie_studio.imports.target import ItemProblem
from ossie_studio.mcp_surface.document_type_imports import check_import, checking_store
from ossie_studio.mcp_surface.document_type_stores import CartoObjectStore
from ossie_studio.mcp_surface.document_types import get_document_type

JOURNAL_TYPE = "carto_import"
DOCUMENT_TYPE = "carto_objet"
IMPORTS_DIR = "cartographie/imports"
# Ce qu'une ligne rend (format du fichier d'un objet, sans le type).
FIELDS = ("cle", "type", "source", "cible", "nom", "documentation", "proprietes")


def imports_dir(slug: str) -> str:
    return f"{IMPORTS_DIR}/{slug}"


def _document(obj: CartoObject, space: Cartographie) -> dict[str, Any]:
    """L'objet tel qu'une ligne le rend, extrémités en `type/CLÉ` quand on les trouve :
    une relation inchangée se compare égale, qu'elle soit écrite en clé seule (forme
    d'avant) ou en `type/CLÉ`."""
    doc = obj.document()
    doc.pop("type", None)
    for end in ("source", "cible"):
        if isinstance(doc.get(end), str):
            doc[end] = space.resolve(doc[end]) or doc[end]
    return doc


@dataclass(frozen=True)
class CartoImportTarget:
    space: Cartographie
    slug: str
    cat: Catalog
    # Les clés des référentiels liés au type, lues une fois (None : aucun lien).
    index: KeyIndex | None = None

    def current(self) -> list[dict[str, Any]]:
        return [_document(o, self.space) for _p, o in self.space.objects_of(self.slug)]

    def normalize(self, item: Any) -> Any:
        """Forme canonique, celle qu'écrirait le fichier : sans documentation ni
        propriétés vides, pour qu'une ligne inchangée se compare égale."""
        if not isinstance(item, dict) or set(item) - set(FIELDS):
            return item
        obj = self._object(item)
        return item if obj is None else _document(obj, self.space)

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
            for r in check_import(DOCUMENT_TYPE, self.space, entries, space=self.space)
        ]

    def tags(self, produced: list[Any]) -> dict[int, list[dict[str, Any]]]:
        """Les lignes dont une valeur manque au référentiel lié (rang → tags) : la
        propriété, le référentiel et la valeur, pour que la personne décide."""
        links = self.space.links.get(self.slug, [])
        if not links or self.index is None:
            return {}
        found: dict[int, list[dict[str, Any]]] = {}
        for index, item in enumerate(produced):
            obj = self._object(item) if isinstance(item, dict) else None
            if obj is None:
                continue
            absent = absent_values(obj.properties, links, self.index)
            if absent:
                found[index] = [
                    self._tag(a.link.referentiel, a.properties, a.value) for a in absent
                ]
        return found

    def _tag(self, name: str, properties: list[str], value: tuple[Any, ...]) -> dict[str, Any]:
        info = self.space.referentiels.get(name)
        return {
            "code": "property_referentiel_unknown",
            "property": properties[0],
            "properties": list(properties),
            "referentiel": name,
            "title": info.title if info is not None and info.title else name,
            "value": shown(value),
        }

    def ref(self, item: Any) -> str | None:
        """La fiche d'un objet du type : `type/CLÉ`."""
        key = self.key(item)
        return object_ref(self.slug, str(key)) if key is not None else None

    def in_use(self, produced: list[Any], deleting: set[Hashable]) -> dict[Hashable, list[Any]]:
        """Les objets du lot à supprimer (`deleting`, des clés du type) qu'une
        relation qui reste vise encore, avec ces relations : l'état final est
        l'espace, les objets du fichier mis à jour, sans ceux qu'on supprime."""
        merged = Cartographie(types=self.space.types, objects=dict(self.space.objects))
        for item in produced:
            obj = self._object(item) if isinstance(item, dict) else None
            if obj is not None and obj.key:
                merged.put(object_path(obj.type, obj.key), obj)
        going = {object_ref(self.slug, str(k)) for k in deleting}
        found: dict[Hashable, list[Any]] = {}
        for key in deleting:
            ref = object_ref(self.slug, str(key))
            users = [u for u in merged.users(ref) if u not in going]
            if users:
                found[key] = [self._usage(merged, u, ref) for u in users]
        return found

    def _usage(self, space: Cartographie, relation_ref: str, ref: str) -> dict[str, Any]:
        """Une relation qui vise l'objet, et l'objet à son autre bout (pour les liens
        de la page des éléments gardés)."""
        _path, relation = space.objects[relation_ref]
        source, target = space.ends(relation)
        other_ref = target if source == ref else source
        other = space.find(other_ref)
        rtype = space.types.get(relation.type)
        other_type = space.types.get(other[1].type) if other else None
        return {
            "ref": relation_ref,
            "key": relation.key,
            "name": relation.name,
            "type": relation.type,
            "type_title": rtype.title if rtype else relation.type,
            # La place de l'objet gardé dans la relation.
            "role": "target" if target == ref else "source",
            "other": {
                "ref": other_ref,
                "key": other[1].key if other else None,
                "name": other[1].name if other else None,
                "type_title": other_type.title if other_type else None,
            },
        }

    def key(self, item: Any) -> Hashable | None:
        key = item.get("cle") if isinstance(item, dict) else None
        return key if isinstance(key, str) and key else None

    def label(self, item: Any, index: int) -> str:
        key = self.key(item)
        return str(key) if key is not None else f"#{index + 1}"

    def write(self, root: Path, items: list[Any]) -> None:
        """Crée ou modifie, par le validateur du type de document (celui de write_documents), les
        objets qui changent, puis supprime ceux du type absents de `items` (la
        stratégie de suppression les a retenus, ticket 6d36073b). Le lot est
        écrit d'un bloc (ticket eed2dd0d) : la cartographie est lue une fois, et
        une relation qui vise un objet (ou une relation) du lot passe quel que soit
        l'ordre des lignes — l'écriture objet par objet relisait tout trois fois
        par objet et rejouait les refus d'ordre. Tout ou rien."""
        doc_type = get_document_type(DOCUMENT_TYPE)
        store = checking_store(doc_type)
        if not isinstance(store, CartoObjectStore):
            raise TypeError(
                f"le type de document {doc_type.name} n'écrit pas d'objets de cartographie"
            )
        before = {d["cle"]: d for d in self.current()}
        pending = [item for item in items if before.get(item.get("cle")) != item]
        kept = {item.get("cle") for item in items}
        going = [object_ref(self.slug, str(k)) for k in before if k not in kept]
        # `locate(None, …)` : sans relire la cartographie pour vérifier le type,
        # `save_objects` refuse de toute façon un type inconnu (check_object).
        if pending:
            store.write_batch(
                root,
                [
                    (
                        doc_type.locate(None, self._identity(item)),
                        item,
                        object_ref(self.slug, item["cle"]) not in self.space.objects,
                    )
                    for item in pending
                ],
            )
        # Les absents que la stratégie supprime, après les créations et corrections :
        # une relation créée ou gardée par cet import retient son extrémité.
        if going:
            delete_objects(root, going)


def target_of(space: Cartographie, slug: str, root: Path | None = None) -> CartoImportTarget:
    """La cible d'un import du type. `root` : la vue où lire, **tout de suite**, les
    clés des référentiels liés (l'essai et l'import tournent ensuite hors de la vue)."""
    links = space.links.get(slug, [])
    index = None
    if links and root is not None:
        index = KeyIndex(root)
        for link in links:
            index.keys(link.referentiel)
    return CartoImportTarget(space, slug, catalog(), index)
