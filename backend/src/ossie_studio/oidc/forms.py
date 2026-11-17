from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_SCOPES = "openid email profile"


@dataclass
class ConnectorForm:
    label: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: str
    errors: list[str]


def parse_connector(
    label: str, issuer: str, client_id: str, client_secret: str, scopes: str
) -> ConnectorForm:
    """Valide les champs AVANT tout usage réseau ; messages en clair."""
    errors: list[str] = []
    label = label.strip()
    issuer = issuer.strip().rstrip("/")
    client_id = client_id.strip()
    scopes = scopes.strip() or DEFAULT_SCOPES
    if not label:
        errors.append("Le libellé est obligatoire.")
    if not _is_http_url(issuer):
        errors.append("L'issuer doit être une URL http(s).")
    if not client_id:
        errors.append("Le client_id est obligatoire.")
    if not client_secret:
        errors.append("Le client_secret est obligatoire.")
    if "openid" not in scopes.split():
        errors.append("Les scopes doivent contenir « openid ».")
    return ConnectorForm(label, issuer, client_id, client_secret, scopes, errors)


def _is_http_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
