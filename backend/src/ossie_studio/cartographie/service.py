"""Lire et écrire la cartographie d'une branche, pour une personne (enabler
f8167ae1).

Comme pour les sources et les référentiels : la lecture voit la branche publiée et
le brouillon de la personne ; l'écriture va dans son **brouillon**
(`DraftWorkdir.commit_to_branch`), jamais directement dans la branche.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ossie_studio.cartographie.space import Cartographie, read_cartographie
from ossie_studio.doc_index.store import ViewKey
from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.models import GitSpace


@dataclass(frozen=True)
class CartographieTarget:
    """Où et pour qui : une branche d'un espace (`branch_ref`), dans le brouillon
    de la personne (`workdir`), sous son identité (`author`)."""

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

    async def signature(self, prefix: str) -> tuple[str, list[str]]:
        """Empreinte de ce que la lecture verrait sous `prefix`, et chemins du
        brouillon sous ce préfixe (voir `DraftWorkdir.prefix_signature`)."""
        return await self.workdir.prefix_signature(
            self.space.id, self.branch_ref, self.space.repo_url, self.username, self.token, prefix
        )

    async def indexed_view(self) -> ViewKey:
        """La vue lue, dans l'index des documents, d'accord avec Git (ticket 06171fd3)."""
        return await self.workdir.indexed_view(
            self.space.id, self.branch_ref, self.space.repo_url, self.username, self.token
        )

    async def cartographie(self) -> Cartographie:
        result: Cartographie = await self.read(read_cartographie)
        return result

    async def write[T](self, intent: str, fn: Callable[[Path], T]) -> tuple[T, str | None]:
        """Applique `fn` dans le brouillon ; rend ce qu'elle rend et la révision du
        brouillon (None si rien n'a changé). Un refus de `fn` arrive avant écriture."""
        results: list[T] = []
        revision = await self.workdir.commit_to_branch(
            self.space.id,
            self.branch_ref,
            self.space.repo_url,
            self.username,
            self.token,
            self.author,
            self.author,
            f"cartographie : {intent}",
            lambda root: results.append(fn(root)),
        )
        return results[-1], revision
