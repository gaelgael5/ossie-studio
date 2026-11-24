from __future__ import annotations

from ossie_studio.parsers.base import Plugin
from ossie_studio.parsers.csharp import CSharpParser
from ossie_studio.parsers.demo import DemoJsonSchemaParser
from ossie_studio.parsers.kotlin_java import KotlinJavaParser
from ossie_studio.parsers.markdown import MarkdownPlugin
from ossie_studio.parsers.openapi import OpenApiParser
from ossie_studio.parsers.python import PythonParser
from ossie_studio.parsers.sqlserver_project import SqlServerProjectParser
from ossie_studio.parsers.typescript import TypeScriptParser

# Plugins **en dur** pour l'instant. L'interface et le registre sont pensés pour
# qu'on puisse en brancher d'autres plus tard sans relivrer l'application.
_DEMO = DemoJsonSchemaParser()
_MARKDOWN = MarkdownPlugin()
_CSHARP = CSharpParser()
_PYTHON = PythonParser()
_TYPESCRIPT = TypeScriptParser()
_KOTLIN_JAVA = KotlinJavaParser()
_SQLSERVER_PROJECT = SqlServerProjectParser()
_OPENAPI = OpenApiParser()
# Le même parseur `openapi`, pour une source Git : il cherche les contrats parmi
# les YAML / JSON du dépôt (reconnaissance par contenu) — ticket 56be38c6.
_OPENAPI_REPO = OpenApiParser(repository=True)

# Plugins appliqués au **code** d'une source Git (choisis par le manifeste, défaut
# : tous). `openapi` en fait partie depuis le ticket 56be38c6 : sans risque par
# défaut, il ne prend que les fichiers dont la racine porte la clé `openapi`.
_CODE_PLUGINS: tuple[Plugin, ...] = (
    _CSHARP,
    _PYTHON,
    _TYPESCRIPT,
    _KOTLIN_JAVA,
    _SQLSERVER_PROJECT,
    _DEMO,
    _MARKDOWN,
    _OPENAPI_REPO,
)
_ALL_PLUGINS: tuple[Plugin, ...] = (
    _CSHARP,
    _PYTHON,
    _TYPESCRIPT,
    _KOTLIN_JAVA,
    _SQLSERVER_PROJECT,
    _DEMO,
    _MARKDOWN,
    _OPENAPI,
)


def all_plugins() -> tuple[Plugin, ...]:
    """Tous les plugins installés (pour la page de gestion des plugins)."""
    return _ALL_PLUGINS


def code_plugins() -> tuple[Plugin, ...]:
    """Les plugins de code applicables à une source Git."""
    return _CODE_PLUGINS


def openapi_plugin() -> Plugin:
    """Le plugin qui traite les contrats **déposés** d'une source OpenAPI."""
    return _OPENAPI


def plugin_by_id(plugin_id: str) -> Plugin | None:
    return next((p for p in _ALL_PLUGINS if p.id == plugin_id), None)


def unknown_code_parseur(parseurs: list[str]) -> str | None:
    """Le premier identifiant qui ne correspond à aucun plugin de code, s'il y en
    a un (pour refuser un manifeste nommant un parseur inconnu)."""
    known = {p.id for p in _CODE_PLUGINS}
    return next((pid for pid in parseurs if pid not in known), None)


def conflicting_glob(parseurs: list[str]) -> str | None:
    """Un motif globing pris en charge par deux des parseurs choisis, s'il existe
    (pour refuser deux parseurs qui se disputent le même fichier)."""
    owner: dict[str, str] = {}
    # Les instances « source Git » (dont `openapi` en mode dépôt), pas celles
    # des types de source : ce sont leurs motifs qui se disputent les fichiers.
    by_id = {p.id: p for p in _CODE_PLUGINS}
    for pid in parseurs:
        plugin = by_id.get(pid)
        if plugin is None:
            continue
        for pattern in plugin.globs():
            if pattern in owner and owner[pattern] != pid:
                return pattern
            owner[pattern] = pid
    return None


def select_code_plugins(parseurs: list[str] | None) -> list[Plugin]:
    """Les plugins de code à lancer sur une source : ceux nommés dans le manifeste
    (ordre du manifeste, inconnus ignorés), ou tous par défaut."""
    if not parseurs:
        return list(_CODE_PLUGINS)
    by_id = {p.id: p for p in _CODE_PLUGINS}
    return [by_id[pid] for pid in parseurs if pid in by_id]


# --- Plugins utilisés par un espace (ticket a30b4bd8) ---------------------------

ORIGIN_NATIVE = "native"


def plugin_usage(sources: list[tuple[str, str, str, list[str] | None]]) -> dict[str, list[str]]:
    """Identifiant de plugin → slugs des sources qui l'utilisent, d'après les
    manifestes (`(slug, label, type, parseurs)`). L'usage vit dans le Git de
    l'espace ; les plugins natifs, eux, sont dans l'application.

    - source Git : les parseurs nommés par son manifeste, ou tous les parseurs
      de code s'il n'en nomme aucun (c'est ce que la collecte lance) ;
    - source de contrats déposés : le plugin `openapi` ;
    - source base de données : un extracteur SQL, pas un plugin installé — rien.
    Un parseur inconnu de l'installation est ignoré (Gael, cadrage du 24/11).
    """
    from ossie_studio.sources.manifest import GIT_TYPE, OPENAPI_TYPE

    usage: dict[str, list[str]] = {}
    for slug, _label, source_type, parseurs in sources:
        if source_type == GIT_TYPE:
            ids = [p.id for p in select_code_plugins(parseurs)]
        elif source_type == OPENAPI_TYPE:
            ids = [_OPENAPI.id]
        else:
            ids = []
        for pid in dict.fromkeys(ids):
            usage.setdefault(pid, []).append(slug)
    return usage
