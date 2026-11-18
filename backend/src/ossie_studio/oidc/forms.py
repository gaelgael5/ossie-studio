from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_SCOPES = "openid email profile"


@dataclass
class ConnectorForm:
    label: str
    issuer: str
    client_id: str
    scopes: str
    # Codes de champ, traduits par l'interface.
    errors: list[str]


def parse_connector(label: str, issuer: str, client_id: str, scopes: str) -> ConnectorForm:
    """Valide les champs AVANT tout usage réseau ; renvoie des codes. Le client
    secret n'est plus saisi ici : il se choisit par son slug (secret de catégorie
    « oidc »), validé côté API avec la base."""
    errors: list[str] = []
    label = label.strip()
    issuer = issuer.strip().rstrip("/")
    client_id = client_id.strip()
    scopes = scopes.strip() or DEFAULT_SCOPES
    if not label:
        errors.append("label_required")
    if not _is_http_url(issuer):
        errors.append("issuer_invalid")
    if not client_id:
        errors.append("client_id_required")
    if "openid" not in scopes.split():
        errors.append("scopes_openid_required")
    return ConnectorForm(label, issuer, client_id, scopes, errors)


def _is_http_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
