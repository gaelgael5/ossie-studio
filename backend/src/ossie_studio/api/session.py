from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import user_payload
from ossie_studio.auth.forms import parse_credentials
from ossie_studio.auth.service import (
    BootstrapConflictError,
    db_count_users,
    db_get_user_by_email,
    resolve_bootstrap_admin,
    resolve_login,
)
from ossie_studio.deps import ConnDep, bootstrap_is_done, get_current_user
from ossie_studio.errors import ApiError
from ossie_studio.models import OidcConnector
from ossie_studio.oidc.forms import email_domain
from ossie_studio.oidc.service import db_connectors_for_domain, db_list_connectors
from ossie_studio.profiles.service import load_capabilities, load_capabilities_by_space
from ossie_studio.users.service import SUPPORTED_LANGUAGES, db_set_language

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = ""
    password: str = ""


class EmailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = ""


class LanguageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = ""


@router.get("/session")
async def session_state(request: Request, conn: ConnDep) -> dict[str, object]:
    """État d'amorçage et d'authentification, pour le routage côté SPA."""
    user = await get_current_user(request, conn)
    bootstrap_done = await bootstrap_is_done(request.app)
    capabilities = sorted(await load_capabilities(conn, user)) if user is not None else []
    # Capacités gagnées sur au moins un espace par rattachement d'équipe : le menu
    # montre « Espaces » à qui n'a ce droit que par une équipe.
    space_capabilities: set[str] = set()
    if user is not None:
        for caps in (await load_capabilities_by_space(conn, user.id)).values():
            space_capabilities |= caps
    # Langue enregistrée sur le compte ; une valeur inconnue → "" (l'interface
    # retombe alors sur le socle i18n, c.-à-d. l'anglais à défaut).
    language = ""
    if user is not None and user.language in SUPPORTED_LANGUAGES:
        language = user.language
    return {
        "bootstrap_required": not bootstrap_done,
        "authenticated": user is not None,
        "user": user_payload(user) if user is not None else None,
        "capabilities": capabilities,
        "space_capabilities": sorted(space_capabilities),
        "language": language,
    }


@router.put("/session/language")
async def set_language(request: Request, conn: ConnDep, body: LanguageBody) -> dict[str, bool]:
    # Un utilisateur ne modifie que la langue de SON compte (celui de la session).
    user = await get_current_user(request, conn)
    if user is None:
        raise ApiError("unauthenticated", status_code=401)
    if body.language not in SUPPORTED_LANGUAGES:
        raise ApiError("invalid_request", status_code=400, fields=["language_invalid"])
    await db_set_language(conn, user.id, body.language)
    return {"ok": True}


@router.post("/setup")
async def setup(request: Request, conn: ConnDep, body: Credentials) -> dict[str, object]:
    # Dès qu'un compte existe, l'amorçage n'est plus possible.
    if await db_count_users(conn) > 0:
        raise ApiError("admin_already_exists", status_code=409)
    form = parse_credentials(body.email, body.password)
    if form.errors:
        raise ApiError("invalid_request", status_code=400, fields=form.errors)
    try:
        user = await resolve_bootstrap_admin(conn, form.email, form.password)
    except BootstrapConflictError:
        raise ApiError("admin_already_exists", status_code=409) from None
    request.app.state.bootstrap_done = True
    request.session["user_id"] = str(user.id)
    return {"user": user_payload(user)}


@router.post("/login")
async def login(request: Request, conn: ConnDep, body: Credentials) -> dict[str, object]:
    user = await resolve_login(conn, body.email.strip(), body.password)
    if user is None:
        raise ApiError("invalid_credentials", status_code=401)
    request.session["user_id"] = str(user.id)
    log.info("login_succeeded", user_id=str(user.id))
    return {"user": user_payload(user)}


@router.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/auth/connectors")
async def public_connectors(conn: ConnDep) -> list[dict[str, str]]:
    """Connecteurs proposés sur l'écran de connexion : libellé seulement,
    jamais de secret."""
    connectors = await db_list_connectors(conn)
    return [_public(c) for c in connectors]


def _public(c: OidcConnector) -> dict[str, str]:
    return {"id": str(c.id), "label": c.label, "prompt": c.login_prompt}


@router.post("/auth/login/analyze")
async def analyze_login_email(conn: ConnDep, body: EmailBody) -> dict[str, object]:
    """Premier temps de la connexion (ticket 145b1414) : d'après l'email, dire à
    l'écran quoi demander ensuite.

    - domaine inconnu → « password », que le compte existe ou non : l'écran ne
      doit pas révéler qui a un compte (l'erreur reste générique à la saisie du
      mot de passe) ;
    - domaine d'un connecteur, compte sans mot de passe local → « oidc » ;
    - domaine d'un connecteur ET compte avec mot de passe local (ex. le premier
      administrateur) → « password_oidc » : formulaire du mot de passe, avec les
      phrases des connecteurs dessous.

    Écart assumé, décidé par Gael : dans un domaine fédéré, la réponse distingue
    les comptes qui ont un mot de passe local. Ce sont des comptes de secours,
    rares, et la connexion par mot de passe garde son temps constant.
    """
    connectors = await db_connectors_for_domain(conn, email_domain(body.email))
    if not connectors:
        return {"next": "password", "connectors": []}
    user = await db_get_user_by_email(conn, body.email.strip())
    has_password = user is not None and bool(user.password_hash)
    return {
        "next": "password_oidc" if has_password else "oidc",
        "connectors": [_public(c) for c in connectors],
    }
