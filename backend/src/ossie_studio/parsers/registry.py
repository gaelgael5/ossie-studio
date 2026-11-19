from __future__ import annotations

from ossie_studio.parsers.base import Parser
from ossie_studio.parsers.demo import DemoJsonSchemaParser
from ossie_studio.parsers.openapi import OpenApiParser

# Parseurs appliqués au **code** d'une source (dépôt Git). Tant que l'epic
# « Parseurs et extracteurs » ne livre pas le registre et la sélection par
# source, la collecte applique tous les parseurs de code installés (ici, le seul
# parseur de démonstration).
_CODE_PARSERS: tuple[Parser, ...] = (DemoJsonSchemaParser(),)

# Parseurs appliqués aux **contrats déposés** d'une source `openapi`. Ensemble
# distinct : ces parseurs ne doivent pas concurrencer ceux du code sur un dépôt
# Git (où un `.json` n'est pas un contrat OpenAPI).
_OPENAPI_PARSERS: tuple[Parser, ...] = (OpenApiParser(),)


def all_parsers() -> tuple[Parser, ...]:
    return _CODE_PARSERS


def openapi_parsers() -> tuple[Parser, ...]:
    return _OPENAPI_PARSERS


def parser_for(path: str) -> Parser | None:
    """Le premier parseur de code qui prend en charge ce fichier, s'il y en a un."""
    for parser in _CODE_PARSERS:
        if parser.handles(path):
            return parser
    return None


def parser_versions() -> dict[str, str]:
    return {parser.id: parser.version for parser in (*_CODE_PARSERS, *_OPENAPI_PARSERS)}
