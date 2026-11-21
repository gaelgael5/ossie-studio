"""Contexte commun des routes de gouvernance : la gouvernance se lit dans la
branche vue par la personne (avec son brouillon) et s'écrit dans son brouillon."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeVar

from fastapi import Depends

from ossie_studio.api.branches import space_credentials
from ossie_studio.crypto import CryptoService
from ossie_studio.deps import ConnDep, get_crypto, get_draft_workdir
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.governance.repo import InvalidGovernanceId
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

_T = TypeVar("_T")

CryptoDep = Annotated[CryptoService, Depends(get_crypto)]
DraftWorkdirDep = Annotated[DraftWorkdir, Depends(get_draft_workdir)]


@dataclass(frozen=True)
class GovCtx:
    workdir: DraftWorkdir
    space_id: uuid.UUID
    slug: str
    repo_url: str
    username: str
    token: str | None

    async def read(self, fn: Callable[[Path], _T]) -> _T:
        try:
            return await self.workdir.read_branch(
                self.space_id, self.slug, self.repo_url, self.username, self.token, fn
            )
        except WorkdirError as exc:
            raise ApiError(exc.code, status_code=502) from None

    async def write(self, author: str, message: str, fn: Callable[[Path], None]) -> None:
        def guarded(root: Path) -> None:
            try:
                fn(root)
            except InvalidGovernanceId:
                raise ApiError("not_found", status_code=404) from None

        try:
            await self.workdir.commit_to_branch(
                self.space_id,
                self.slug,
                self.repo_url,
                self.username,
                self.token,
                author,
                author,
                message,
                guarded,
            )
        except WorkdirError as exc:
            raise ApiError(exc.code, status_code=502) from None


async def gov_ctx(
    conn: ConnDep,
    crypto: CryptoService,
    workdir: DraftWorkdir,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
) -> GovCtx:
    branch = await db_get_branch(conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise ApiError("branch_not_found", status_code=404)
    space, username, token = await space_credentials(conn, crypto, space_id)
    return GovCtx(workdir, space_id, branch.slug, space.repo_url, username, token)
