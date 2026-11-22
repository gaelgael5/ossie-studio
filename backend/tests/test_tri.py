from __future__ import annotations

from ossie_studio.sources.tri import (
    TriRule,
    compile_rules,
    field_excluded,
    object_excluded,
    rule_label,
    validate_tri_rules,
)


def test_validate_ok_et_erreurs_nomment_la_regle() -> None:
    assert validate_tri_rules([]) == []
    assert validate_tri_rules(None) == []
    good = [{"target": "object-name", "pattern": "*Dto", "kind": "glob"}]
    assert validate_tri_rules(good) == []
    # Chaque erreur nomme la règle par son index.
    bad = [
        {"target": "nope", "pattern": "x", "kind": "glob"},
        {"target": "object-name", "pattern": "(", "kind": "regex"},  # regex invalide
        {"target": "field-name", "pattern": "", "kind": "glob"},     # motif vide
        {"target": "object-name", "pattern": "x", "kind": "fuzzy"},  # genre invalide
    ]
    errors = validate_tri_rules(bad)
    assert "triage[0]:target_invalid" in errors
    assert "triage[1]:pattern_invalid" in errors
    assert "triage[2]:pattern_required" in errors
    assert "triage[3]:kind_invalid" in errors


def test_validate_rejette_non_liste_et_cle_inconnue() -> None:
    assert validate_tri_rules({"target": "x"}) == ["triage_invalid"]
    assert "triage[0]:unknown_key" in validate_tri_rules(
        [{"target": "object-name", "pattern": "x", "kind": "glob", "oops": 1}]
    )


def test_object_excluded_par_nom_glob() -> None:
    rules = compile_rules([{"target": "object-name", "pattern": "*Dto", "kind": "glob"}])
    assert object_excluded({"title": "UserDto"}, rules) is not None
    assert object_excluded({"title": "User"}, rules) is None


def test_object_excluded_par_type_et_regex() -> None:
    rules = compile_rules([{"target": "object-type", "pattern": "^view$", "kind": "regex"}])
    assert object_excluded({"title": "V", "x-kind": "view"}, rules) is not None
    assert object_excluded({"title": "T", "x-kind": "table"}, rules) is None


def test_object_excluded_par_schema_sql_depuis_id() -> None:
    rules = compile_rules([{"target": "sql-schema", "pattern": "staging", "kind": "glob"}])
    assert object_excluded({"$id": "staging.orders.json", "title": "orders"}, rules) is not None
    assert object_excluded({"$id": "app.orders.json", "title": "orders"}, rules) is None


def test_object_excluded_par_chemin_fichier() -> None:
    rules = compile_rules([{"target": "file-path", "pattern": "*/generated/*", "kind": "glob"}])
    schema = {"title": "X", "x-source": [{"file": "src/generated/x.cs", "line": 3}]}
    assert object_excluded(schema, rules) is not None
    assert object_excluded({"title": "X", "x-source": [{"file": "src/x.cs"}]}, rules) is None


def test_field_excluded() -> None:
    rules = compile_rules([{"target": "field-name", "pattern": "ETL_*", "kind": "glob"}])
    assert field_excluded("ETL_LOAD_TS", rules) is not None
    assert field_excluded("id", rules) is None
    # Une règle de champ n'écarte pas l'objet entier.
    assert object_excluded({"title": "ETL_X"}, rules) is None


def test_rule_label() -> None:
    assert rule_label(TriRule("object-name", "*Dto", "glob", "DTO")) == "DTO"
    assert rule_label(TriRule("object-name", "*Dto", "glob")) == "object-name glob: *Dto"
