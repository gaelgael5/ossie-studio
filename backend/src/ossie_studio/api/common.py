from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.deps import ConnDep, get_current_user
from ossie_studio.errors import ApiError
from ossie_studio.models import User
from ossie_studio.profiles.service import load_capabilities


def user_payload(user: User) -> dict[str, str]:
    return {"email": user.email, "role": user.role}


async def _space_from_path(request: Request, conn: ConnDep) -> uuid.UUID | None:
    """Identifiant d'espace porté par le chemin de la route, ou None hors espace.
    Un espace inconnu (ou un identifiant mal formé) → 404."""
    raw = request.path_params.get("space_id")
    if raw is None:
        return None
    try:
        space_id = uuid.UUID(str(raw))
    except ValueError:
        raise ApiError("space_not_found", status_code=404) from None
    if not await conn.fetchval("SELECT 1 FROM git_spaces WHERE id = $1", space_id):
        raise ApiError("space_not_found", status_code=404)
    return space_id


def require_capability(capability: str) -> Callable[..., Awaitable[User]]:
    """Fabrique une garde d'API pour une capacité : anonyme → 401, profil sans
    la capacité → 403. Les droits sont relus à chaque requête (jamais figés en
    session) : un changement de profil vaut dès la requête suivante. Le serveur
    fait foi ; le masquage côté écran n'est jamais la protection.

    Une capacité déclinable par espace, vérifiée sur une route qui porte un
    `space_id`, est évaluée **dans le contexte de cet espace** (profil de base +
    équipes rattachées à l'espace) : toutes les routes sous `/api/spaces/{id}/…`
    en profitent sans rien changer à leur signature."""

    async def dependency(request: Request, conn: ConnDep) -> User:
        user = await get_current_user(request, conn)
        if user is None:
            raise ApiError("unauthenticated", status_code=401)
        space_id = await _space_from_path(request, conn) if capability in SPACE_SCOPED else None
        if capability not in await load_capabilities(conn, user, space_id):
            raise ApiError("forbidden", status_code=403)
        return user

    return dependency


async def require_user(request: Request, conn: ConnDep) -> User:
    """Utilisateur connecté, sans capacité exigée (la route filtre elle-même)."""
    user = await get_current_user(request, conn)
    if user is None:
        raise ApiError("unauthenticated", status_code=401)
    return user


CurrentUser = Annotated[User, Depends(require_user)]


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
CurationReview = Annotated[User, Depends(require_capability("curation:review"))]
CurationRules = Annotated[User, Depends(require_capability("curation:rules"))]
GovernanceRead = Annotated[User, Depends(require_capability("governance:read"))]
AuditRead = Annotated[User, Depends(require_capability("audit:read"))]
GovernanceWrite = Annotated[User, Depends(require_capability("governance:write"))]
ReferentielsRead = Annotated[User, Depends(require_capability("referentiels:read"))]
ReferentielsWrite = Annotated[User, Depends(require_capability("referentiels:write"))]
CartographieRead = Annotated[User, Depends(require_capability("cartographie:read"))]
CartographieWrite = Annotated[User, Depends(require_capability("cartographie:write"))]
ProfilesRead = Annotated[User, Depends(require_capability("profiles:read"))]
ProfilesWrite = Annotated[User, Depends(require_capability("profiles:write"))]
UsersRead = Annotated[User, Depends(require_capability("users:read"))]
UsersWrite = Annotated[User, Depends(require_capability("users:write"))]
AgentProfilesRead = Annotated[User, Depends(require_capability("agent_profiles:read"))]
AgentProfilesWrite = Annotated[User, Depends(require_capability("agent_profiles:write"))]
ApiKeysRead = Annotated[User, Depends(require_capability("api_keys:read"))]
ApiKeysWrite = Annotated[User, Depends(require_capability("api_keys:write"))]
AgentConnectionsRead = Annotated[User, Depends(require_capability("agent_connections:read"))]
