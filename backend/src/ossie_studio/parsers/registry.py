from __future__ import annotations

from ossie_studio.parsers.base import Parser
from ossie_studio.parsers.demo import DemoJsonSchemaParser

# Parseurs installés. Tant que l'epic « Parseurs et extracteurs » ne livre pas le
# registre et la sélection par source, la collecte applique tous les parseurs
# installés (ici, le seul parseur de démonstration).
_PARSERS: tuple[Parser, ...] = (DemoJsonSchemaParser(),)


def all_parsers() -> tuple[Parser, ...]:
    return _PARSERS


def parser_for(path: str) -> Parser | None:
    """Le premier parseur qui prend en charge ce fichier, s'il y en a un."""
    for parser in _PARSERS:
        if parser.handles(path):
            return parser
    return None


def parser_versions() -> dict[str, str]:
    return {parser.id: parser.version for parser in _PARSERS}
