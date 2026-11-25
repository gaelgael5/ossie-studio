from __future__ import annotations

from fastapi import Request

# Nom de la route du callback OIDC (unique, sans identifiant de connecteur).
CALLBACK_ROUTE = "oidc_callback"


def public_callback_url(request: Request) -> str:
    """URL de retour OIDC à déclarer chez le fournisseur.

    **Unique pour tous les connecteurs** : le connecteur est retrouvé via l'état
    gardé en session, pas par l'URL. Elle vient de l'adresse publique configurée
    (`public_base_url`) ou, à défaut, de la requête entrante.
    """
    base = (getattr(request.app.state.settings, "public_base_url", "") or "").strip().rstrip("/")
    if base:
        return f"{base}/auth/oidc/callback"
    return str(request.url_for(CALLBACK_ROUTE))


def safe_next(value: str) -> str | None:
    """Chemin où revenir après la connexion, s'il reste dans l'application
    (un chemin absolu local ; jamais une autre origine : pas de redirection
    ouverte)."""
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if any(ord(ch) < 0x20 for ch in value):
        return None
    return value
