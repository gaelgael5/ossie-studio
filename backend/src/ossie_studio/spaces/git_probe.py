from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Les cinq droits éprouvés, dans l'ordre. Un espace créé sans l'un d'eux
# casserait plus tard, au milieu d'une opération sans rapport apparent.
RIGHT_READ = "read_fetch"
RIGHT_CREATE_BRANCH = "create_branch"
RIGHT_PUSH_COMMIT = "push_commit"
RIGHT_PUSH_TAG = "push_tag"
RIGHT_DELETE_BRANCH = "delete_branch"

_ASKPASS = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *[Uu]sername*) printf '%s' \"$GIT_USERNAME\" ;;\n"
    "  *) printf '%s' \"$GIT_PASSWORD\" ;;\n"
    "esac\n"
)


@dataclass
class ProbeResult:
    ok: bool
    verify_branch: str
    verify_tag: str
    failed_right: str | None = None
    cause: str | None = None
    # Noms restés sur la forge que le nettoyage n'a pas pu retirer.
    residuals: list[str] = field(default_factory=list)
    posed_main: bool = False
    found_branches: int = 0
    found_tags: int = 0
    has_sources: bool = False


class _GitError(Exception):
    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


def _classify(stderr: str) -> str:
    """Traduit l'erreur git en cause stable (jamais « accès insuffisant »)."""
    s = stderr.lower()
    if "could not resolve host" in s or "connection refused" in s or "unable to access" in s:
        return "forge_unreachable"
    if "authentication failed" in s or "403" in s or "could not read username" in s:
        return "auth_failed"
    if (
        "not found" in s
        or "repository not found" in s
        or "does not exist" in s
        or "404" in s
        or "not a git repository" in s
        or "does not appear to be a git repository" in s
    ):
        return "repo_not_found"
    if "protected" in s and "tag" in s:
        return "tag_protected"
    if "protected" in s or "pre-receive hook declined" in s or "gh006" in s:
        return "branch_protected"
    if "denied" in s or "permission" in s or "write access" in s or "read-only" in s:
        return "write_denied"
    return "git_failed"


