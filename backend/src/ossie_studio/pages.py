from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ossie_studio.deps import get_current_user, get_session, templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: SessionDep) -> Response:
    user = await get_current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "home.html", {"user": user})
