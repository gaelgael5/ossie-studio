"""Liens web vers un fichier d'un dépôt, à une ligne, selon la forge.

Pur calcul d'adresse : aucun appel réseau. Une forge inconnue rend None (le
raccourci « Ouvrir dans la forge » est alors absent plutôt que faux).
"""

from __future__ import annotations

from urllib.parse import quote

_WEB_GITHUB = "https://github.com"


def _github_web_base(base_url: str) -> str:
    # Une connexion GitHub peut porter l'URL d'API : le site est à côté.
    url = base_url.rstrip("/") or _WEB_GITHUB
    if url in ("https://api.github.com", "http://api.github.com"):
        return _WEB_GITHUB
    return url.removesuffix("/api/v3")


def forge_file_url(
    forge: str, base_url: str, repo: str, ref: str, path: str, line: int | None = None
) -> str | None:
    """Adresse web de `path` sur la branche `ref` du dépôt `repo` (`org/depot`,
    ou `projet/depot` sur Azure DevOps)."""
    if not repo or not path:
        return None
    safe_path = quote(path.lstrip("/"))
    safe_ref = quote(ref, safe="")
    if forge == "github":
        anchor = f"#L{line}" if line else ""
        return f"{_github_web_base(base_url)}/{repo}/blob/{safe_ref}/{safe_path}{anchor}"
    if forge == "gitlab":
        anchor = f"#L{line}" if line else ""
        base = base_url.rstrip("/") or "https://gitlab.com"
        return f"{base}/{repo}/-/blob/{safe_ref}/{safe_path}{anchor}"
    if forge == "azure_devops":
        project, _, name = repo.partition("/")
        url = (
            f"{base_url.rstrip('/')}/{quote(project)}/_git/{quote(name or project)}"
            f"?path=/{safe_path}&version=GB{safe_ref}"
        )
        if line:
            url += f"&line={line}&lineEnd={line}&lineStartColumn=1&lineEndColumn=1"
        return url
    return None
