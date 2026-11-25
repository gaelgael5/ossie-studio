from __future__ import annotations

import re
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


# Un domaine d'email : au moins deux étiquettes (« pickup-services.com »). On ne
# cherche pas l'exhaustivité RFC — juste refuser une saisie qui ne pourra jamais
# correspondre à la partie droite d'une adresse.
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SEPARATORS = re.compile(r"[\s,;]+")
MAX_LOGIN_PROMPT = 120


@dataclass
class LoginRoutingForm:
    email_domains: list[str]
    login_prompt: str
    errors: list[str]


def parse_login_routing(email_domains: str, login_prompt: str) -> LoginRoutingForm:
    """Domaines saisis tels que l'administrateur les écrit (« @pickup-services.com »,
    séparés par des virgules ou des espaces) → liste normalisée sans « @ », en
    minuscules et sans doublon ; codes d'erreur sinon."""
    errors: list[str] = []
    domains: list[str] = []
    for raw in _SEPARATORS.split(email_domains.strip()):
        if not raw:
            continue
        domain = raw.lstrip("@").lower()
        if not _DOMAIN_RE.match(domain):
            if "email_domain_invalid" not in errors:
                errors.append("email_domain_invalid")
            continue
        if domain not in domains:
            domains.append(domain)
    prompt = login_prompt.strip()
    if len(prompt) > MAX_LOGIN_PROMPT:
        errors.append("login_prompt_too_long")
    return LoginRoutingForm(domains, prompt, errors)


def email_domain(email: str) -> str:
    """Partie droite d'une adresse, en minuscules ; "" si l'adresse n'en a pas."""
    _, at, domain = email.strip().rpartition("@")
    return domain.lower() if at else ""
