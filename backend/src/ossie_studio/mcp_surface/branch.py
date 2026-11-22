"""Lire une branche pour un agent : la version publiée, ou avec le brouillon de
la personne pour qui il agit.

- **Connexion OAuth** : vue `draft` par défaut (publiée + brouillon de la
  personne), pour l'aider sur ce qu'elle est en train de faire ; `published`
  sur demande. Ce qui vient du brouillon est marqué `unpublished` : pas validé,
  pas encore publié.
- **Clé d'API** : personne derrière, donc aucun brouillon : vue `published`
  seule ; demander `draft` est refusé (`draft_requires_person`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.branches import space_credentials
from ossie_studio.drafts.store import db_get_draft, db_list_objects
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.errors import ApiError
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.models import GitSpace, SpaceBranch
from ossie_studio.sources.paths import is_valid_source_slug, source_rel_dir
from ossie_studio.spaces.branch_service import db_get_branch
from ossie_studio.spaces.workdir import WorkdirError

_T = TypeVar("_T")

View = Literal["draft", "published"]

# Une clé d'API n'a pas de personne : en vue publiée, l'identifiant de personne
# du lecteur n'est jamais consulté (aucun brouillon n'est lu).
_NO_PERSON = uuid.UUID(int=0)


class BranchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: uuid.UUID = Field(description="Space id (from list_spaces).")
    branch_id: uuid.UUID = Field(description="Branch id (from list_spaces).")


class ViewArgs(BranchArgs):
    view: View | None = Field(
        default=None,
        description=(
            "'draft' = published content plus the user's unpublished draft (default when "
            "acting for a user); 'published' = validated content only (always, with an API key)."
        ),
    )


class SourceArgs(ViewArgs):
    source: str = Field(description="Source slug (from list_sources).")


class ObjectArgs(SourceArgs):
    path: str = Field(description="Schema file name, e.g. 'customer.json' (from the lists).")


@dataclass(frozen=True)
class BranchView:
    """Une branche ouverte pour une lecture, dans la vue choisie."""

    space: GitSpace
    branch: SpaceBranch
    username: str
    token: str | None
    workdir: DraftWorkdir
    view: View
    # Chemins (depuis la racine du dépôt) modifiés dans le brouillon lu.
    draft_paths: frozenset[str]

    async def read(self, fn: Callable[..., _T]) -> _T:
        try:
            return await self.workdir.read_branch(
                self.space.id, self.branch.git_ref, self.space.repo_url, self.username,
                self.token, fn,
            )
        except WorkdirError as exc:
            raise PrimitiveError(exc.code) from None

    def unpublished(self, repo_path: str) -> bool:
        return repo_path in self.draft_paths

    def unpublished_under(self, repo_dir: str) -> int:
        prefix = repo_dir.rstrip("/") + "/"
        return sum(1 for p in self.draft_paths if p.startswith(prefix))


async def branch_of(ctx: PrimitiveContext, space_id: uuid.UUID, branch_id: uuid.UUID) -> tuple[
    GitSpace, SpaceBranch, str, str | None
]:
    """Espace, branche et identifiants git ; erreurs nommées comme dans l'IHM."""
    try:
        space, username, token = await space_credentials(ctx.conn, ctx.state.crypto, space_id)
    except ApiError as exc:
        raise PrimitiveError(exc.code) from None
    branch = await db_get_branch(ctx.conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise PrimitiveError("branch_not_found")
    return space, branch, username, token


async def open_branch(ctx: PrimitiveContext, args: BranchArgs, view: View | None) -> BranchView:
    if ctx.user is None:
        if view == "draft":
            raise PrimitiveError("draft_requires_person")
        view = "published"
    resolved: View = view or "draft"
    space, branch, username, token = await branch_of(ctx, args.space_id, args.branch_id)
    workdir = DraftWorkdir(
        ctx.state.workdir_manager,
        ctx.state.draft_views,
        ctx.state.pool,
        ctx.user.id if ctx.user is not None else _NO_PERSON,
        branch.id,
        published_only=resolved == "published",
        conn=ctx.conn,
    )
    paths: frozenset[str] = frozenset()
    if resolved == "draft" and ctx.user is not None:
        draft = await db_get_draft(ctx.conn, ctx.user.id, branch.id)
        if draft is not None and draft.space_id == space.id:
            paths = frozenset(o.path for o in await db_list_objects(ctx.conn, draft.id))
    return BranchView(space, branch, username, token, workdir, resolved, paths)


def source_dir(slug: str) -> str:
    """Répertoire d'une source dans le dépôt ; slug invalide → erreur nommée."""
    if not is_valid_source_slug(slug):
        raise PrimitiveError("source_not_found", source=slug)
    return source_rel_dir(slug)
