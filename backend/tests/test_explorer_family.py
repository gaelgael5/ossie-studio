"""Filtre « Type » de l'explorateur raw (ticket docflow 915c76d2) : schémas,
contrats OpenAPI, documents markdown."""

from __future__ import annotations

from ossie_studio.sources.explorer import (
    ExplorerObject,
    SearchCriteria,
    object_family,
    object_matches,
)


def _obj(parser: str) -> ExplorerObject:
    return ExplorerObject("x.schema.yaml", "X", "class", 1, "", parser, (), "")


def test_famille_d_un_objet() -> None:
    assert object_family(_obj("openapi")) == "openapi"
    assert object_family(_obj("csharp")) == "schema"
    assert object_family(_obj("")) == "schema"


def test_critere_famille_avant_la_limite_et_sans_declencher_de_recherche() -> None:
    contract, code = _obj("openapi"), _obj("csharp")
    only_contracts = SearchCriteria(text="x", family="openapi")
    assert object_matches(contract, only_contracts)
    assert not object_matches(code, only_contracts)
    # Documents markdown : aucun objet n'en est.
    assert not object_matches(code, SearchCriteria(family="markdown"))
    assert not SearchCriteria(family="schema").active()
