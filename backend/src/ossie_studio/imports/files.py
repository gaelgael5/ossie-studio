"""Lire et écrire les trois fichiers d'un import dans un dossier d'imports
(`folder`, relatif à la racine de la vue), quelle que soit la cible (enabler
4f32c8c6, repris de `referentiels/repo.py`). Tout est vérifié **avant**
d'écrire ; un refus lève ImportsError."""

from __future__ import annotations

from pathlib import Path

from ossie_studio.imports.declaration import (
    FORMATS,
    Import,
    ImportDeclaration,
    check_declaration,
    dump_declaration,
    example_name,
    parse_declaration,
)
from ossie_studio.imports.errors import ImportsError
from ossie_studio.imports.provenance import SUFFIX as PROVENANCE_SUFFIX
from ossie_studio.slugs import is_valid_slug


def import_paths(folder: str, decl: ImportDeclaration) -> list[str]:
    return [
        f"{folder}/{decl.slug}.yaml",
        f"{folder}/{decl.slug}.jslt",
        f"{folder}/{example_name(decl.slug, decl.format)}",
    ]


def read_imports(root: Path, folder: str) -> list[Import]:
    directory = root / folder
    if not directory.is_dir():
        return []
    out: list[Import] = []
    for file in sorted(directory.glob("*.yaml")):
        slug = file.stem
        if not is_valid_slug(slug):
            continue
        decl = parse_declaration(slug, file.read_text(encoding="utf-8"))
        template = directory / f"{slug}.jslt"
        example = directory / example_name(slug, decl.format)
        out.append(
            Import(
                decl,
                template.read_text(encoding="utf-8") if template.is_file() else "",
                example.read_bytes() if example.is_file() else b"",
            )
        )
    return out


def read_import(root: Path, folder: str, slug: str) -> Import:
    found = next((i for i in read_imports(root, folder) if i.slug == slug), None)
    if found is None:
        raise ImportsError("import_not_found", 404, slug=slug)
    return found


def _write_text(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_import(
    root: Path,
    folder: str,
    decl: ImportDeclaration,
    template: str,
    example: bytes,
    *,
    create: bool,
) -> Import:
    """Écrit les trois fichiers d'un import. `create` : le slug doit être libre
    (« import_slug_taken ») ; sinon l'import doit exister."""
    if not is_valid_slug(decl.slug):
        raise ImportsError("import_slug_invalid", slug=decl.slug)
    problems = check_declaration(decl)
    if problems:
        raise ImportsError("import_invalid", problems=problems)
    directory = root / folder
    declaration = directory / f"{decl.slug}.yaml"
    if create and declaration.is_file():
        raise ImportsError("import_slug_taken", 409, slug=decl.slug)
    if not create and not declaration.is_file():
        raise ImportsError("import_not_found", 404, slug=decl.slug)
    yaml_rel, jslt_rel, example_rel = import_paths(folder, decl)
    _write_text(root, yaml_rel, dump_declaration(decl))
    _write_text(root, jslt_rel, template)
    target = root / example_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(example)
    # Format changé : l'ancien fichier d'exemple (autre extension) disparaît.
    for other in FORMATS:
        stale = directory / example_name(decl.slug, other)
        if other != decl.format and stale.is_file():
            stale.unlink()
    return Import(decl, template, example)


def delete_import(root: Path, folder: str, slug: str) -> None:
    """Supprime les trois fichiers d'un import, et sa provenance (« import_not_found »
    s'il n'existe pas)."""
    directory = root / folder
    if not is_valid_slug(slug) or not (directory / f"{slug}.yaml").is_file():
        raise ImportsError("import_not_found", 404, slug=slug)
    files = [f"{slug}.yaml", f"{slug}.jslt", *(example_name(slug, f) for f in FORMATS)]
    for file in [*files, f"{slug}{PROVENANCE_SUFFIX}"]:
        target = directory / file
        if target.is_file():
            target.unlink()
