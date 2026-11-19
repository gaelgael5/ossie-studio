from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from ossie_studio.forges.azure import AzureDevOpsStrategy
from ossie_studio.forges.github import GitHubStrategy
from ossie_studio.forges.gitlab import GitLabStrategy
from ossie_studio.sources.download import SourceDownloader, SourceDownloadError


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[str, Path]:
    """Crée un dépôt bare avec une branche main (un commit) et une copie de
    travail pour le faire évoluer. Rend l'URL file:// et la copie."""
    bare = tmp_path / "src.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "a.txt").write_text("un", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "initial")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "origin", "main")
    return f"file://{bare}", work


def _head(work: Path, branch: str) -> str:
    return _git(work, "rev-parse", branch)


@pytest.fixture
def downloader(tmp_path: Path) -> SourceDownloader:
    return SourceDownloader(tmp_path / "volume")


async def test_clone_premiere_descente(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, work = _make_repo(tmp_path)
    space = uuid.uuid4()
    sha = await downloader.download(space, "api", url, "main", "x", None)
    assert sha == _head(work, "main")
    # Le code est présent sur le volume.
    assert (tmp_path / "volume" / str(space) / "api" / "a.txt").read_text() == "un"


async def test_descente_idempotente(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, _ = _make_repo(tmp_path)
    space = uuid.uuid4()
    first = await downloader.download(space, "api", url, "main", "x", None)
    second = await downloader.download(space, "api", url, "main", "x", None)
    assert first == second


async def test_branche_avance(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, work = _make_repo(tmp_path)
    space = uuid.uuid4()
    first = await downloader.download(space, "api", url, "main", "x", None)
    (work / "b.txt").write_text("deux", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "second")
    _git(work, "push", "-q", "origin", "main")
    second = await downloader.download(space, "api", url, "main", "x", None)
    assert second != first
    assert second == _head(work, "main")


async def test_changement_de_branche(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, work = _make_repo(tmp_path)
    _git(work, "checkout", "-q", "-b", "develop")
    (work / "c.txt").write_text("trois", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "dev")
    _git(work, "push", "-q", "origin", "develop")
    space = uuid.uuid4()
    await downloader.download(space, "api", url, "main", "x", None)
    on_dev = await downloader.download(space, "api", url, "develop", "x", None)
    assert on_dev == _head(work, "develop")


async def test_branche_inexistante_refuse(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, _ = _make_repo(tmp_path)
    with pytest.raises(SourceDownloadError) as exc:
        await downloader.download(uuid.uuid4(), "api", url, "absente", "x", None)
    assert exc.value.code == "branch_not_found"


async def test_copie_corrompue_reclone(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, work = _make_repo(tmp_path)
    space = uuid.uuid4()
    await downloader.download(space, "api", url, "main", "x", None)
    # On casse la copie : .git présent mais illisible.
    git_dir = tmp_path / "volume" / str(space) / "api" / ".git"
    shutil.rmtree(git_dir)
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("corrompu", encoding="utf-8")
    sha = await downloader.download(space, "api", url, "main", "x", None)
    assert sha == _head(work, "main")


async def test_suppression_retire_le_code(downloader: SourceDownloader, tmp_path: Path) -> None:
    url, _ = _make_repo(tmp_path)
    space = uuid.uuid4()
    await downloader.download(space, "api", url, "main", "x", None)
    workdir = tmp_path / "volume" / str(space) / "api"
    assert workdir.exists()
    await downloader.remove(space, "api")
    assert not workdir.exists()


async def test_slug_traversant_refuse(downloader: SourceDownloader) -> None:
    with pytest.raises(SourceDownloadError) as exc:
        await downloader.download(uuid.uuid4(), "../evil", "file:///x", "main", "x", None)
    assert exc.value.code == "slug_invalid"


def test_git_clone_url() -> None:
    assert GitHubStrategy().git_clone_url("https://github.com", "org/repo") == (
        "https://github.com/org/repo.git"
    )
    assert GitLabStrategy().git_clone_url("https://gitlab.com/", "grp/proj") == (
        "https://gitlab.com/grp/proj.git"
    )
    assert AzureDevOpsStrategy().git_clone_url("https://dev.azure.com/org", "projet/depot") == (
        "https://dev.azure.com/org/projet/_git/depot"
    )
