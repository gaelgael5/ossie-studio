"""Espace Git de test prêt à l'emploi par l'API : administrateur connecté,
connexion GitHub (fausse forge), dépôt nu local, branche de base créée."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.crypto import SecretValue
from ossie_studio.drafts.workdir import DraftWorkdir
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


async def admin_workdir(app: FastAPI, space: ApiSpace) -> DraftWorkdir:
    async with app.state.pool.acquire() as conn:
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    return DraftWorkdir(
        app.state.workdir_manager,
        app.state.draft_views,
        app.state.pool,
        admin_id,
        uuid.UUID(space.branch_id),
    )


async def draft_write(app: FastAPI, space: ApiSpace, rel: str, value: object | None) -> None:
    """Écrit (ou supprime, None) un objet JSON dans le brouillon de l'administrateur."""
    workdir = await admin_workdir(app, space)

    def write(root: Path) -> None:
        target = root / rel
        if value is None:
            target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))

    await workdir.commit_to_branch(
        uuid.UUID(space.space_id), "principale", space.url, "x", None, "a", "a", "écrit", write
    )


async def set_credential(
    app: FastAPI, space: ApiSpace, status: str = "valide", email: str | None = None
) -> None:
    """Identifiant personnel de l'administrateur (ou de `email`) dans l'état voulu."""
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT id FROM users WHERE is_bootstrap"
            if email is None
            else "SELECT id FROM users WHERE email = $1",
            *([] if email is None else [email]),
        )
        connection_id = await conn.fetchval(
            "SELECT connection_id FROM git_spaces WHERE id = $1", uuid.UUID(space.space_id)
        )
        value = None if status == "a_saisir" else app.state.crypto.encrypt(SecretValue(GOOD_TOKEN))
        await conn.execute(
            "INSERT INTO personal_credentials (user_id, connection_id, value_encrypted, status) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id, connection_id) DO UPDATE SET "
            "value_encrypted = EXCLUDED.value_encrypted, status = EXCLUDED.status",
            user_id,
            connection_id,
            value,
            status,
        )


class Rebased:
    """Client qui redirige les anciennes routes globales `/api/governance/…`
    vers celles de la branche de l'espace (la gouvernance vit dans le dépôt)."""

    def __init__(self, client: AsyncClient, space: ApiSpace) -> None:
        self.client = client
        self.space = space

    def _url(self, url: str) -> str:
        return url.replace("/api/governance/", f"{self.space.base}/governance/", 1)

    async def get(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return await self.client.get(self._url(url), **kwargs)  # type: ignore[arg-type]

    async def post(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return await self.client.post(self._url(url), **kwargs)  # type: ignore[arg-type]

    async def put(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return await self.client.put(self._url(url), **kwargs)  # type: ignore[arg-type]

    async def delete(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return await self.client.delete(self._url(url), **kwargs)  # type: ignore[arg-type]
