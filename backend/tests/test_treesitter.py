from __future__ import annotations

import pytest

from ossie_studio.parsers.treesitter import (
    GrammarUnavailable,
    grammar_version,
    node_text,
    parse,
)

_CODE = b"namespace App { public class Commande { public string Numero { get; set; } } }"


def test_parse_csharp_produit_un_arbre() -> None:
    tree = parse("csharp", _CODE)
    kinds = set()

    def walk(node: object) -> None:
        kinds.add(node.type)
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    assert "class_declaration" in kinds
    assert "namespace_declaration" in kinds


def test_node_text() -> None:
    tree = parse("csharp", _CODE)
    root = tree.root_node
    assert node_text(_CODE, root).startswith("namespace App")


def test_grammaire_epinglee() -> None:
    # La version de la grammaire est connue (elle entre dans la version du parseur).
    assert grammar_version("csharp")


def test_langage_inconnu_leve() -> None:
    with pytest.raises(GrammarUnavailable):
        parse("cobol", b"x")
