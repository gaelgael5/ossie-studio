from __future__ import annotations

import json

import pytest

from ossie_studio.parsers.conventions import ConventionError, dump_schema, validate_schema

# --- Exemples conformes (critère 4) ------------------------------------------

CLASSE_PUBLIQUE = {
    "$id": "app.commande.json",
    "title": "Commande",
    "type": "object",
    "x-language": "csharp",
    "x-kind": "class",
    "x-visibility": "public",
    "properties": {
        "numero": {"type": "string", "x-visibility": "public"},
        "ligne": {"$ref": "app.ligne.json", "x-visibility": "public"},
    },
    "required": ["numero"],
}

CLASSE_INTERNE = {
    "$id": "app.interne.json",
    "title": "Interne",
    "type": "object",
    "x-language": "csharp",
    "x-visibility": "internal",
    "properties": {},
}

ENUMERATION = {
    "$id": "app.statut.json",
    "title": "Statut",
    "enum": ["ouvert", "fermé"],
    "x-language": "csharp",
    "x-kind": "enum",
    "x-visibility": "public",
}

CLASSE_HERITEE = {
    "$id": "app.commande-web.json",
    "title": "CommandeWeb",
    "x-language": "csharp",
    "x-visibility": "public",
    "allOf": [{"$ref": "app.commande.json"}],
    "properties": {"origine": {"type": "string", "x-visibility": "public"}},
}

TABLE_SQL = {
    "$id": "ventes.commandes.json",
    "title": "commandes",
    "type": "object",
    "x-engine": "postgres",
    "x-kind": "table",
    "properties": {
        "id": {"type": "integer"},
        "client_id": {"type": "integer", "x-references": "ventes.clients.json"},
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
        validate_schema({"$id": "a.json", "type": "object", "discriminator": "k"})
    assert exc.value.rule == "unknown_key"
    assert exc.value.detail == "discriminator"


def test_refus_clef_non_standard_imbriquee() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {"$id": "a.json", "properties": {"x": {"type": "string", "bogus": 1}}}
        )
    assert exc.value.rule == "unknown_key"


def test_refus_objet_code_sans_visibilite() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema({"$id": "a.json", "type": "object", "x-language": "csharp"})
    assert exc.value.rule == "visibility_missing"


def test_refus_visibilite_hors_liste() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {"$id": "a.json", "x-language": "csharp", "x-visibility": "friend"}
        )
    assert exc.value.rule == "visibility_invalid"


def test_refus_champ_de_code_sans_visibilite() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_schema(
            {
                "$id": "a.json",
                "x-language": "csharp",
                "x-visibility": "public",
                "properties": {"x": {"type": "string"}},
            }
        )
    assert exc.value.rule == "field_visibility_missing"


def test_table_sql_sans_visibilite_est_acceptee() -> None:
    # Pas de x-language → pas d'exigence de visibilité.
    validate_schema({"$id": "s.t.json", "type": "object", "x-engine": "postgres"})


# --- Déterminisme ------------------------------------------------------------


def test_dump_deterministe_et_trie() -> None:
    schema = {"$id": "a.json", "type": "object", "title": "A"}
    first = dump_schema(schema)
    second = dump_schema(dict(reversed(list(schema.items()))))
    assert first == second
    assert first.endswith("\n")
    # Clefs triées : $id avant title avant type.
    assert list(json.loads(first).keys()) == ["$id", "title", "type"]
