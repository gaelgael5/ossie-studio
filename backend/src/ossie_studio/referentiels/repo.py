"""Lecture et écriture des référentiels dans la vue d'une branche (`root`).

Fonctions pures sur le dossier de travail : elles vérifient tout **avant**
d'écrire et lèvent ReferentielError sinon. L'écriture dans le brouillon de la
personne passe par `service.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ossie_studio.imports import files
from ossie_studio.imports.declaration import Import, ImportDeclaration
from ossie_studio.imports.errors import ImportsError
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import dump_model, load_model, model_key, validate_model
from ossie_studio.referentiels.values import (
    dump_values,
    key_of,
    load_values,
    sorted_values,
    validate_values,
)
from ossie_studio.roundtrip import write_document
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
    imports: list[Import]


def referentiel_dir(name: str) -> str:
    """`referentiels/<nom>` ; le nom : minuscules, chiffres, tirets."""
    if not is_valid_slug(name):
        raise ReferentielError("referentiel_name_invalid", name=name)
    return f"{REFERENTIELS_DIR}/{name}"


def model_path(name: str) -> str:
    return f"{referentiel_dir(name)}/{MODEL_FILE}"


def values_path(name: str) -> str:
    return f"{referentiel_dir(name)}/{VALUES_FILE}"


def imports_dir(name: str) -> str:
    """Le dossier des imports d'un référentiel (cible du module commun d'import)."""
    return f"{referentiel_dir(name)}/{IMPORTS_DIR}"


def import_paths(name: str, decl: ImportDeclaration) -> list[str]:
    return files.import_paths(imports_dir(name), decl)


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


@contextmanager
def _as_referentiel() -> Iterator[None]:
    """Un refus du module commun d'import devient un refus du référentiel, au même
    code : ses appelants (routes, tests) ne voient pas la différence."""
    try:
        yield
    except ReferentielError:
        raise
    except ImportsError as exc:
        raise ReferentielError(exc.code, exc.status, exc.problems, **exc.detail) from None


def read_imports(root: Path, name: str) -> list[Import]:
    with _as_referentiel():
        return files.read_imports(root, imports_dir(name))


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
    # Aller-retour (14bb8eb8) : modèle et valeurs écrits sur le texte existant.
    # Valeurs rapprochées par leur clé : l'ordre du fichier est gardé, une
    # nouvelle valeur va à sa place dans l'ordre des clés (Gael, 28/11).
    key = model_key(checked_model)
    write_document(root / model_path(name), checked_model, dump_model)
    write_document(
        root / values_path(name),
        sorted_values(checked_model, checked_values),
        lambda values: dump_values(checked_model, values),
        identity=lambda value: key_of(value, key) if isinstance(value, dict) else None,
        keep_order=True,
    )
    return Referentiel(
        name,
        checked_model,
        sorted_values(checked_model, checked_values),
        read_imports(root, name),
    )


def require_referentiel(root: Path, name: str) -> None:
    """Refus « referentiel_not_found » si le référentiel n'existe pas."""
    if not _exists(root, name):
        raise ReferentielError("referentiel_not_found", 404, name=name)


def write_import(
    root: Path,
    name: str,
    decl: ImportDeclaration,
    template: str,
    example: bytes,
    *,
    create: bool,
) -> Import:
    """Écrit les trois fichiers d'un import du référentiel (module commun)."""
    require_referentiel(root, name)
    with _as_referentiel():
        return files.write_import(root, imports_dir(name), decl, template, example, create=create)


def delete_import(root: Path, name: str, slug: str) -> None:
    require_referentiel(root, name)
    with _as_referentiel():
        files.delete_import(root, imports_dir(name), slug)


def read_import(root: Path, name: str, slug: str) -> Import:
    require_referentiel(root, name)
    with _as_referentiel():
        return files.read_import(root, imports_dir(name), slug)
