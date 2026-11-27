"""Vérification d'un template JSLT avant exécution : les fonctions appelées existent.

Gael, 27/11/2026 (ticket 2d39db17) : « J'ai utilisé le template jslt pour sa
flexibilité. c'est pas pour retirer des fonctions. […] si le template compile et que
les valeurs […] existent c'est tout ce qu'on demande. » Le profil d'écriture repris
de Miles (cadrage 68cae2ff : ni arithmétique, ni logique, ni comparaison, ni `when`,
ni variable) est donc retiré côté Ossie ; Miles garde le sien. Restent : la
compilation (`jslt_engine.parse_ast`) et le catalogue de fonctions, ici. La validité
du résultat (propriétés du schéma, types, clé) est l'affaire de la cible d'import.
"""

from __future__ import annotations

from dataclasses import dataclass

from urban_json import FunctionRegistry
from urban_json.ast import JsltBase
from urban_json.ast.nodes import (
    JsltArray,
    JsltBinaryOperator,
    JsltFunctionCall,
    JsltObject,
    JsltOperator,
    JsltProperty,
    JsltSwitch,
)


@dataclass(frozen=True)
class Violation:
    """Une fonction inconnue : nommée et située, pour que l'auteur la corrige."""

    construct: str
    detail: str | None
    line: int | None
    column: int | None


def _at(node: JsltBase, construct: str, detail: str | None = None) -> Violation:
    location = node.location
    line = location.line if location is not None else None
    column = location.column if location is not None else None
    return Violation(construct, detail, line, column)


def unknown_functions(ast: JsltBase, registry: FunctionRegistry) -> tuple[Violation, ...]:
    """Fonctions appelées que le catalogue ne connaît pas : refusées à l'écriture.

    Vérifiées ici plutôt qu'à l'exécution : une fonction retirée du registre rend
    ses templates invalides dès leur prochaine vérification, pas sur un terminal.
    """
    found: list[Violation] = []
    _calls(ast, registry, found)
    return tuple(found)


def _calls(node: JsltBase, registry: FunctionRegistry, found: list[Violation]) -> None:
    if isinstance(node, JsltFunctionCall) and node.name not in registry:
        found.append(_at(node, "fonction_inconnue", node.name))
    for child in _children(node):
        _calls(child, registry, found)


def _children(node: JsltBase) -> list[JsltBase]:
    children: list[JsltBase | None] = []
    if isinstance(node, JsltObject):
        children += [node.source, node.where, *node.variables, *node.properties]
    elif isinstance(node, JsltProperty):
        children.append(node.value)
    elif isinstance(node, JsltArray):
        children += node.items
    elif isinstance(node, JsltFunctionCall):
        children += node.arguments
    elif isinstance(node, JsltBinaryOperator):
        children += [node.left, node.right]
    elif isinstance(node, JsltOperator):
        children.append(node.left)
    elif isinstance(node, JsltSwitch):
        children += [node.expression, node.default, *node.cases]
    return [c for c in children if c is not None]
