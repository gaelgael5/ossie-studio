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
