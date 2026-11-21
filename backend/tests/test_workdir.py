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


def _push_tag(tmp_path: Path, url: str, tagname: str, annotated: bool, name: str) -> None:
    # Pose un tag « à la main » sur la tête de la racine, sur la forge.
    bare = url.removeprefix("file://")
    clone = tmp_path / f"tagger_{name}"
    subprocess.run(["git", "clone", "-q", bare, str(clone)], check=True, capture_output=True)
    make = (
        ["-c", "user.email=t@t", "-c", "user.name=t", "tag", "-a", tagname, "-m", "manuel", "HEAD"]
        if annotated
        else ["tag", tagname, "HEAD"]
    )
    push = ["-c", "protocol.version=0", "push", "-q", "origin", f"refs/tags/{tagname}"]
    for args in (make, push):
        subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True)


async def test_tag_version_cree_un_tag_annote(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    created = await manager.tag_version(
        space_id, "principale", url, "x", None, "Première", "Alice", "alice@example.org"
    )
    assert created.number == 1
    assert created.already_exists is False
    assert "refs/tags/version/1" in _refs(url)

    listed = await manager.list_versions(space_id, "principale", url, "x", None)
    assert len(listed) == 1
    assert listed[0].number == 1
    assert listed[0].label == "Première"
    assert listed[0].author == "Alice"


async def test_tag_version_rien_a_figer(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    await manager.tag_version(space_id, "principale", url, "x", None, "A", "Alice", "a@a")
    # Même tête, autre libellé : rien de neuf à figer.
    with pytest.raises(WorkdirError) as exc:
        await manager.tag_version(space_id, "principale", url, "x", None, "B", "Alice", "a@a")
    assert exc.value.code == "nothing_to_version"


async def test_tag_version_rejeu_signale(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    await manager.tag_version(space_id, "principale", url, "x", None, "A", "Alice", "a@a")
    # Même tête, même libellé : sans effet, signalé.
    again = await manager.tag_version(space_id, "principale", url, "x", None, "A", "Alice", "a@a")
    assert again.number == 1
    assert again.already_exists is True


async def test_tag_version_numero_suit_le_depot(tmp_path: Path) -> None:
    url = _seed_bare(tmp_path)
    # Un tag version/5 posé à la main (léger) : le numéro suivant suit le dépôt,
    # et le tag léger est ignoré au listage.
    _push_tag(tmp_path, url, "version/5", annotated=False, name="m5")
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    created = await manager.tag_version(
        space_id, "principale", url, "x", None, "Six", "Alice", "a@a"
    )
    assert created.number == 6
    listed = await manager.list_versions(space_id, "principale", url, "x", None)
    assert [v.number for v in listed] == [6]


async def test_tag_version_numero_pris_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    await manager.tag_version(space_id, "principale", url, "x", None, "A", "Alice", "a@a")
    # La tête avance sur la forge (sinon « rien à figer »).
    _advance_forge(tmp_path, url, "work/principale", "x")

    async def _stale(*_args: object, **_kwargs: object) -> list[int]:
        return []  # vue périmée : le manager croit que le numéro 1 est libre

    monkeypatch.setattr(manager, "_remote_version_numbers", _stale)
    with pytest.raises(WorkdirError) as exc:
        await manager.tag_version(space_id, "principale", url, "x", None, "B", "Alice", "a@a")
    assert exc.value.code == "version_taken"
    # Le tag reste tel quel sur la forge (push sans force, aucune réécriture).
    assert "refs/tags/version/1" in _refs(url)




async def test_copie_de_lecture_jette_les_commits_locaux_herites(tmp_path: Path) -> None:
    # Transition depuis l'ancien modèle : une copie qui porte un commit local non
    # publié est réalignée sur la forge (le commit est jeté, et journalisé).
    url = _seed_bare(tmp_path)
    manager = WorkdirManager(tmp_path / "vol")
    space_id = uuid.uuid4()
    await manager.create_base_branch(space_id, "principale", url, "x", None)
    copy = manager.copy_dir(space_id, "principale")
    (copy / "local.txt").write_text("hérité")
    _local_commit(copy)
    _advance_forge(tmp_path, url, "work/principale", "x")
    seen = await manager.read_branch(
        space_id, "principale", url, "x", None, lambda wc: sorted(p.name for p in wc.iterdir())
    )
    assert "local.txt" not in seen
    assert "x.txt" in seen  # la tête distante est bien là


def _local_commit(copy: Path) -> None:
    for args in (
        ["add", "-A"],
        ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "-m", "hérité"],
    ):
        subprocess.run(["git", "-C", str(copy), *args], check=True, capture_output=True)
