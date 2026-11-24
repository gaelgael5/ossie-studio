"""Écrire et lire les référentiels d'une branche, pour une personne.

L'écriture va dans le **brouillon de la personne** (`DraftWorkdir.commit_to_branch`),
comme pour les sources et la gouvernance : aucun commit, aucun push. La
**branche racine** du dépôt est en lecture seule (règle des espaces Git) :
écrire un référentiel quand la branche visée est la racine est refusé
(« branch_root_readonly »), avant toute écriture.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace
from ossie_studio.referentiels.errors import ReferentielError


@dataclass(frozen=True)
class ReferentielTarget:
    """Où et pour qui : une branche suivie d'un espace (`branch_ref`), dans le
    brouillon de la personne (`workdir`), sous son identité (`author`)."""

    workdir: DraftWorkdir
    space: GitSpace
    branch_ref: str
    username: str
    token: str | None
    author: str

    async def read[T](self, fn: Callable[[Path], T]) -> T:
        return await self.workdir.read_branch(
            self.space.id, self.branch_ref, self.space.repo_url, self.username, self.token, fn
        )

    async def is_root(self) -> bool:
        """La branche visée est-elle la racine du dépôt (lecture seule) ?"""
        _heads, root = await self.workdir.manager.remote_branches(
            self.space.id, self.space.repo_url, self.username, self.token
        )
        return bool(root) and self.branch_ref == root

    async def _check_not_root(self) -> None:
        if await self.is_root():
            raise ReferentielError("branch_root_readonly", 409, ref=self.branch_ref)

    async def write[T](self, intent: str, fn: Callable[[Path], T]) -> tuple[T, str | None]:
        """Applique `fn` dans le brouillon de la personne ; rend ce qu'elle rend
        et la révision du brouillon (None si rien n'a changé). Refus sur la
        branche racine, et tout refus de `fn`, avant écriture."""
        await self._check_not_root()
        results: list[T] = []
        revision = await self.workdir.commit_to_branch(
            self.space.id,
            self.branch_ref,
            self.space.repo_url,
            self.username,
            self.token,
            self.author,
            self.author,
            f"référentiel : {intent}",
            lambda root: results.append(fn(root)),
        )
        return results[-1], revision
