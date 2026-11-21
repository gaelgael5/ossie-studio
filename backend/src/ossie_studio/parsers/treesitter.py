"""Moteur d'analyse syntaxique commun aux parseurs de code, fondé sur
**tree-sitter**.

Un seul moteur, embarqué, sans compilateur ni environnement d'exécution par
langage (pas de .NET, de JVM ni de Node dans l'image), tolérant aux fichiers
incomplets. C'est une analyse **syntaxique** : pas de résolution de types entre
fichiers — un type venu d'une bibliothèque devient `x-unresolved-type`.

Les grammaires sont **épinglées** par version ; cette version entre dans la
version du parseur (changer de grammaire force la retransformation).
"""

from __future__ import annotations

import importlib
from functools import cache
from importlib.metadata import version as _pkg_version

from tree_sitter import Language, Node, Parser, Tree

# Grammaires installées : identifiant de langage → module Python de la grammaire.
GRAMMARS: dict[str, str] = {
    "csharp": "tree_sitter_c_sharp",
    "python": "tree_sitter_python",
    "java": "tree_sitter_java",
    "kotlin": "tree_sitter_kotlin",
    "typescript": "tree_sitter_typescript",
    "tsx": "tree_sitter_typescript",
}

# Nom de la fonction d'entrée de la grammaire, quand ce n'est pas `language`
# (le paquet TypeScript expose deux grammaires dans un même module).
_LANGUAGE_FUNCS: dict[str, str] = {
    "typescript": "language_typescript",
    "tsx": "language_tsx",
}


class GrammarUnavailable(Exception):
    """Aucune grammaire tree-sitter pour ce langage."""

    def __init__(self, language: str) -> None:
        super().__init__(language)
        self.language = language


@cache
def _language(language: str) -> Language:
    module_name = GRAMMARS.get(language)
    if module_name is None:
        raise GrammarUnavailable(language)
    module = importlib.import_module(module_name)
    func = getattr(module, _LANGUAGE_FUNCS.get(language, "language"))
    return Language(func())


def grammar_version(language: str) -> str:
    """Version épinglée de la grammaire (pour la version du parseur)."""
    module_name = GRAMMARS.get(language)
    if module_name is None:
        raise GrammarUnavailable(language)
    return _pkg_version(module_name.replace("_", "-"))


def parse(language: str, source: bytes) -> Tree:
    """Analyse syntaxique d'un fichier source. Ne lève jamais sur du code
    incomplet : tree-sitter produit un arbre, avec des nœuds d'erreur isolés."""
    return Parser(_language(language)).parse(source)


def node_text(source: bytes, node: Node) -> str:
    """Texte d'un nœud, décodé en UTF-8 (caractères illisibles remplacés)."""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")
