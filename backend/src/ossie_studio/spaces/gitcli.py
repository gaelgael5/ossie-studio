from __future__ import annotations

import asyncio
import os
from pathlib import Path

# Le jeton n'est remis à git que par l'environnement de son propre processus
# (credential helper), jamais dans l'URL, un argument ou un fichier.
ASKPASS = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *[Uu]sername*) printf '%s' \"$GIT_USERNAME\" ;;\n"
    "  *) printf '%s' \"$GIT_PASSWORD\" ;;\n"
    "esac\n"
)


def write_askpass(directory: Path) -> Path:
    path = directory / "askpass.sh"
    path.write_text(ASKPASS)
    path.chmod(0o700)
    return path


def git_env(home: Path, askpass: Path | None, username: str, token: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if askpass is not None and token is not None:
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_USERNAME"] = username
        env["GIT_PASSWORD"] = token
    return env


async def run_git(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        # Protocole v0 : la négociation v2 échoue sur le transport local ; v0
        # est universellement supporté.
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


async def show_files(
    cwd: Path, env: dict[str, str], rev: str, paths: list[str]
) -> dict[str, str | None]:
    """Contenu de chaque chemin à `rev` (None s'il n'y existe pas), en UN seul
    processus `git cat-file --batch` : une collecte touche des milliers de
    fichiers, un `git show` par fichier coûterait des dizaines de secondes."""
    if not paths:
        return {}
    proc = await asyncio.create_subprocess_exec(
        "git",
        "cat-file",
        "--batch",
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Un chemin contenant un saut de ligne casserait le protocole ligne à ligne :
    # il est simplement donné absent.
    asked = [p for p in paths if "\n" not in p]
    out, _ = await proc.communicate("".join(f"{rev}:{p}\n" for p in asked).encode())
    result: dict[str, str | None] = dict.fromkeys(paths)
    pos = 0
    for path in asked:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode(errors="replace").split()
        pos = end + 1
        if len(header) == 3 and header[2].isdigit():  # objet trouvé (blob, ou dossier)
            size = int(header[2])
            if header[1] == "blob":
                result[path] = out[pos : pos + size].decode("utf-8", "replace")
            pos += size + 1  # contenu puis saut de ligne final
        # « missing » : une seule ligne, rien à sauter
    return result


def classify(stderr: str) -> str:
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


def default_branch(heads: list[str]) -> str:
    for candidate in ("main", "master"):
        if candidate in heads:
            return candidate
    return heads[0]
