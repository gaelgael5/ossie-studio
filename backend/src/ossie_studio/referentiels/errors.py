"""Refus nommés des référentiels, et problèmes de validation localisés."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Problem:
    """Un problème localisé : `path` dit où (la clé de la valeur, puis le chemin
    dans l'objet, séparés par `model.SEPARATOR` ; ou « x-key », ou le chemin
    dans le modèle), `code` dit quoi, `detail` précise (valeur en double, type
    attendu…)."""

    path: str
    code: str
    detail: str = ""
    # Problème d'une valeur : son rang dans la liste vérifiée (-1 : sans objet) et
    # le chemin du champ dans la valeur (« paliers[2] », puis « delai_jours » ; vide : la
    # valeur entière, par exemple une clé en double). L'écran s'en sert pour
    # désigner la ligne et la cellule.
    index: int = -1
    field: str = ""

    def __str__(self) -> str:
        where = f"{self.path} : " if self.path else ""
        return f"{where}{self.code}" + (f" ({self.detail})" if self.detail else "")

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {"path": self.path, "code": self.code, "detail": self.detail}
        if self.index >= 0:
            out.update({"index": self.index, "field": self.field})
        return out


class ReferentielError(Exception):
    """Refus nommé d'un geste sur un référentiel ; rien n'a été écrit.

    `status` : le statut HTTP pour l'écran et l'API ; `problems` : les problèmes
    de validation (modèle ou valeurs), dans un ordre stable."""

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
