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


def test_arbre_compte_le_seul_type_choisi() -> None:
    """Bug d307159a : l'arbre suit le filtre « Type »."""
    from ossie_studio.sources.explorer import (
        ExplorerDocument,
        SourceEntries,
        index_from_entries,
        subtree_file_counts,
    )

    code = ExplorerObject("a.schema.yaml", "A", "class", 1, "src/A.cs", "csharp", (), "")
    contract = ExplorerObject("b.schema.yaml", "B", "object", 1, "api/b.yaml", "openapi", (), "")
    doc = ExplorerDocument("docs/guide.md", "Guide", 1)
    index = index_from_entries(SourceEntries("s", "S", "git", "", True, [code, contract], [doc]))
    assert subtree_file_counts(index)[""] == 3
    md = subtree_file_counts(index, "markdown")
    assert md[""] == 1 and md["docs"] == 1 and md["src"] == 0
    assert subtree_file_counts(index, "openapi")["api"] == 1
    assert subtree_file_counts(index, "schema")["src"] == 1
