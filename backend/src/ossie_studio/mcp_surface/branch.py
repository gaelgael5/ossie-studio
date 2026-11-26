"""Lire une branche pour un agent : la version publiée, ou avec le brouillon de
la personne pour qui il agit.

Paramètre `branch`, commun à toutes les primitives (ticket aa19569b) :

- **Lecture** : absent → la branche **publiée** (le contenu validé, celui de
  tout le monde), y compris en connexion OAuth ; `draft` → publiée + brouillon
  de la personne. Ce qui vient du brouillon est marqué `unpublished`.
  Décision de Gael : le défaut était `draft` pour une connexion OAuth ; il passe
  à la version publiée, pour qu'un agent ne prenne pas pour acquis ce que la
  personne n'a pas encore validé sans l'avoir demandé.
- **Écriture** : toujours le brouillon de la personne ; absent, vide ou `draft`.
- Toute autre valeur est refusée (`branch_unsupported`) : on ne lit ni n'écrit
  encore une autre branche nommée.
- **Clé d'API** : personne derrière, donc aucun brouillon ; `draft` est refusé
  (`draft_requires_person`).
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

DRAFT = "draft"
# Valeurs de `branch` acceptées, citées dans le refus.
ACCEPTED_BRANCHES = (DRAFT,)

# Une clé d'API n'a pas de personne : en vue publiée, l'identifiant de personne
# du lecteur n'est jamais consulté (aucun brouillon n'est lu).
_NO_PERSON = uuid.UUID(int=0)


BRANCH_DESCRIPTION = (
    "Version to work on. Reading: omit it for the published branch (validated content, "
    "the default); 'draft' = the published branch plus the user's unpublished draft (items "
    "marked unpublished). Writing: always the user's draft (omit it or pass 'draft'). "
    "Any other value is refused (branch_unsupported)."
)


class BranchParam(BaseModel):
    """Le paramètre `branch`, déclaré par chaque primitive (test de concordance)."""

    model_config = ConfigDict(extra="forbid")
    # Texte libre plutôt qu'un Literal : une valeur inconnue doit donner le refus
    # nommé `branch_unsupported`, pas un `invalid_arguments` anonyme.
    branch: str | None = Field(default=None, description=BRANCH_DESCRIPTION)


def check_branch(branch: str | None) -> bool:
    """Vrai si `branch` demande le brouillon ; absent ou vide → faux.
    Toute autre valeur → `branch_unsupported`."""
    if not branch:
        return False
    if branch != DRAFT:
        raise PrimitiveError("branch_unsupported", branch=branch, accepted=list(ACCEPTED_BRANCHES))
    return True


class BranchArgs(BranchParam):
    space_id: uuid.UUID = Field(description="Space id (from list_spaces).")
    branch_id: uuid.UUID = Field(description="Branch id (from list_spaces).")


class SourceArgs(BranchArgs):
    source: str = Field(description="Source slug (from list_sources).")


class ObjectArgs(SourceArgs):
    path: str = Field(description="Schema file name, e.g. 'customer.schema.yaml' (from the lists).")


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
                self.space.id,
                self.branch.git_ref,
                self.space.repo_url,
                self.username,
                self.token,
                fn,
            )
        except WorkdirError as exc:
            raise PrimitiveError(exc.code) from None

    def unpublished(self, repo_path: str) -> bool:
        return repo_path in self.draft_paths

    def unpublished_under(self, repo_dir: str) -> int:
        prefix = repo_dir.rstrip("/") + "/"
        return sum(1 for p in self.draft_paths if p.startswith(prefix))


async def branch_of(
    ctx: PrimitiveContext, space_id: uuid.UUID, branch_id: uuid.UUID
) -> tuple[GitSpace, SpaceBranch, str, str | None]:
    """Espace, branche et identifiants git ; erreurs nommées comme dans l'IHM."""
    try:
        space, username, token = await space_credentials(ctx.conn, ctx.state.crypto, space_id)
    except ApiError as exc:
        raise PrimitiveError(exc.code) from None
    branch = await db_get_branch(ctx.conn, branch_id)
    if branch is None or branch.space_id != space_id:
        raise PrimitiveError("branch_not_found")
    return space, branch, username, token


async def open_branch(
    ctx: PrimitiveContext, args: BranchArgs, *, write: bool = False
) -> BranchView:
    """Branche ouverte selon `args.branch` ; `write` : toujours le brouillon."""
    with_draft = check_branch(args.branch) or write
    return await open_view(ctx, args.space_id, args.branch_id, with_draft=with_draft)


async def open_view(
    ctx: PrimitiveContext, space_id: uuid.UUID, branch_id: uuid.UUID, *, with_draft: bool
) -> BranchView:
    if with_draft and ctx.user is None:
        raise PrimitiveError("draft_requires_person")
    resolved: View = "draft" if with_draft else "published"
    space, branch, username, token = await branch_of(ctx, space_id, branch_id)
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
