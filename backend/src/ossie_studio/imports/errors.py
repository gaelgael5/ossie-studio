"""Refus nommé d'un geste d'import ; rien n'a été écrit.

Base commune : chaque domaine en dérive son erreur (`ReferentielError`…), et ses
routes la traduisent comme les siennes (même code, même statut, mêmes problèmes).
"""

from __future__ import annotations

from ossie_studio.problems import Problem


class ImportsError(Exception):
    """`status` : le statut HTTP pour l'écran ; `problems` : les problèmes en cause,
    dans un ordre stable."""

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
