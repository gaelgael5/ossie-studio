from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from ossie_studio.spaces.workdir import WorkdirError, WorkdirManager


def _seed_bare(tmp_path: Path, name: str = "repo") -> str:
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / f"seed_{name}"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for args in (
        ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "i"],
        ["remote", "add", "origin", str(bare)],
        ["push", "-q", "origin", "main"],
    ):
        subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)
    return f"file://{bare}"


def _refs(url: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-remote", url.removeprefix("file://")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(line.split("\t")[1] for line in out.splitlines() if "\t" in line)


async def test_create_base_pose_arborescence(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    assert "refs/heads/work/principale" in _refs(url)


async def test_base_existante_rejetee(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    with pytest.raises(WorkdirError) as exc:
        await manager.create_base_branch(space_id, "principale", url, "x", None)
    assert exc.value.code == "branch_exists"


async def test_child_depuis_la_base(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    await manager.create_child_branch(space_id, "principale", "feature-a", url, "x", None)
    assert "refs/heads/work/feature-a" in _refs(url)


async def test_child_sans_base_introuvable(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    with pytest.raises(WorkdirError) as exc:
        await manager.create_child_branch(uuid.uuid4(), "absente", "f", url, "x", None)
    assert exc.value.code == "branch_not_found"


async def test_depot_injoignable_signale_l_acces(tmp_path: Path) -> None:
    manager = WorkdirManager(tmp_path / "vol")
    with pytest.raises(WorkdirError) as exc:
        await manager.create_base_branch(
            uuid.uuid4(), "p", f"file://{tmp_path / 'absent.git'}", "x", None
        )
    assert exc.value.code == "repo_access_lost"


def _write(relpath: str, text: str):
    def write(workdir: Path) -> None:
        target = workdir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    return write


def _advance_forge(tmp_path: Path, url: str, ref: str, name: str) -> None:
    # Fait avancer la branche directement sur la forge (autre contributeur),
    # pour éprouver la réconciliation et le refus de publication.
    bare = url.removeprefix("file://")
    clone = tmp_path / f"other_{name}"
    subprocess.run(["git", "clone", "-q", bare, str(clone)], check=True, capture_output=True)
    for args in (
        ["-c", "protocol.version=0", "fetch", "-q", "origin", ref],
        ["checkout", "-q", "-B", ref, "FETCH_HEAD"],
    ):
        subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True)
    (clone / f"{name}.txt").write_text("forge")
    for args in (
        ["add", "-A"],
        ["-c", "user.email=f@f", "-c", "user.name=f", "commit", "-q", "-m", "forge"],
        ["-c", "protocol.version=0", "push", "-q", "origin", ref],
    ):
        subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True)


def _clone_file(tmp_path: Path, url: str, ref: str, relpath: str, name: str) -> str:
    bare = url.removeprefix("file://")
    clone = tmp_path / f"read_{name}"
    subprocess.run(
        ["git", "clone", "-q", "-b", ref, bare, str(clone)], check=True, capture_output=True
    )
    return (clone / relpath).read_text()


async def test_commit_puis_publie(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)

    created = await manager.commit_to_branch(
        space_id, "principale", url, "x", None,
        "Alice", "alice@example.org", "ajoute data",
        _write("sources/data.txt", "v1"),
    )
    assert created is True

    pending = await manager.list_unpublished(space_id, "principale", url, "x", None)
    assert len(pending) == 1
    assert pending[0].author == "Alice"
    assert pending[0].message == "ossie: ajoute data"

    count = await manager.publish(space_id, "principale", url, "x", None)
    assert count == 1
    # Publié : plus rien en attente, et le contenu est bien sur la forge.
    assert await manager.list_unpublished(space_id, "principale", url, "x", None) == []
    assert _clone_file(tmp_path, url, "work/principale", "sources/data.txt", "a") == "v1"


async def test_commit_vide_jamais_de_commit(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    # Une écriture qui ne change rien ne doit produire aucun commit.
    created = await manager.commit_to_branch(
        space_id, "principale", url, "x", None, "Alice", "a@a", "rien", lambda _wd: None
    )
    assert created is False
    assert await manager.list_unpublished(space_id, "principale", url, "x", None) == []


async def test_publish_rien_a_publier(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    assert await manager.publish(space_id, "principale", url, "x", None) == 0


async def test_realignement_si_rien_en_attente(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    # La forge avance alors qu'aucun commit local n'est en attente : la copie
    # se recale, et un nouveau commit se publie en fast-forward.
    _advance_forge(tmp_path, url, "work/principale", "x")
    created = await manager.commit_to_branch(
        space_id, "principale", url, "x", None, "Alice", "a@a", "après forge",
        _write("sources/data.txt", "v2"),
    )
    assert created is True
    assert await manager.publish(space_id, "principale", url, "x", None) == 1
    assert _clone_file(tmp_path, url, "work/principale", "x.txt", "b") == "forge"


async def test_garde_les_commits_en_attente_et_refuse_si_branche_avancee(
    tmp_path: Path,
) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    # Un commit local en attente, puis la forge avance de son côté.
    await manager.commit_to_branch(
        space_id, "principale", url, "x", None, "Alice", "a@a", "un",
        _write("sources/data.txt", "v1"),
    )
    _advance_forge(tmp_path, url, "work/principale", "x")
    # Un second commit : le premier ne doit pas être perdu par la réconciliation.
    await manager.commit_to_branch(
        space_id, "principale", url, "x", None, "Alice", "a@a", "deux",
        _write("sources/data2.txt", "v2"),
    )
    pending = await manager.list_unpublished(space_id, "principale", url, "x", None)
    assert [c.message for c in pending] == ["ossie: deux", "ossie: un"]
    # La branche a avancé sur la forge : publication refusée, rien de perdu.
    with pytest.raises(WorkdirError) as exc:
        await manager.publish(space_id, "principale", url, "x", None)
    assert exc.value.code == "branch_changed"
    assert len(await manager.list_unpublished(space_id, "principale", url, "x", None)) == 2


async def test_list_unpublished_branche_absente(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    with pytest.raises(WorkdirError) as exc:
        await manager.list_unpublished(uuid.uuid4(), "absente", url, "x", None)
    assert exc.value.code == "branch_not_found"
