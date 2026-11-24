"""Écrire et lire les référentiels d'une branche, pour une personne.

L'écriture va dans le **brouillon de la personne** (`DraftWorkdir.commit_to_branch`),
comme pour les sources et la gouvernance : aucun commit, aucun push. La
branche racine n'est PAS refusée (bug e7deb2b9, Gael : « il essaie d'écrire sur
la racine au lieu de mettre ça dans le répertoire de changement de
l'utilisateur ») : rien n'atteint la racine avant la publication, et la racine
peut être la branche de base d'un espace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace


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

    async def write[T](self, intent: str, fn: Callable[[Path], T]) -> tuple[T, str | None]:
        """Applique `fn` dans le brouillon de la personne ; rend ce qu'elle rend
        et la révision du brouillon (None si rien n'a changé). Tout refus de
        `fn` arrive avant écriture."""
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
