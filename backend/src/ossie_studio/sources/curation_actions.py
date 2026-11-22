"""Gestes de curation qui écrivent : retenir, retirer, enrichir, rafraîchir.

Service commun à l'écran (routes `api/curation.py`) et à l'agent (primitives MCP
d'écriture) : une seule règle métier. Chaque geste écrit dans le brouillon de
la personne (`DraftWorkdir.commit_to_branch`), jamais dans un commit, et rend la
révision du brouillon (None si rien n'a changé) et les chemins touchés.

Mode strict (agent) : un champ inconnu est refusé (`UnknownFieldsError`) au
lieu d'être ignoré, comme l'écran le fait. Un agent qui se trompe de nom de
champ doit le savoir.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import (
    enrich_asset_fn,
    refresh_assets_fn,
    refresh_targets,
    remove_asset_fn,
    retain_asset_fn,
)
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    safe_assets_path,
    safe_source_path,
    source_rel_dir,
)


class UnknownFieldsError(Exception):
    """Champs demandés absents de l'objet (mode strict)."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


@dataclass(frozen=True)
class CurationTarget:
    """Où et pour qui écrire : la branche d'un espace, dans le brouillon de la
    personne (`workdir`), sous son identité (`author`)."""

    workdir: DraftWorkdir
    space: GitSpace
    branch_ref: str
    username: str
    token: str | None
    author: str
    # Précision de l'intention journalisée (« via l'agent … ») ; None à l'écran.
    via: str | None = None
    strict: bool = False

    async def write(self, intent: str, fn: Callable[[Path], None]) -> str | None:
        message = f"{intent} ({self.via})" if self.via else intent
        return await self.workdir.commit_to_branch(
            self.space.id, self.branch_ref, self.space.repo_url, self.username, self.token,
            self.author, self.author, message, fn,
        )


@dataclass(frozen=True)
class WriteResult:
    # Repère de révision du brouillon (`draft:<n>`), None si rien n'a changé.
    revision: str | None
    # Chemins du dépôt touchés dans le brouillon (vide si rien n'a changé).
    paths: list[str] = field(default_factory=list)


def asset_repo_path(slug: str, path: str) -> str:
    return f"{source_rel_dir(slug)}/{ASSETS_DIR}/{path}"


def _result(revision: str | None, slug: str, paths: list[str]) -> WriteResult:
    return WriteResult(revision, [asset_repo_path(slug, p) for p in paths] if revision else [])


def _properties(file: Path) -> set[str] | None:
    if not file.is_file():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    props = data.get("properties") if isinstance(data, dict) else None
    return set(props) if isinstance(props, dict) else set()


def _check_fields(file: Path, fields: list[str] | None) -> None:
    """Mode strict : refuse les champs absents de l'objet lu dans `file`."""
    if not fields:
        return
    known = _properties(file)
    if known is None:
        return  # l'objet manque : le geste lui-même le signale
    unknown = sorted(set(fields) - known)
    if unknown:
        raise UnknownFieldsError(unknown)


async def retain(
    target: CurationTarget, slug: str, path: str, fields: list[str] | None
) -> WriteResult:
    """Retenir un objet brut (en entier si `fields` est None). Lève
    AssetOriginMissingError si le brut n'existe pas."""
    inner = retain_asset_fn(slug, path, fields)

    def write(root: Path) -> None:
        if target.strict:
            _check_fields(safe_source_path(root, slug) / RAW_DIR / path, fields)
        inner(root)

    revision = await target.write(f"curation {slug} : retenir {path}", write)
    return _result(revision, slug, [path])


async def remove(target: CurationTarget, slug: str, path: str) -> WriteResult:
    """Retirer un asset (sans effet s'il n'existe pas)."""
    revision = await target.write(f"curation {slug} : retirer {path}", remove_asset_fn(slug, path))
    return _result(revision, slug, [path])


async def enrich(
    target: CurationTarget,
    slug: str,
    path: str,
    object_values: dict[str, Any] | None,
    field_values: dict[str, dict[str, Any]],
) -> WriteResult:
    """Poser ou retirer l'enrichissement de l'objet et de ses champs. Seules les
    clés données changent (une valeur vide retire la clé). Lève
    AssetNotRetainedError si l'asset n'existe pas."""
    inner = enrich_asset_fn(slug, path, object_values, field_values)

    def write(root: Path) -> None:
        if target.strict:
            _check_fields(safe_assets_path(root, slug) / path, list(field_values))
        inner(root)

    revision = await target.write(f"curation {slug} : enrichir {path}", write)
    return _result(revision, slug, [path])


async def refresh(target: CurationTarget, slug: str, paths: list[str] | None) -> WriteResult:
    """Rafraîchir depuis le brut les assets retenus (tous si `paths` est None).
    Ne touche que ceux qui changeraient ; sans changement, rien n'est écrit."""
    changed = await target.workdir.read_branch(
        target.space.id, target.branch_ref, target.space.repo_url, target.username, target.token,
        lambda root: refresh_targets(root, slug, paths),
    )
    if not changed:
        return WriteResult(None)
    intent = f"curation {slug} : rafraîchir {len(changed)} asset(s) : {', '.join(changed)}"
    revision = await target.write(intent, refresh_assets_fn(slug, changed))
    return _result(revision, slug, changed)
