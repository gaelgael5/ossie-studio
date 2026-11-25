"""Refus nommés de la cartographie. Les problèmes localisés reprennent la forme
de ceux des référentiels (`path` : le fichier, `field` : la propriété en cause)."""

from __future__ import annotations

from ossie_studio.referentiels.errors import Problem

__all__ = ["CartographieError", "Problem"]


class CartographieError(Exception):
    """Refus nommé d'un geste sur la cartographie ; rien n'a été écrit.

    `status` : le statut HTTP pour l'écran et l'API ; `problems` : les problèmes
    en cause, dans un ordre stable."""

    def __init__(
        self,
        code: str,
        status: int = 400,
        problems: list[Problem] | None = None,
        **detail: object,
    ) -> None:
        self.code = code
        self.status = status
        self.problems = sorted(set(problems or []))
        self.detail = detail
        shown = "; ".join(str(p) for p in self.problems)
        super().__init__(f"{code}: {shown}" if shown else code)
