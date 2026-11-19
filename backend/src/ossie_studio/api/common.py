from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from ossie_studio.deps import ConnDep, get_current_user
from ossie_studio.errors import ApiError
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities


def user_payload(user: User) -> dict[str, str]:
    return {"email": user.email, "role": user.role}


def require_capability(capability: str) -> Callable[..., Awaitable[User]]:
    """Fabrique une garde d'API pour une capacité : anonyme → 401, profil sans
    la capacité → 403. Les droits sont relus à chaque requête (jamais figés en
    session) : un changement de profil vaut dès la requête suivante. Le serveur
    fait foi ; le masquage côté écran n'est jamais la protection."""

    async def dependency(request: Request, conn: ConnDep) -> User:
        user = await get_current_user(request, conn)
        if user is None:
            raise ApiError("unauthenticated", status_code=401)
        if capability not in await load_capabilities(conn, user):
            raise ApiError("forbidden", status_code=403)
        return user

    return dependency


SecretsRead = Annotated[User, Depends(require_capability("secrets:read"))]
SecretsWrite = Annotated[User, Depends(require_capability("secrets:write"))]
ConnectionsRead = Annotated[User, Depends(require_capability("connections:read"))]
ConnectionsWrite = Annotated[User, Depends(require_capability("connections:write"))]
OidcRead = Annotated[User, Depends(require_capability("oidc:read"))]
OidcWrite = Annotated[User, Depends(require_capability("oidc:write"))]
SpacesRead = Annotated[User, Depends(require_capability("spaces:read"))]
SpacesCreate = Annotated[User, Depends(require_capability("spaces:create"))]
SpacesBranch = Annotated[User, Depends(require_capability("spaces:branch"))]
SpacesPublish = Annotated[User, Depends(require_capability("spaces:publish"))]
SpacesVersion = Annotated[User, Depends(require_capability("spaces:version"))]
SourcesRead = Annotated[User, Depends(require_capability("sources:read"))]
SourcesDeclare = Annotated[User, Depends(require_capability("sources:declare"))]
SourcesCollect = Annotated[User, Depends(require_capability("sources:collect"))]
ParsingRead = Annotated[User, Depends(require_capability("parsing:read"))]
ParsingManage = Annotated[User, Depends(require_capability("parsing:manage"))]
CurationRead = Annotated[User, Depends(require_capability("curation:read"))]
CurationWrite = Annotated[User, Depends(require_capability("curation:write"))]
GovernanceRead = Annotated[User, Depends(require_capability("governance:read"))]
GovernanceWrite = Annotated[User, Depends(require_capability("governance:write"))]
ProfilesRead = Annotated[User, Depends(require_capability("profiles:read"))]
ProfilesWrite = Annotated[User, Depends(require_capability("profiles:write"))]
UsersRead = Annotated[User, Depends(require_capability("users:read"))]
UsersWrite = Annotated[User, Depends(require_capability("users:write"))]
