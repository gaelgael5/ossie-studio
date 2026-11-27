"""Refus nommés des référentiels, et problèmes de validation localisés."""

from __future__ import annotations

from ossie_studio.imports.errors import ImportsError
from ossie_studio.problems import Problem

__all__ = ["Problem", "ReferentielError"]


class ReferentielError(ImportsError):
    """Refus nommé d'un geste sur un référentiel ; rien n'a été écrit.

    `status` : le statut HTTP pour l'écran et l'API ; `problems` : les problèmes
    de validation (modèle ou valeurs), dans un ordre stable. Dérive de l'erreur
    du module commun d'import, que les routes des référentiels traduisent de la
    même façon."""
