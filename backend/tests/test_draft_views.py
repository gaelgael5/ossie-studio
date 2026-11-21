"""Vue éphémère : toute écriture va au brouillon (aucun commit), l'auteur voit ses
modifications et lui seul, la vue se reconstruit à l'identique depuis la base."""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from ossie_studio.crypto import SecretValue
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.spaces.views import DraftViewManager
from tests.draft_helpers import draft_workdir


def _seed_bare(tmp_path: Path) -> str:
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for args in (
        ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "i"],
        ["remote", "add", "origin", str(bare)],
        ["push", "-q", "origin", "main"],
    ):
        subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)
    return f"file://{bare}"


def _remote_head(url: str, ref: str = "refs/heads/work/principale") -> str:
    out = subprocess.run(
        ["git", "ls-remote", url.removeprefix("file://"), ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("\t")[0]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _publish_file(tmp_path: Path, url: str, name: str, text: str) -> None:
    """Un fichier publié sur la forge par un autre clone."""
    clone = tmp_path / f"autre-{name}"
    _git(tmp_path, "clone", "-q", "-b", "work/principale", url.removeprefix("file://"), str(clone))
    (clone / name).write_text(text)
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", name)
    _git(clone, "push", "-q", "origin", "work/principale")


def _write(rel: str, text: str) -> Callable[[Path], None]:
    def fn(root: Path) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    return fn


def _read(rel: str) -> Callable[[Path], str | None]:
    def fn(root: Path) -> str | None:
        target = root / rel
        return target.read_text() if target.exists() else None

    return fn


async def _setup(app: FastAPI, tmp_path: Path) -> tuple[DraftWorkdir, object, str]:
    url = _seed_bare(tmp_path)
    manager, workdir, space = await draft_workdir(app, tmp_path, url)
    await manager.create_base_branch(space.id, "principale", url, "x", None)
    return workdir, space, url


async def _commit(workdir: DraftWorkdir, space: object, fn: Callable[[Path], None]) -> str | None:
    return await workdir.commit_to_branch(
        space.id,
        "principale",
        space.repo_url,
        "x",
        None,
        "a@a",
        "a@a",
        "écrit",
        fn,  # type: ignore[attr-defined]
    )


async def _read_as(workdir: DraftWorkdir, space: object, rel: str) -> str | None:
    return await workdir.read_branch(
        space.id,
        "principale",
        space.repo_url,
        "x",
        None,
        _read(rel),  # type: ignore[attr-defined]
    )


async def test_ecriture_va_au_brouillon_sans_commit(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, url = await _setup(app, tmp_path)
    head = _remote_head(url)
    marker = await _commit(workdir, space, _write("sources/a.yaml", "v1"))
    assert marker == "draft:1"
    assert _remote_head(url) == head  # rien n'est poussé
    copy = tmp_path / "work" / str(space.id) / "principale"  # type: ignore[attr-defined]
    copy_head = _git(copy, "rev-parse", "HEAD")
    assert copy_head == head  # la copie de lecture ne porte jamais de commit local
    async with app.state.pool.acquire() as conn:
        draft = await db_get_draft(conn, workdir.user_id, workdir.branch_id)
        assert draft is not None
        objects = await db_list_objects(conn, draft.id)
    assert [(o.path, o.content) for o in objects] == [("sources/a.yaml", b"v1")]


async def test_ecriture_sans_changement_rend_none(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, _url = await _setup(app, tmp_path)
    await _commit(workdir, space, _write("sources/a.yaml", "v1"))
    assert await _commit(workdir, space, _write("sources/a.yaml", "v1")) is None


async def test_auteur_voit_ses_modifs_un_autre_non(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, _url = await _setup(app, tmp_path)
    await _commit(workdir, space, _write("sources/a.yaml", "v1"))
    assert await _read_as(workdir, space, "sources/a.yaml") == "v1"
    async with app.state.pool.acquire() as conn:
        other_id = await conn.fetchval(
            "INSERT INTO users (email, role) VALUES ('autre@x.org', 'user') RETURNING id"
        )
    other = DraftWorkdir(
        workdir._manager, workdir._views, app.state.pool, other_id, workdir.branch_id
    )
    assert await _read_as(other, space, "sources/a.yaml") is None


async def test_creer_modifier_supprimer_superposes(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, url = await _setup(app, tmp_path)
    # Un fichier publié par un autre clone, que le brouillon va supprimer.
    _publish_file(tmp_path, url, "publie.txt", "p")

    def edit(root: Path) -> None:
        (root / "neuf.txt").write_text("n")
        (root / "publie.txt").unlink()

    await _commit(workdir, space, edit)
    assert await _read_as(workdir, space, "neuf.txt") == "n"
    assert await _read_as(workdir, space, "publie.txt") is None
    published = await workdir.read_branch(
        space.id,
        "principale",
        url,
        "x",
        None,
        _read("publie.txt"),
        published=True,  # type: ignore[attr-defined]
    )
    assert published == "p"


async def test_deux_ecritures_concurrentes_meme_utilisateur_sans_perte(
    app: FastAPI, tmp_path: Path
) -> None:
    workdir, space, _url = await _setup(app, tmp_path)
    await asyncio.gather(
        _commit(workdir, space, _write("sources/a.yaml", "A")),
        _commit(workdir, space, _write("sources/b.yaml", "B")),
    )
    async with app.state.pool.acquire() as conn:
        draft = await db_get_draft(conn, workdir.user_id, workdir.branch_id)
        assert draft is not None
        paths = {o.path for o in await db_list_objects(conn, draft.id)}
    assert paths == {"sources/a.yaml", "sources/b.yaml"}


async def test_vue_purgee_puis_reconstruite_a_l_identique(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, _url = await _setup(app, tmp_path)
    await _commit(workdir, space, _write("sources/a.yaml", "v1"))
    views: DraftViewManager = workdir._views
    assert await views.purge_idle(max_idle_seconds=0) == 1
    view_root = tmp_path / "work" / str(space.id) / "principale.views"  # type: ignore[attr-defined]
    assert not [p for p in view_root.glob("*") if p.is_dir()]
    assert await _read_as(workdir, space, "sources/a.yaml") == "v1"


async def test_ecriture_garde_le_brouillon_apres_purge(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, _url = await _setup(app, tmp_path)
    await _commit(workdir, space, _write("sources/a.yaml", "A"))
    await workdir._views.purge_idle(max_idle_seconds=0)
    await _commit(workdir, space, _write("sources/b.yaml", "B"))
    assert await _read_as(workdir, space, "sources/a.yaml") == "A"


async def test_brouillon_de_500_objets_lecture(app: FastAPI, tmp_path: Path) -> None:
    workdir, space, _url = await _setup(app, tmp_path)

    def many(root: Path) -> None:
        folder = root / "sources" / "api" / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(500):
            (folder / f"o{i}.json").write_text(f'{{"i": {i}}}')

    started = time.perf_counter()
    await _commit(workdir, space, many)
    write_s = time.perf_counter() - started
    started = time.perf_counter()
    count = await workdir.read_branch(
        space.id,
        "principale",
        space.repo_url,
        "x",
        None,  # type: ignore[attr-defined]
        lambda root: len(list((root / "sources" / "api" / "raw").glob("*.json"))),
    )
    read_s = time.perf_counter() - started
    assert count == 500
    print(f"brouillon 500 objets : écriture {write_s:.2f}s, lecture {read_s:.2f}s")
    assert read_s < 5


async def test_ecriture_api_sans_capacite_403_avec_rattachement_201(
    app: FastAPI, tmp_path: Path
) -> None:
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import create_plain_user, login

    workdir, space, _url = await _setup(app, tmp_path)
    await create_plain_user(app, "contrib@x.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('L', $1) RETURNING id",
            ["spaces:read", "sources:read"],
        )
        writer = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) VALUES ('W', $1) RETURNING id",
            ["sources:declare"],
        )
        user_id = await conn.fetchval(
            "UPDATE users SET profile_id = $1 WHERE email = 'contrib@x.org' RETURNING id", reader
        )
        team = await conn.fetchval(
            "INSERT INTO teams (name, slug, origin) VALUES ('t', 't', 'manual') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO user_teams (user_id, team_id, origin) VALUES ($1, $2, 'manual')",
            user_id,
            team,
        )
    async with app.state.pool.acquire() as conn:
        # La route déchiffre le jeton de la connexion de l'espace : un vrai chiffré.
        await conn.execute(
            "UPDATE secrets SET value_encrypted = $1 WHERE slug = "
            "(SELECT secret_slug FROM connections WHERE id = $2)",
            app.state.crypto.encrypt(SecretValue("jeton")),
            space.connection_id,  # type: ignore[attr-defined]
        )
    body = {"slug": "api-x", "label": "API X", "type": "openapi"}
    url = f"/api/spaces/{space.id}/branches/{workdir.branch_id}/sources"  # type: ignore[attr-defined]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "contrib@x.org", "motdepasse-solide")
        app.state.workdir_manager = workdir._manager
        app.state.draft_views = workdir._views
        assert (await client.post(url, json=body)).status_code == 403
        async with app.state.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO space_team_profiles (space_id, team_id, profile_id) "
                "VALUES ($1, $2, $3)",
                space.id,
                team,
                writer,  # type: ignore[attr-defined]
            )
        created = await client.post(url, json=body)
    assert created.status_code == 201, created.text
    assert uuid.UUID(str(user_id))
