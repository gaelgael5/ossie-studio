"""La cartographie d'une branche entière : lecture avec signalements, et écritures
(enabler f8167ae1).

À la lecture, sont signalés : un fichier illisible ; un objet rangé hors de
`cartographie/objets/<type>/` ou sous le dossier d'un autre type ; une clé qui ne
correspond pas au nom du fichier ; une clé en double (aucune écriture n'est
acceptée tant qu'elle n'est pas corrigée) ; une relation dont la source ou la
cible n'existe pas ; une relation que les règles d'ArchiMate ne permettent pas
entre les types de ses extrémités ; des propriétés hors du schéma du type, avec la
propriété en cause.

Les écritures opèrent sur une copie de travail (`root`), dans le brouillon de la
personne (`service`). Elles refusent de changer une clé, le slug ou le
`x-archimate` d'un type, de supprimer un objet encore utilisé par une relation,
d'écrire une relation que les règles d'ArchiMate ne permettent pas.

`allowed_between` et `relation_types_between` répondent à « entre ces deux objets,
quelles relations sont permises ? » (enabler 0e088eaa) : un type de l'espace suit
le type ArchiMate sur lequel il repose (`x-archimate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ossie_studio.cartographie.catalog import Catalog, catalog
from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.cartographie.model import (
    OBJECTS_DIR,
    TYPE_SUFFIX,
    TYPES_DIR,
    CartoObject,
    CartoType,
    check_object,
    check_type,
    dump_object,
    dump_type,
    is_empty,
    object_location,
    object_path,
    parse_object,
    parse_type,
    type_path,
)
from ossie_studio.cartographie.rules import allowed_relations


@dataclass
class Cartographie:
    """Ce que la branche contient : types (par slug), objets (par clé, avec leur
    chemin), et les problèmes trouvés à la lecture."""

    types: dict[str, CartoType] = field(default_factory=dict)
    objects: dict[str, tuple[str, CartoObject]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    duplicates: set[str] = field(default_factory=set)

    def users(self, key: str) -> list[str]:
        """Les relations qui désignent cet objet comme source ou cible."""
        return sorted(
            k for k, (_p, o) in self.objects.items() if key in (o.source, o.target) and k != key
        )

    def objects_of(self, slug: str) -> list[tuple[str, CartoObject]]:
        """Les objets (chemin, objet) d'un type de l'espace."""
        return [(p, o) for p, o in self.objects.values() if o.type == slug]

    def property_usage(self, slug: str) -> dict[str, int]:
        """Pour chaque propriété ajoutée d'un type : le nombre de ses objets qui y
        ont une valeur (non vide)."""
        ctype = self.types.get(slug)
        objects = self.objects_of(slug)
        return {
            name: sum(1 for _p, o in objects if not is_empty(o.properties.get(name)))
            for name in (ctype.properties if ctype else {})
        }

    def archimate(self, key: str) -> str | None:
        """Le type ArchiMate d'un objet (celui de son type) ; None s'il est inconnu."""
        found = self.objects.get(key)
        ctype = self.types.get(found[1].type) if found else None
        return ctype.archimate if ctype else None


def _relation_not_allowed(
    space: Cartographie, obj: CartoObject, path: str, cat: Catalog
) -> Problem | None:
    """Une relation dont le type n'est pas permis entre les types de ses extrémités
    (rien n'est dit tant qu'un des types est inconnu : c'est signalé ailleurs)."""
    ctype = space.types.get(obj.type)
    base = cat.get(ctype.archimate) if ctype else None
    if base is None or not base.is_relation or not obj.source or not obj.target:
        return None
    source, target = space.archimate(obj.source), space.archimate(obj.target)
    if source is None or target is None or not cat.get(source) or not cat.get(target):
        return None
    if base.name in allowed_relations(source, target, cat):
        return None
    return Problem(path, "relation_not_allowed", f"{source} -> {target} : {base.name}")


def read_cartographie(root: Path, cat: Catalog | None = None) -> Cartographie:
    cat = cat or catalog()
    space = Cartographie()
    types_dir = root / TYPES_DIR
    for file in sorted(types_dir.glob(f"*{TYPE_SUFFIX}")) if types_dir.is_dir() else []:
        rel = file.relative_to(root).as_posix()
        slug = file.name[: -len(TYPE_SUFFIX)]
        try:
            ctype = parse_type(slug, file.read_text(encoding="utf-8"))
        except (CartographieError, UnicodeDecodeError) as exc:
            space.problems.append(Problem(rel, "type_unreadable", str(exc)))
            continue
        space.types[slug] = ctype
        space.problems.extend(check_type(ctype, cat))

    seen: dict[str, list[str]] = {}
    objects_dir = root / OBJECTS_DIR
    for file in sorted(objects_dir.rglob("*")) if objects_dir.is_dir() else []:
        if not file.is_file():
            continue
        rel = file.relative_to(root).as_posix()
        location = object_location(rel)
        if location is None:
            space.problems.append(Problem(rel, "object_misplaced"))
            continue
        folder, stem = location
        try:
            obj = parse_object(file.read_text(encoding="utf-8"))
        except (CartographieError, UnicodeDecodeError) as exc:
            space.problems.append(Problem(rel, "object_unreadable", str(exc)))
            continue
        if stem != obj.key.lower():
            space.problems.append(Problem(rel, "object_key_mismatch", obj.key))
        if folder != obj.type:
            space.problems.append(Problem(rel, "object_wrong_folder", obj.type))
        space.problems.extend(check_object(obj, rel, space.types.get(obj.type), cat))
        seen.setdefault(obj.key, []).append(rel)
        space.objects.setdefault(obj.key, (rel, obj))

    for key, paths in seen.items():
        if len(paths) > 1:
            space.duplicates.add(key)
            space.problems.extend(Problem(p, "object_key_duplicate", key) for p in paths)
    for key, (rel, obj) in space.objects.items():
        for end, value in (("source", obj.source), ("cible", obj.target)):
            if value and value not in space.objects:
                space.problems.append(Problem(rel, "relation_endpoint_unknown", value, field=end))
        if obj.source == key or obj.target == key:
            space.problems.append(Problem(rel, "relation_self", key))
        refused = _relation_not_allowed(space, obj, rel, cat)
        if refused:
            space.problems.append(refused)
    space.problems = sorted(set(space.problems))
    return space


def _refuse_duplicates(space: Cartographie) -> None:
    if space.duplicates:
        problems = [p for p in space.problems if p.code == "object_key_duplicate"]
        raise CartographieError("object_keys_duplicate", status=409, problems=problems)


def save_type(
    root: Path, ctype: CartoType, *, create: bool, cat: Catalog | None = None
) -> CartoType:
    """Crée ou modifie un type de l'espace. Le slug et `x-archimate` d'un type
    existant sont figés ; seuls le libellé et les propriétés ajoutées changent.

    Les objets du type doivent rester valides : retirer une propriété qu'ils
    utilisent est refusé (« property_in_use », avec le nombre d'objets par
    propriété), et toute autre modification qui rendrait des objets invalides
    aussi (« type_objects_invalid », avec les problèmes)."""
    cat = cat or catalog()
    space = read_cartographie(root, cat)
    current = space.types.get(ctype.slug)
    if create and (current is not None or (root / type_path(ctype.slug)).exists()):
        raise CartographieError("type_exists", status=409, slug=ctype.slug)
    if not create and current is None:
        raise CartographieError("type_not_found", status=404, slug=ctype.slug)
    if current is not None and current.archimate != ctype.archimate:
        raise CartographieError("type_archimate_frozen", status=409, slug=ctype.slug)
    problems = check_type(ctype, cat)
    if problems:
        raise CartographieError("type_invalid", problems=problems)
    if current is not None:
        _refuse_breaking_objects(space, current, ctype, cat)
    target = root / type_path(ctype.slug)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_type(ctype), encoding="utf-8")
    return ctype


