"""La cartographie d'une branche entière : lecture avec signalements, et écritures
(enabler f8167ae1).

À la lecture, sont signalés : un fichier illisible ; un objet rangé hors de
`cartographie/objets/<type>/` ou sous le dossier d'un autre type ; une clé qui ne
correspond pas au nom du fichier ; une clé en double (aucune écriture n'est
acceptée tant qu'elle n'est pas corrigée) ; une relation dont la source ou la
cible n'existe pas ; des propriétés hors du schéma du type, avec la propriété en
cause.

Les écritures opèrent sur une copie de travail (`root`), dans le brouillon de la
personne (`service`). Elles refusent de changer une clé, le slug ou le
`x-archimate` d'un type, de supprimer un objet encore utilisé par une relation.
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
    object_location,
    object_path,
    parse_object,
    parse_type,
    type_path,
)


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
    existant sont figés ; seuls le libellé et les propriétés ajoutées changent."""
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
    target = root / type_path(ctype.slug)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_type(ctype), encoding="utf-8")
    return ctype


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
