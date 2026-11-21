from __future__ import annotations

import subprocess
from pathlib import Path

from ossie_studio.spaces.git_probe import prove_five_rights


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _bare(path: Path) -> str:
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return f"file://{path}"


def _seed(bare: Path, work: Path, *, with_sources: bool = False) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    if with_sources:
        (work / "sources").mkdir()
        (work / "sources" / "x.txt").write_text("hi")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "init")
    else:
        _git(work, "commit", "-q", "--allow-empty", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "origin", "main")


def _refs(bare: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    return sorted(line.split("\t")[1] for line in out.splitlines() if "\t" in line)


async def test_depot_vide_pose_main_et_passe(tmp_path: Path) -> None:
    url = _bare(tmp_path / "empty.git")
    result = await prove_five_rights(url, username="x", token=None)
    assert result.ok is True
    assert result.posed_main is True
    # Seul effet durable admis : main posée.
    assert _refs(tmp_path / "empty.git") == ["refs/heads/main"]


async def test_depot_avec_contenu_inchange_et_sources_detecte(tmp_path: Path) -> None:
    bare = tmp_path / "full.git"
    _bare(bare)
    _seed(bare, tmp_path / "wd", with_sources=True)
    before = _refs(bare)
    result = await prove_five_rights(f"file://{bare}", username="x", token=None)
    assert result.ok is True
    assert result.has_sources is True
    assert result.found_branches == 1
    # L'état du dépôt après l'essai est identique à avant.
    assert _refs(bare) == before


async def test_branche_protegee_nomme_le_droit(tmp_path: Path) -> None:
    bare = tmp_path / "ro.git"
    _bare(bare)
    _seed(bare, tmp_path / "wdro")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
    hook.chmod(0o755)
    result = await prove_five_rights(f"file://{bare}", username="x", token=None)
    assert result.ok is False
    assert result.failed_right == "create_branch"
    assert result.cause == "branch_protected"


async def test_depot_inexistant(tmp_path: Path) -> None:
    result = await prove_five_rights(
        f"file://{tmp_path / 'nexiste-pas.git'}", username="x", token=None
    )
    assert result.ok is False
    assert result.failed_right == "read_fetch"
    assert result.cause == "repo_not_found"


async def test_suppression_refusee_affiche_les_residus(tmp_path: Path) -> None:
    """Un dépôt qui accepte les créations mais refuse les suppressions laisse
    la branche et le tag de vérification : leurs noms sont affichés."""
    bare = tmp_path / "nodelete.git"
    _bare(bare)
    _seed(bare, tmp_path / "wdnd")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  if [ "$new" = "0000000000000000000000000000000000000000" ]; then\n'
        "    echo 'deletion denied' >&2; exit 1\n"
        "  fi\n"
        "done\n"
    )
    hook.chmod(0o755)
    result = await prove_five_rights(f"file://{bare}", username="x", token=None)
    assert result.ok is False
    assert result.failed_right == "delete_branch"
    assert any("branch" in r for r in result.residuals)
    assert any("tag" in r for r in result.residuals)
