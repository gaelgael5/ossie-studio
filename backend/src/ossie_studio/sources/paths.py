from __future__ import annotations

import re
from pathlib import Path

# Toutes les sources vivent sous ce répertoire de la copie de travail.
SOURCES_DIR = "sources"
MANIFEST_FILENAME = "manifest.yaml"

# Un slug de source : minuscules, chiffres et tirets, sans tiret en bord ni
# double tiret. Volontairement strict : il devient un nom de répertoire.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_valid_source_slug(slug: str) -> bool:
    if not slug or len(slug) > 64:
        return False
    if ".." in slug or "/" in slug or "\\" in slug:
        return False
    return bool(_SLUG_RE.match(slug))


def source_rel_dir(slug: str) -> str:
    """Chemin relatif du répertoire d'une source, validé. Lève ValueError si le
    slug est invalide — aucun chemin n'est jamais construit depuis une saisie
    non validée."""
    if not is_valid_source_slug(slug):
        raise ValueError("slug_invalid")
    return f"{SOURCES_DIR}/{slug}"


def safe_source_path(workdir: Path, slug: str) -> Path:
    """Chemin absolu du répertoire d'une source dans une copie de travail, avec
    garantie qu'il reste **sous `sources/`** : défense en profondeur contre une
    traversée, même si le slug a échappé à la validation en amont."""
    base = (workdir / SOURCES_DIR).resolve()
    target = (workdir / SOURCES_DIR / slug).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path_escape")
    if target == base:
        raise ValueError("slug_invalid")
    return target
