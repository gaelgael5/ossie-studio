from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ossie_studio.deps import ConnDep, get_current_user
from ossie_studio.errors import ApiError
from ossie_studio.models import ROLE_ADMIN, User


def user_payload(user: User) -> dict[str, str]:
    return {"email": user.email, "role": user.role}


async def require_admin_api(request: Request, conn: ConnDep) -> User:
    """Garde d'API : anonyme → 401, rôle insuffisant → 403, le tout en codes."""
    user = await get_current_user(request, conn)
    if user is None:
        raise ApiError("unauthenticated", status_code=401)
    if user.role != ROLE_ADMIN:
        raise ApiError("forbidden", status_code=403)
    return user


AdminApiDep = Annotated[User, Depends(require_admin_api)]
