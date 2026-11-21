from __future__ import annotations

import uuid
from urllib.parse import urlparse


def validate_space(name: str, connection_id: str, repo_url: str) -> list[str]:
    """Valide les champs AVANT tout usage ; renvoie des codes de champ."""
    errors: list[str] = []
    if not name.strip():
        errors.append("space_name_required")
    if not _is_uuid(connection_id):
        errors.append("connection_required")
    if not repo_url.strip():
        errors.append("repo_url_required")
    elif not _is_http_url(repo_url.strip()):
        errors.append("repo_url_invalid")
    return errors


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
