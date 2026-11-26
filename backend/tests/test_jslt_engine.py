"""Moteur JSLT des imports de référentiels : le registre isolé garde tout ce que le
moteur d'urban-json sait d'une fonction du cœur (montée 0.2.1 → 0.8.0)."""

from __future__ import annotations

import pytest
from urban_json import default_registry

from ossie_studio.referentiels.jslt_engine import (
    _CORE_MODULE,
    OutsideProfileError,
    compile_template,
    evaluate,
    function_registry,
)

DOC = {
    "actif": True,
    "lignes": [{"nom": "a", "ok": True, "n": 2}, {"nom": "b", "ok": False, "n": 3}],
}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('{"r": ifelse($.actif, "oui", "non")}', {"r": "oui"}),
        ('{"r": map($.lignes, $.nom)}', {"r": ["a", "b"]}),
        ('{"r": filter($.lignes, $.ok)}', {"r": [{"nom": "a", "ok": True, "n": 2}]}),
        ('{"r": find($.lignes, $.ok)}', {"r": {"nom": "a", "ok": True, "n": 2}}),
    ],
)
def test_fonctions_a_arguments_paresseux(source: str, expected: object) -> None:
    """`ifelse`, `map`, `filter`… reçoivent leurs arguments non évalués : recopiées
    sans `lazy_args`, elles échouaient dans ce moteur (« 'str' object is not callable »)."""
    assert evaluate(compile_template(source), DOC, 10_000) == expected


def test_registre_isole_fidele_au_coeur() -> None:
    """Chaque fonction du cœur garde ses arguments de chemin, ses arguments paresseux et
    sa page de référence ; aucune fonction de plugin n'entre."""
    registry = function_registry()
    for name in registry.names():
        ours = registry.describe(name)
        theirs = default_registry.describe(name)
        assert ours is not None
        if theirs is None or theirs.function is not ours.function:
            continue  # la conversion stricte, propre à ce moteur
        assert ours.function.__module__.startswith(_CORE_MODULE), name
        assert (ours.path_args, ours.lazy_args, ours.doc) == (
            theirs.path_args,
            theirs.lazy_args,
            theirs.doc,
        ), name
    assert any(registry.describe(n).lazy_args for n in registry.names())  # type: ignore[union-attr]


def test_today_ecartee() -> None:
    """`today` dépend du jour d'exécution : refusée, nommée ; `now` et `uuid` restent
    (décision de Gael, 26/11/2026)."""
    with pytest.raises(OutsideProfileError) as refused:
        compile_template('{"jour": today()}')
    [violation] = refused.value.violations
    assert (violation.construct, violation.detail) == ("fonction_inconnue", "today")
    names = set(function_registry().names())
    assert "today" not in names and {"now", "uuid"} <= names
