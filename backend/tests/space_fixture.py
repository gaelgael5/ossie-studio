"""Espace Git de test prêt à l'emploi par l'API : administrateur connecté,
connexion GitHub (fausse forge), dépôt nu local, branche de base créée."""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import bootstrap_admin, wire_fake_idp
from tests.fake_forge import GOOD_TOKEN, fake_github_app


@dataclass
class ApiSpace:
    client: AsyncClient
    space_id: str
    branch_id: str
    url: str
    tmp: Path

    @property
    def base(self) -> str:
        return f"/api/spaces/{self.space_id}/branches/{self.branch_id}"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=f@f", "-c", "user.name=forge", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def seed_bare(tmp: Path) -> str:
    bare = tmp / "repo.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "commit", "-q", "--allow-empty", "-m", "i")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    return f"file://{bare}"


def forge_edit(space: ApiSpace, edits: dict[str, str | None], ref: str = "work/principale") -> None:
    """Fait avancer la branche directement sur la forge (autre contributeur) :
    chemin → contenu, ou None pour supprimer."""
    clone = space.tmp / f"forge-{uuid.uuid4().hex[:6]}"
    git(space.tmp, "clone", "-q", "-b", ref, space.url.removeprefix("file://"), str(clone))
    for rel, text in edits.items():
        target = clone / rel
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "forge")
    git(clone, "push", "-q", "origin", ref)


def forge_read(space: ApiSpace, rel: str, ref: str = "work/principale") -> str | None:
    out = subprocess.run(
        ["git", "--git-dir", space.url.removeprefix("file://"), "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
    )
    return out.stdout if out.returncode == 0 else None


async def make_api_space(client: AsyncClient, app: FastAPI, tmp: Path) -> ApiSpace:
    wire_fake_idp(app, fake_github_app())
    await bootstrap_admin(client)
    await client.post(
        "/api/secrets",
        json={"slug": "gh", "label": "Jeton", "category": "github", "value": GOOD_TOKEN},
    )
    await client.post(
        "/api/connections",
        json={
            "label": "GitHub",
            "forge": "github",
            "url": "https://github.com",
            "secret_slug": "gh",
        },
    )
    connection = (await client.get("/api/connections")).json()["connections"][0]
    (tmp / "git").mkdir(exist_ok=True)
    url = seed_bare(tmp / "git")
    async with app.state.pool.acquire() as conn:
        space_id = await conn.fetchval(
            "INSERT INTO git_spaces (name, connection_id, repo_url, last_probe_status) "
            "VALUES ('modele', $1, $2, 'valid') RETURNING id",
            uuid.UUID(connection["id"]),
            url,
        )
    base = await client.post(f"/api/spaces/{space_id}/branches/base", json={"label": "Principale"})
    assert base.status_code == 201, base.text
    return ApiSpace(client, str(space_id), base.json()["id"], url, tmp)