def _refuse_breaking_objects(
    space: Cartographie, current: CartoType, ctype: CartoType, cat: Catalog
) -> None:
    usage = space.property_usage(current.slug)
    in_use = {name: usage[name] for name in sorted(set(current.properties) - set(ctype.properties))}
    in_use = {name: count for name, count in in_use.items() if count}
    if in_use:
        raise CartographieError("property_in_use", status=409, slug=current.slug, properties=in_use)
    broken = [
        p
        for path, obj in space.objects_of(current.slug)
        for p in check_object(obj, path, ctype, cat)
        if p.code.startswith("property_")
    ]
    if broken:
        raise CartographieError(
            "type_objects_invalid",
            status=409,
            problems=broken,
            slug=current.slug,
            objects=len({p.path for p in broken}),
        )


def delete_type(root: Path, slug: str, cat: Catalog | None = None) -> None:
    """Supprime le fichier d'un type ; refusé tant qu'il a des objets."""
    space = read_cartographie(root, cat or catalog())
    if slug not in space.types:
        raise CartographieError("type_not_found", status=404, slug=slug)
    objects = len(space.objects_of(slug))
    if objects:
        raise CartographieError("type_in_use", status=409, slug=slug, objects=objects)
    (root / type_path(slug)).unlink()


def save_object(
    root: Path, obj: CartoObject, *, create: bool, cat: Catalog | None = None
) -> CartoObject:
    """Crée ou modifie un objet (ou une relation). La clé est unique dans
    l'espace et figée ; le type d'un objet existant aussi."""
    cat = cat or catalog()
    space = read_cartographie(root, cat)
    _refuse_duplicates(space)
    existing = space.objects.get(obj.key)
    if create and existing is not None:
        raise CartographieError("object_key_taken", status=409, key=obj.key)
    if not create and existing is None:
        raise CartographieError("object_not_found", status=404, key=obj.key)
    if existing is not None and existing[1].type != obj.type:
        raise CartographieError("object_type_frozen", status=409, key=obj.key)
    path = object_path(obj.type, obj.key)
    problems = check_object(obj, path, space.types.get(obj.type), cat)
    for end, value in (("source", obj.source), ("cible", obj.target)):
        if value and (value == obj.key or value not in space.objects):
            problems.append(Problem(path, "relation_endpoint_unknown", value, field=end))
    refused = _relation_not_allowed(space, obj, path, cat)
    if refused:
        problems.append(refused)
    if problems:
        raise CartographieError("object_invalid", problems=problems)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_object(obj), encoding="utf-8")
    return obj


