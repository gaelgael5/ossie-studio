from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ossie_studio.deps import ConnDep, get_current_user, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, conn: ConnDep) -> Response:
    user = await get_current_user(request, conn)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "home.html", {"user": user})
