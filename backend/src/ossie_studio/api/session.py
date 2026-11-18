from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ossie_studio.api.common import user_payload
from ossie_studio.auth.forms import parse_credentials
from ossie_studio.auth.service import (
    BootstrapConflictError,
    db_count_users,
    resolve_bootstrap_admin,
    resolve_login,
)
from ossie_studio.deps import ConnDep, bootstrap_is_done, get_current_user
from ossie_studio.errors import ApiError
from ossie_studio.oidc.service import db_list_connectors
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.users.service import SUPPORTED_LANGUAGES, db_set_language

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = ""
    password: str = ""


class LanguageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = ""


@router.get("/session")
async def session_state(request: Request, conn: ConnDep) -> dict[str, object]:
    """État d'amorçage et d'authentification, pour le routage côté SPA."""
    user = await get_current_user(request, conn)
    bootstrap_done = await bootstrap_is_done(request.app)
    capabilities = sorted(await load_capabilities(conn, user)) if user is not None else []
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
    return [{"id": str(c.id), "label": c.label} for c in connectors]
