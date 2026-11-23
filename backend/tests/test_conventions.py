from __future__ import annotations

import pytest

from ossie_studio.parsers.conventions import (
    ConventionError,
    SchemaReadError,
    dump_schema,
    is_schema_name,
    load_schema,
    schema_file,
    schema_key,
    validate_schema,
)

# --- Exemples conformes (critère 4) ------------------------------------------

CLASSE_PUBLIQUE = {
    "$id": "app.commande.schema.yaml",
    "title": "Commande",
    "type": "object",
    "x-language": "csharp",
    "x-kind": "class",
    "x-visibility": "public",
    "properties": {
        "numero": {"type": "string", "x-visibility": "public"},
        "ligne": {"$ref": "app.ligne.schema.yaml", "x-visibility": "public"},
    },
    "required": ["numero"],
}

CLASSE_INTERNE = {
    "$id": "app.interne.schema.yaml",
    "title": "Interne",
    "type": "object",
    "x-language": "csharp",
    "x-visibility": "internal",
    "properties": {},
}

ENUMERATION = {
    "$id": "app.statut.schema.yaml",
    "title": "Statut",
    "enum": ["ouvert", "fermé"],
    "x-language": "csharp",
    "x-kind": "enum",
    "x-visibility": "public",
}

CLASSE_HERITEE = {
    "$id": "app.commande-web.schema.yaml",
    "title": "CommandeWeb",
    "x-language": "csharp",
    "x-visibility": "public",
    "allOf": [{"$ref": "app.commande.schema.yaml"}],
    "properties": {"origine": {"type": "string", "x-visibility": "public"}},
}

TABLE_SQL = {
    "$id": "ventes.commandes.schema.yaml",
    "title": "commandes",
    "type": "object",
    "x-engine": "postgres",
    "x-kind": "table",
    "properties": {
        "id": {"type": "integer"},
        "client_id": {"type": "integer", "x-references": "ventes.clients.schema.yaml"},
    },
}


@pytest.mark.parametrize(
    "schema",
    [CLASSE_PUBLIQUE, CLASSE_INTERNE, ENUMERATION, CLASSE_HERITEE, TABLE_SQL],
)
def test_exemples_conformes_passent(schema: dict) -> None:
    validate_schema(schema)


# --- Refus (critère 2) -------------------------------------------------------


def test_refus_sans_id() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema({"type": "object"})
    assert exc.value.rule == "missing_id"


def test_refus_clef_non_standard_sans_x() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema({"$id": "a.schema.yaml", "type": "object", "discriminator": "k"})
    assert exc.value.rule == "unknown_key"
    assert exc.value.detail == "discriminator"


def test_refus_clef_non_standard_imbriquee() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {"$id": "a.schema.yaml", "properties": {"x": {"type": "string", "bogus": 1}}}
        )
    assert exc.value.rule == "unknown_key"


def test_refus_objet_code_sans_visibilite() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema({"$id": "a.schema.yaml", "type": "object", "x-language": "csharp"})
    assert exc.value.rule == "visibility_missing"


def test_refus_visibilite_hors_liste() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {"$id": "a.schema.yaml", "x-language": "csharp", "x-visibility": "friend"}
        )
    assert exc.value.rule == "visibility_invalid"


def test_refus_champ_de_code_sans_visibilite() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {
                "$id": "a.schema.yaml",
                "x-language": "csharp",
                "x-visibility": "public",
                "properties": {"x": {"type": "string"}},
            }
        )
    assert exc.value.rule == "field_visibility_missing"


def test_table_sql_sans_visibilite_est_acceptee() -> None:
    # Pas de x-language → pas d'exigence de visibilité.
    validate_schema({"$id": "s.t.schema.yaml", "type": "object", "x-engine": "postgres"})


# --- Déterminisme ------------------------------------------------------------


def test_dump_deterministe_et_trie() -> None:
    schema = {"$id": "a.schema.yaml", "type": "object", "title": "A"}
    first = dump_schema(schema)
    second = dump_schema(dict(reversed(list(schema.items()))))
    assert first == second
    assert first.endswith("\n")
    # Clefs triées : $id avant title avant type.
    assert list(load_schema(first).keys()) == ["$id", "title", "type"]


def test_fichier_de_schema_yaml_sans_collision_avec_les_index() -> None:
    """Ticket 5cc1cb07 : `<clé>.schema.yaml` ; les index YAML de raw/ n'en sont pas."""
    assert schema_file("app.order") == "app.order.schema.yaml"
    assert schema_key("app.order.schema.yaml") == "app.order"
    assert is_schema_name("app.order.schema.yaml")
    for index in ("manifest.yaml", "manifest-csharp.yaml", "docs-index.yaml", "a.json"):
        assert not is_schema_name(index)
    assert not is_schema_name("docs/a.schema.yaml")


def test_dump_schema_yaml_deterministe_et_fidele() -> None:
    schema = {
        "title": "Commande",
        "$id": "app.commande.schema.yaml",
        "x-date": "2026-11-22",  # reste une chaîne (pas de date implicite)
        "x-flag": "yes",
        "x-description": "Articles.\n\n```mermaid\ngraph TD; A-->B\n```\n",
        "properties": {"b": {"type": "string"}, "a": {"type": "integer"}},
    }
    text = dump_schema(schema)
    assert load_schema(text) == schema
    assert dump_schema(load_schema(text)) == text  # mêmes octets : pas de faux diff
    assert text.startswith("$id: app.commande.schema.yaml\n")  # clés triées
    assert "x-description: |\n  Articles.\n" in text  # bloc littéral lisible
    assert load_schema('{"a": 1}') == {"a": 1}  # un JSON se lit encore


def test_load_schema_illisible() -> None:
    with pytest.raises(SchemaReadError):
        load_schema("a: [b")
