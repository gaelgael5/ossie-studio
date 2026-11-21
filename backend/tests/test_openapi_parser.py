from __future__ import annotations

import json

import pytest
import yaml

from ossie_studio.parsers.openapi import (
    Analysis,
    OpenApiError,
    OpenApiParser,
    analyze,
    proposed_version_name,
)


def _doc(**overrides: object) -> bytes:
    base: dict[str, object] = {
        "openapi": "3.1.0",
        "info": {"title": "API boutique", "version": "2.1.0"},
        "paths": {},
        "components": {"schemas": {}},
    }
    base.update(overrides)
    return yaml.safe_dump(base).encode("utf-8")


def _schema_of(parser: OpenApiParser, filename: str, raw: bytes, key: str) -> dict:
    objects = {o.key: o for o in parser.parse({filename: raw})}
    return json.loads(objects[key].schema)


# --- Analyse -----------------------------------------------------------------


def test_analyse_openapi_31() -> None:
    raw = _doc(
        components={
            "schemas": {
                "Commande": {"type": "object"},
                "Ligne": {"type": "object"},
            }
        },
        paths={
            "/commandes": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Commande"}
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    )
    a = analyze(raw)
    assert isinstance(a, Analysis)
    assert a.openapi == "3.1.0"
    assert a.title == "API boutique"
    assert a.declared_version == "2.1.0"
    assert a.schema_count == 2
    assert a.operation_count == 1
    assert a.external_refs == []
    assert a.proposed_version == "v2"


def test_analyse_refuse_swagger_2() -> None:
    raw = yaml.safe_dump({"swagger": "2.0", "info": {}}).encode("utf-8")
    with pytest.raises(OpenApiError) as exc:
        analyze(raw)
    assert exc.value.code == "swagger_unsupported"


def test_analyse_refuse_non_openapi() -> None:
    with pytest.raises(OpenApiError) as exc:
        analyze(b"- ceci\n- n'est pas\n- un contrat\n")
    assert exc.value.code == "not_openapi"


def test_analyse_refuse_yaml_invalide() -> None:
    with pytest.raises(OpenApiError) as exc:
        analyze(b"{ ceci : : : pas valide")
    assert exc.value.code == "yaml_invalid"


def test_analyse_refuse_ref_interne_absente() -> None:
    raw = _doc(
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {"remise": {"$ref": "#/components/schemas/Remise"}},
                }
            }
        }
    )
    with pytest.raises(OpenApiError) as exc:
        analyze(raw)
    assert exc.value.code == "ref_missing"
    assert "Remise" in exc.value.detail


def test_analyse_signale_ref_externe_sans_refus() -> None:
    raw = _doc(
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {"montant": {"$ref": "commun.yaml#/Money"}},
                }
            }
        }
    )
    a = analyze(raw)
    assert a.external_refs == ["commun.yaml#/Money"]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [("4.0.0", "v4"), ("", "v1"), ("1.2", "v1"), ("v3", "v3"), ("2026-01", "v2026")],
)
def test_version_proposee(declared: str, expected: str) -> None:
    assert proposed_version_name(declared) == expected


# --- Parseur : un schéma par objet, version-scopé ---------------------------


def test_un_schema_par_objet_prefixe_par_version() -> None:
    raw = _doc(
        components={"schemas": {"Commande": {"type": "object"}, "Ligne": {"type": "object"}}}
    )
    keys = sorted(o.key for o in OpenApiParser().parse({"v1.yaml": raw}))
    assert keys == ["v1-commande", "v1-ligne"]


def test_versions_coexistent() -> None:
    raw = _doc(components={"schemas": {"Commande": {"type": "object"}}})
    parser = OpenApiParser()
    keys_v1 = {o.key for o in parser.parse({"v1.yaml": raw})}
    keys_v2 = {o.key for o in parser.parse({"v2.yaml": raw})}
    # Commande v1 et v2 sont deux schémas distincts.
    assert keys_v1 == {"v1-commande"}
    assert keys_v2 == {"v2-commande"}


def test_schema_porte_origine_et_usage() -> None:
    raw = _doc(
        components={"schemas": {"Commande": {"type": "object"}}},
        paths={
            "/commandes": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Commande"}
                            }
                        }
                    }
                }
            }
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    assert schema["$id"] == "v1-commande.json"
    assert schema["x-source"] == {"fichier": "v1.yaml", "pointeur": "#/components/schemas/Commande"}
    assert schema["x-used-by"] == ["POST /commandes"]


def test_ref_interne_pointe_vers_le_fichier_genere() -> None:
    raw = _doc(
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {"ligne": {"$ref": "#/components/schemas/Ligne"}},
                },
                "Ligne": {"type": "object"},
            }
        }
    )
    schema = _schema_of(OpenApiParser(), "v2.yaml", raw, "v2-commande")
    assert schema["properties"]["ligne"] == {"$ref": "v2-ligne.json"}


def test_ref_externe_devient_x_unresolved() -> None:
    raw = _doc(
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {"montant": {"$ref": "commun.yaml#/Money"}},
                }
            }
        }
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    assert schema["properties"]["montant"] == {"x-unresolved-type": "commun.yaml#/Money"}


# --- Conversion 3.0 → 2020-12 ------------------------------------------------


def test_30_nullable_type_admet_null() -> None:
    raw = _doc(
        openapi="3.0.3",
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {"note": {"type": "string", "nullable": True}},
                }
            }
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    assert schema["properties"]["note"]["type"] == ["string", "null"]
    assert "nullable" not in schema["properties"]["note"]


def test_30_nullable_avec_ref_devient_anyof() -> None:
    raw = _doc(
        openapi="3.0.3",
        components={
            "schemas": {
                "Commande": {
                    "type": "object",
                    "properties": {
                        "ligne": {"$ref": "#/components/schemas/Ligne", "nullable": True}
                    },
                },
                "Ligne": {"type": "object"},
            }
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    ligne = schema["properties"]["ligne"]
    assert ligne == {"anyOf": [{"$ref": "v1-ligne.json"}, {"type": "null"}]}


def test_30_example_devient_examples() -> None:
    raw = _doc(
        openapi="3.0.3",
        components={
            "schemas": {"Commande": {"type": "string", "example": "ABC"}}
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    assert schema["examples"] == ["ABC"]
    assert "example" not in schema


def test_30_exclusive_minimum_booleen_devient_numerique() -> None:
    raw = _doc(
        openapi="3.0.3",
        components={
            "schemas": {
                "Commande": {"type": "integer", "minimum": 5, "exclusiveMinimum": True}
            }
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    assert schema["exclusiveMinimum"] == 5
    assert "minimum" not in schema


def test_31_type_tableau_inchange() -> None:
    raw = _doc(
        components={
            "schemas": {"Commande": {"type": ["string", "null"], "example": "x"}}
        },
    )
    schema = _schema_of(OpenApiParser(), "v1.yaml", raw, "v1-commande")
    # 3.1 : type tableau gardé ; example (OpenAPI) normalisé en examples (standard).
    assert schema["type"] == ["string", "null"]
    assert schema["examples"] == ["x"]
    assert "example" not in schema


def test_sortie_deterministe() -> None:
    raw = _doc(components={"schemas": {"Commande": {"type": "object"}}})
    parser = OpenApiParser()
    first = parser.parse({"v1.yaml": raw})[0].schema
    second = parser.parse({"v1.yaml": raw})[0].schema
    assert first == second
    assert first.endswith("\n")
