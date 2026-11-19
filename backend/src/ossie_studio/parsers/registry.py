from __future__ import annotations

from ossie_studio.parsers.base import Plugin
from ossie_studio.parsers.demo import DemoJsonSchemaParser
from ossie_studio.parsers.markdown import MarkdownPlugin
from ossie_studio.parsers.openapi import OpenApiParser

# Plugins **en dur** pour l'instant. L'interface et le registre sont pensés pour
# qu'on puisse en brancher d'autres plus tard sans relivrer l'application.
_DEMO = DemoJsonSchemaParser()
_MARKDOWN = MarkdownPlugin()
_OPENAPI = OpenApiParser()

# Plugins appliqués au **code** d'une source Git (choisis par le manifeste, défaut
# : tous). Le plugin OpenAPI n'en fait pas partie : il ne tourne que sur les
# sources `openapi`, sur les contrats déposés.
_CODE_PLUGINS: tuple[Plugin, ...] = (_DEMO, _MARKDOWN)
_ALL_PLUGINS: tuple[Plugin, ...] = (_DEMO, _MARKDOWN, _OPENAPI)


def all_plugins() -> tuple[Plugin, ...]:
    """Tous les plugins installés (pour la page de gestion des plugins)."""
    return _ALL_PLUGINS


def code_plugins() -> tuple[Plugin, ...]:
    """Les plugins de code applicables à une source Git."""
    return _CODE_PLUGINS


def openapi_plugin() -> Plugin:
    """Le plugin qui traite les contrats d'une source OpenAPI."""
    return _OPENAPI


def plugin_by_id(plugin_id: str) -> Plugin | None:
    return next((p for p in _ALL_PLUGINS if p.id == plugin_id), None)


def select_code_plugins(parseurs: list[str] | None) -> list[Plugin]:
    """Les plugins de code à lancer sur une source : ceux nommés dans le manifeste
    (ordre du manifeste, inconnus ignorés), ou tous par défaut."""
    if not parseurs:
        return list(_CODE_PLUGINS)
    by_id = {p.id: p for p in _CODE_PLUGINS}
    return [by_id[pid] for pid in parseurs if pid in by_id]