def _git_env(workdir: Path, askpass: Path | None, username: str, token: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if askpass is not None:
        # Le jeton n'est remis à git que par l'environnement de son propre
        # processus, jamais dans l'URL, un argument ou un fichier.
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_USERNAME"] = username
        env["GIT_PASSWORD"] = token
    return env


async def _run_git(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        # Protocole v0 : la négociation v2 échoue sur le transport local des
        # tests ; v0 est universellement supporté et suffisant pour une sonde.
        "-c",
        "protocol.version=0",
        "-c",
        "user.email=ossie@studio.local",
        "-c",
        "user.name=Ossie Studio",
        *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def prove_five_rights(
    repo_url: str, *, username: str, token: str | None, prefix: str = "ossie-verify"
) -> ProbeResult:
    """Éprouve réellement les cinq droits sur le dépôt. Interroger les
    permissions déclarées ne prouverait rien : seul l'essai le dit."""
    suffix = secrets.token_hex(4)
    branch = f"{prefix}/{suffix}"
    tag = f"{prefix}-{suffix}"
    result = ProbeResult(ok=False, verify_branch=branch, verify_tag=tag)

    with tempfile.TemporaryDirectory(prefix="ossie-probe-") as tmp:
        workdir = Path(tmp)
        askpass: Path | None = None
        if token is not None:
            askpass = workdir / "askpass.sh"
            askpass.write_text(_ASKPASS)
            askpass.chmod(0o700)
        env = _git_env(workdir, askpass, username, token or "")
        repo = workdir / "repo"
        repo.mkdir()
        await _run_git(["init", "-q", "-b", "main"], repo, env)
        await _run_git(["remote", "add", "origin", repo_url], repo, env)
        try:
            await _probe_steps(repo, env, result)
        except _RightFailure as exc:
            result.failed_right = exc.right
            result.cause = exc.cause
            await _cleanup(repo, env, result)
            return result
    result.ok = True
    return result


async def _probe_steps(repo: Path, env: dict[str, str], result: ProbeResult) -> None:
    # 1. Lire et récupérer.
    code, out, err = await _run_git(["ls-remote", "origin"], repo, env)
    if code != 0:
        raise _RightFailure(RIGHT_READ, _classify(err))
    refs = [line.split("\t")[1] for line in out.splitlines() if "\t" in line]
    heads = [r.removeprefix("refs/heads/") for r in refs if r.startswith("refs/heads/")]
    result.found_branches = len(heads)
    result.found_tags = sum(1 for r in refs if r.startswith("refs/tags/"))

    if not refs:
        await _require(
            repo, env, ["commit", "-q", "--allow-empty", "-m", "init"], RIGHT_PUSH_COMMIT
        )
        await _require(repo, env, ["push", "-q", "origin", "HEAD:main"], RIGHT_PUSH_COMMIT)
        result.posed_main = True
        base = "HEAD"
    else:
        default = _default_branch(heads)
        await _require(repo, env, ["fetch", "-q", "origin", default], RIGHT_READ)
        _, tree, _ = await _run_git(["ls-tree", "--name-only", "FETCH_HEAD"], repo, env)
        result.has_sources = "sources" in tree.split()
        base = "FETCH_HEAD"

    # 2. Créer une branche (depuis le commit de base) et la pousser.
    await _require(
        repo, env, ["checkout", "-q", "-b", result.verify_branch, base], RIGHT_CREATE_BRANCH
    )
    await _require(repo, env, ["push", "-q", "origin", result.verify_branch], RIGHT_CREATE_BRANCH)
    # 3. Pousser un commit.
    await _require(repo, env, ["commit", "-q", "--allow-empty", "-m", "verify"], RIGHT_PUSH_COMMIT)
    await _require(repo, env, ["push", "-q", "origin", result.verify_branch], RIGHT_PUSH_COMMIT)
    # 4. Poser un tag de version.
    await _require(repo, env, ["tag", "-a", result.verify_tag, "-m", "verify"], RIGHT_PUSH_TAG)
    await _require(repo, env, ["push", "-q", "origin", result.verify_tag], RIGHT_PUSH_TAG)
    # 5. Supprimer la branche (et le tag de vérification).
    await _require(
        repo, env, ["push", "-q", "origin", f":refs/tags/{result.verify_tag}"], RIGHT_DELETE_BRANCH
    )
    await _require(
        repo,
        env,
        ["push", "-q", "origin", f":refs/heads/{result.verify_branch}"],
        RIGHT_DELETE_BRANCH,
    )


def _default_branch(heads: list[str]) -> str:
    for candidate in ("main", "master"):
        if candidate in heads:
            return candidate
    return heads[0]


async def _require(repo: Path, env: dict[str, str], args: list[str], right: str) -> None:
    code, _, err = await _run_git(args, repo, env)
    if code != 0:
        # On renseigne le droit fautif ; la cause est classée par l'appelant.
        raise _RightFailure(right, _classify(err))


class _RightFailure(_GitError):
    def __init__(self, right: str, cause: str) -> None:
        super().__init__(cause)
        self.right = right


async def _cleanup(repo: Path, env: dict[str, str], result: ProbeResult) -> None:
    # Tente de retirer la branche et le tag de vérification, puis vérifie.
    await _run_git(["push", "-q", "origin", f":refs/tags/{result.verify_tag}"], repo, env)
    await _run_git(["push", "-q", "origin", f":refs/heads/{result.verify_branch}"], repo, env)
    code, out, _ = await _run_git(["ls-remote", "origin"], repo, env)
    if code != 0:
        return
    refs = out
    if f"refs/heads/{result.verify_branch}" in refs:
        result.residuals.append(f"branch {result.verify_branch}")
    if f"refs/tags/{result.verify_tag}" in refs:
        result.residuals.append(f"tag {result.verify_tag}")