def delete_object(root: Path, key: str, cat: Catalog | None = None) -> None:
    """Supprime le fichier d'un objet ; refusé tant qu'une relation l'utilise."""
    space = read_cartographie(root, cat or catalog())
    _refuse_duplicates(space)
    found = space.objects.get(key)
    if found is None:
        raise CartographieError("object_not_found", status=404, key=key)
    users = space.users(key)
    if users:
        raise CartographieError("object_in_use", status=409, key=key, relations=users)
    (root / found[0]).unlink()


def allowed_between(
    root: Path, source_key: str, target_key: str, cat: Catalog | None = None
) -> list[str]:
    """Les types de relation ArchiMate permis de l'objet `source_key` vers l'objet
    (ou la relation) `target_key`. Refus nommé si l'un des deux n'existe pas."""
    cat = cat or catalog()
    space = read_cartographie(root, cat)
    return _allowed(space, source_key, target_key, cat)


def relation_types_between(
    root: Path, source_key: str, target_key: str, cat: Catalog | None = None
) -> list[CartoType]:
    """Les types de relation **de l'espace** que l'on peut créer entre ces deux
    objets (ceux dont le `x-archimate` est permis), par libellé : ce que proposent
    « + Relier… » et le délégué des relations permises du diagramme."""
    cat = cat or catalog()
    space = read_cartographie(root, cat)
    permitted = set(_allowed(space, source_key, target_key, cat))
    found = [t for t in space.types.values() if t.archimate in permitted]
    return sorted(found, key=lambda t: (t.title.casefold(), t.slug))


def _allowed(space: Cartographie, source_key: str, target_key: str, cat: Catalog) -> list[str]:
    for key in (source_key, target_key):
        if key not in space.objects:
            raise CartographieError("object_not_found", status=404, key=key)
    source, target = space.archimate(source_key), space.archimate(target_key)
    if source is None or target is None:
        return []
    return allowed_relations(source, target, cat)
