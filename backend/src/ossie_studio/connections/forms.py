from __future__ import annotations

from urllib.parse import urlparse

from ossie_studio.forges.registry import is_known_forge


def validate_connection(label: str, forge: str, url: str, token: str) -> list[str]:
    """Valide les champs AVANT tout usage ; renvoie des codes de champ."""
    errors: list[str] = []
    if not label.strip():
        errors.append("label_required")
    if not is_known_forge(forge):
        errors.append("forge_invalid")
    if not url.strip():
        errors.append("url_required")
    elif not _is_http_url(url.strip()):
        errors.append("url_invalid")
    if not token:
        errors.append("token_required")
    return errors


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
