from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ossie_studio.auth.forms import parse_credentials
from ossie_studio.auth.service import (
    BootstrapConflictError,
    resolve_bootstrap_admin,
    resolve_login,
)
from ossie_studio.deps import bootstrap_is_done, get_session, templates

log = structlog.get_logger(__name__)

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _reject_if_bootstrap_done(request: Request) -> None:
    """Dès qu'un compte existe, l'écran de bootstrap n'existe plus."""
    if await bootstrap_is_done(request.app):
        raise HTTPException(status_code=404)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> Response:
    await _reject_if_bootstrap_done(request)
    return templates.TemplateResponse(request, "setup.html", {"errors": [], "email": ""})


@router.post("/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    await _reject_if_bootstrap_done(request)
    form = parse_credentials(email, password)
    if form.errors:
        context = {"errors": form.errors, "email": form.email}
        return templates.TemplateResponse(request, "setup.html", context, status_code=400)
    try:
        user = await resolve_bootstrap_admin(session, form.email, form.password)
    except BootstrapConflictError:
        # Course perdue : un autre appel vient de créer l'administrateur.
        raise HTTPException(status_code=409, detail="Un administrateur existe déjà.") from None
    request.app.state.bootstrap_done = True
    request.session["user_id"] = str(user.id)
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"errors": [], "email": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    user = await resolve_login(session, email.strip(), password)
    if user is None:
        context = {"errors": ["Email ou mot de passe incorrect."], "email": email.strip()}
        return templates.TemplateResponse(request, "login.html", context, status_code=401)
    request.session["user_id"] = str(user.id)
    log.info("login_succeeded", user_id=str(user.id))
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
