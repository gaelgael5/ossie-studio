"""Outils de test du modèle « brouillon » : une copie de travail vue par un
utilisateur (DraftWorkdir) branchée sur une base de test, et un collecteur qui
l'utilise par défaut."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.sources.collect import CollectReport, SourceCollector
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.spaces.views import DraftViewManager
from ossie_studio.spaces.workdir import WorkdirManager


async def draft_workdir(
    app: FastAPI, tmp_path: Path, space_url: str, branch_slug: str = "principale"
) -> tuple[WorkdirManager, DraftWorkdir, GitSpace]:
    """Espace + branche + utilisateur en base, et la copie de travail de cet
    utilisateur sur cette branche (volume de travail sous `tmp_path/work`)."""
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ($1, 'user') RETURNING id",
            f"u-{uuid.uuid4().hex[:8]}@x.org",
        )
        slug = f"s-{uuid.uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO secrets (slug, label, category, value_encrypted) "
            "VALUES ($1, 's', 'github', '\\x00')",
            slug,
        )
        connection_id = await conn.fetchval(
            "INSERT INTO connections (label, slug, forge, url, secret_slug, identity, last_status) "
            "VALUES ($1, $1, 'github', 'https://g', $1, 'i', 'valid') RETURNING id",
            slug,
        )
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ($1, $2, $3, 'valid') RETURNING id",
            slug,
            connection_id,
            space_url,
        )
        branch_id = await conn.fetchval(
            "INSERT INTO space_branches (space_id, label, slug, kind, git_ref) "
            "VALUES ($1, 'Principale', $2, 'base', $3) RETURNING id",
            space_id,
            branch_slug,
            f"work/{branch_slug}",
        )
    manager = WorkdirManager(tmp_path / "work")
    workdir = DraftWorkdir(manager, DraftViewManager(manager), app.state.pool, user_id, branch_id)
    space = GitSpace(
        id=space_id,
        name=slug,
        connection_id=connection_id,
        repo_url=space_url,
        last_probe_status="valid",
        last_probe_at=None,
        created_at=datetime.now(UTC),
    )
    return manager, workdir, space


def view_dir(tmp_path: Path, space: GitSpace, branch_slug: str = "principale") -> Path:
    """Vue éphémère (la seule) de la branche : là où l'utilisateur voit son brouillon."""
    views = sorted((tmp_path / "work" / str(space.id) / f"{branch_slug}.views").glob("*"))
    dirs = [v for v in views if v.is_dir()]
    assert dirs, "aucune vue de brouillon"
    return dirs[0]


class DraftCollector(SourceCollector):
    """Collecteur qui écrit, par défaut, dans le brouillon de `workdir`."""

    def __init__(
        self, downloader: SourceDownloader, manager: WorkdirManager, workdir: DraftWorkdir
    ) -> None:
        super().__init__(downloader)
        self._workdir = manager
        self.dw = workdir

    async def collect(self, **kwargs: object) -> CollectReport:  # type: ignore[override]
        kwargs.setdefault("workdir", self.dw)
        return await super().collect(**kwargs)  # type: ignore[arg-type]

    async def collect_openapi(self, **kwargs: object) -> CollectReport:  # type: ignore[override]
        kwargs.setdefault("workdir", self.dw)
        return await super().collect_openapi(**kwargs)  # type: ignore[arg-type]

    async def collect_sql(self, **kwargs: object) -> CollectReport:  # type: ignore[override]
        kwargs.setdefault("workdir", self.dw)
        return await super().collect_sql(**kwargs)  # type: ignore[arg-type]
