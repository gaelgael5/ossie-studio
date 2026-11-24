"""Lecture et écriture des référentiels dans la vue d'une branche (`root`).

Fonctions pures sur le dossier de travail : elles vérifient tout **avant**
d'écrire et lèvent ReferentielError sinon. L'écriture dans le brouillon de la
personne passe par `service.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.imports import (
    ImportDeclaration,
    ReferentielImport,
    check_declaration,
    dump_declaration,
    example_name,
    parse_declaration,
)
from ossie_studio.referentiels.model import dump_model, load_model, validate_model
from ossie_studio.referentiels.values import (
    dump_values,
    load_values,
    sorted_values,
    validate_values,
)
from ossie_studio.slugs import is_valid_slug

REFERENTIELS_DIR = "referentiels"
MODEL_FILE = "modele.schema.yaml"
VALUES_FILE = "valeurs.json"
IMPORTS_DIR = "imports"


@dataclass(frozen=True)
class Referentiel:
    name: str
    model: dict[str, Any]
    values: list[dict[str, Any]]
    imports: list[ReferentielImport]


def referentiel_dir(name: str) -> str:
    """`referentiels/<nom>` ; le nom : minuscules, chiffres, tirets."""
    if not is_valid_slug(name):
        raise ReferentielError("referentiel_name_invalid", name=name)
    return f"{REFERENTIELS_DIR}/{name}"


def model_path(name: str) -> str:
    return f"{referentiel_dir(name)}/{MODEL_FILE}"


def values_path(name: str) -> str:
    return f"{referentiel_dir(name)}/{VALUES_FILE}"


def import_paths(name: str, decl: ImportDeclaration) -> list[str]:
    base = f"{referentiel_dir(name)}/{IMPORTS_DIR}"
    return [
        f"{base}/{decl.slug}.yaml",
        f"{base}/{decl.slug}.jslt",
        f"{base}/{example_name(decl.slug, decl.format)}",
    ]


def _exists(root: Path, name: str) -> bool:
    return (root / model_path(name)).is_file()


def list_referentiels(root: Path) -> list[str]:
    base = root / REFERENTIELS_DIR
    if not base.is_dir():
        return []
    return sorted(
        d.name
        for d in base.iterdir()
        if d.is_dir() and is_valid_slug(d.name) and _exists(root, d.name)
    )


# --- Lecture ------------------------------------------------------------------------------


def read_imports(root: Path, name: str) -> list[ReferentielImport]:
    folder = root / referentiel_dir(name) / IMPORTS_DIR
    if not folder.is_dir():
        return []
    out: list[ReferentielImport] = []
    for file in sorted(folder.glob("*.yaml")):
        slug = file.stem
        if not is_valid_slug(slug):
            continue
        decl = parse_declaration(slug, file.read_text(encoding="utf-8"))
        template = folder / f"{slug}.jslt"
        example = folder / example_name(slug, decl.format)
        out.append(
            ReferentielImport(
                decl,
                template.read_text(encoding="utf-8") if template.is_file() else "",
                example.read_bytes() if example.is_file() else b"",
            )
        )
    return out


def read_referentiel(root: Path, name: str) -> Referentiel:
    if not _exists(root, name):
        raise ReferentielError("referentiel_not_found", 404, name=name)
    model = load_model((root / model_path(name)).read_text(encoding="utf-8"))
    if not isinstance(model, dict):
        raise ReferentielError("model_unreadable", name=name)
    values_file = root / values_path(name)
    values = load_values(values_file.read_text(encoding="utf-8")) if values_file.is_file() else []
    if not isinstance(values, list):
        raise ReferentielError("values_unreadable", name=name)
    return Referentiel(name, model, values, read_imports(root, name))


# --- Écriture -----------------------------------------------------------------------------


def _write_text(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_referentiel(
    root: Path,
    name: str,
    model: Any,
    values: Any,
    *,
    create: bool,
) -> Referentiel:
    """Écrit le modèle et les valeurs d'un référentiel. `create` : le nom doit
    être libre (« referentiel_name_taken ») ; sinon il doit exister. Modèle puis
    valeurs validés avant toute écriture ; sortie déterministe."""
    referentiel_dir(name)
    checked_model = validate_model(model)
    checked_values = validate_values(checked_model, values)
    exists = _exists(root, name)
    if create and exists:
        raise ReferentielError("referentiel_name_taken", 409, name=name)
    if not create and not exists:
        raise ReferentielError("referentiel_not_found", 404, name=name)
    _write_text(root, model_path(name), dump_model(checked_model))
    _write_text(root, values_path(name), dump_values(checked_model, checked_values))
    return Referentiel(
        name,
        checked_model,
        sorted_values(checked_model, checked_values),
        read_imports(root, name),
    )


def write_import(
    root: Path,
    name: str,
    decl: ImportDeclaration,
    template: str,
    example: bytes,
    *,
    create: bool,
) -> ReferentielImport:
    """Écrit les trois fichiers d'un import. `create` : le slug doit être libre
    (« import_slug_taken ») ; sinon l'import doit exister."""
    if not _exists(root, name):
        raise ReferentielError("referentiel_not_found", 404, name=name)
    if not is_valid_slug(decl.slug):
        raise ReferentielError("import_slug_invalid", slug=decl.slug)
    problems = check_declaration(decl)
    if problems:
        raise ReferentielError("import_invalid", problems=problems)
    folder = root / referentiel_dir(name) / IMPORTS_DIR
    declaration = folder / f"{decl.slug}.yaml"
    if create and declaration.is_file():
        raise ReferentielError("import_slug_taken", 409, slug=decl.slug)
    if not create and not declaration.is_file():
        raise ReferentielError("import_not_found", 404, slug=decl.slug)
    yaml_rel, jslt_rel, example_rel = import_paths(name, decl)
    _write_text(root, yaml_rel, dump_declaration(decl))
    _write_text(root, jslt_rel, template)
    target = root / example_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(example)
    # Format changé : l'ancien fichier d'exemple (autre extension) disparaît.
    for other in ("csv", "json"):
        stale = folder / f"{decl.slug}.exemple.{other}"
        if other != decl.format and stale.is_file():
            stale.unlink()
    return ReferentielImport(decl, template, example)
