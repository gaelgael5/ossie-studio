from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from ossie_studio.spaces.gitcli import classify, default_branch, git_env, run_git, write_askpass

log = structlog.get_logger(__name__)

# Les cinq droits éprouvés, dans l'ordre. Un espace créé sans l'un d'eux
# casserait plus tard, au milieu d'une opération sans rapport apparent.
RIGHT_READ = "read_fetch"
RIGHT_CREATE_BRANCH = "create_branch"
RIGHT_PUSH_COMMIT = "push_commit"
RIGHT_PUSH_TAG = "push_tag"
RIGHT_DELETE_BRANCH = "delete_branch"


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
        askpass = write_askpass(workdir) if token is not None else None
        env = git_env(workdir, askpass, username, token)
        repo = workdir / "repo"
        repo.mkdir()
        await run_git(["init", "-q", "-b", "main"], repo, env)
        await run_git(["remote", "add", "origin", repo_url], repo, env)
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
    code, out, err = await run_git(["ls-remote", "origin"], repo, env)
    if code != 0:
        raise _RightFailure(RIGHT_READ, classify(err))
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
        default = default_branch(heads)
        await _require(repo, env, ["fetch", "-q", "origin", default], RIGHT_READ)
        _, tree, _ = await run_git(["ls-tree", "--name-only", "FETCH_HEAD"], repo, env)
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


async def _require(repo: Path, env: dict[str, str], args: list[str], right: str) -> None:
    code, _, err = await run_git(args, repo, env)
    if code != 0:
        # On renseigne le droit fautif ; la cause est classée par l'appelant.
        raise _RightFailure(right, classify(err))


class _RightFailure(_GitError):
    def __init__(self, right: str, cause: str) -> None:
        super().__init__(cause)
        self.right = right


async def _cleanup(repo: Path, env: dict[str, str], result: ProbeResult) -> None:
    # Tente de retirer la branche et le tag de vérification, puis vérifie.
    await run_git(["push", "-q", "origin", f":refs/tags/{result.verify_tag}"], repo, env)
    await run_git(["push", "-q", "origin", f":refs/heads/{result.verify_branch}"], repo, env)
    code, out, _ = await run_git(["ls-remote", "origin"], repo, env)
    if code != 0:
        return
    refs = out
    if f"refs/heads/{result.verify_branch}" in refs:
        result.residuals.append(f"branch {result.verify_branch}")
    if f"refs/tags/{result.verify_tag}" in refs:
        result.residuals.append(f"tag {result.verify_tag}")
