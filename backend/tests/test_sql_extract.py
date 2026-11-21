from __future__ import annotations

import json

from ossie_studio.databases.base import CatalogColumn, CatalogForeignKey, CatalogObject
from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.sources.sql_extract import SqlExtractor, object_to_source, source_key


def _extract(obj: CatalogObject) -> dict:
    files = {source_key(obj): object_to_source(obj)}
    objects = SqlExtractor("postgres").parse(files)
    assert len(objects) == 1
    schema = json.loads(objects[0].schema)
    validate_schema(schema)  # critère 7 : passe le validateur des conventions
    return schema


def _col(
    name: str, fragment: dict, *, required: bool, position: int, default=None, desc=""
) -> CatalogColumn:
    return CatalogColumn(
        name=name, schema=fragment, required=required, position=position,
        default=default, description=desc,
    )


def test_table_colonnes_et_obligatoires() -> None:
    obj = CatalogObject(
        schema="app",
        name="client",
        kind="table",
        description="Un client.",
        columns=(
            _col("id", {"type": "string", "format": "uuid", "x-db-type": "uuid"},
                 required=True, position=1),
            _col("nom", {"type": "string", "maxLength": 50, "x-db-type": "character varying(50)"},
                 required=True, position=2, desc="Nom complet."),
            _col("age", {"type": "integer", "format": "int32", "x-db-type": "integer"},
                 required=False, position=3, default=None),
        ),
        primary_key=("id",),
    )
    schema = _extract(obj)
    assert schema["$id"] == "app-client.json"
    assert schema["title"] == "client"
    assert schema["x-kind"] == "table"
    assert schema["description"] == "Un client."
    assert "x-language" not in schema  # SQL : pas d'objet de code, pas de x-visibility
    assert schema["properties"]["nom"]["maxLength"] == 50
    assert schema["properties"]["nom"]["description"] == "Nom complet."
    assert schema["properties"]["id"]["x-position"] == 1
    assert set(schema["required"]) == {"id", "nom"}
    assert schema["x-primary-key"] == ["id"]


def test_valeur_par_defaut_et_enum() -> None:
    obj = CatalogObject(
        schema="app", name="t", kind="table",
        columns=(
            _col("etat", {"enum": ["actif", "inactif"], "x-db-type": "statut"},
                 required=True, position=1, default="'actif'::statut"),
        ),
    )
    schema = _extract(obj)
    etat = schema["properties"]["etat"]
    assert etat["enum"] == ["actif", "inactif"]
    assert etat["x-default"] == "'actif'::statut"


def test_clefs_etrangeres_sur_la_colonne() -> None:
    obj = CatalogObject(
        schema="app", name="commande", kind="table",
        columns=(
            _col("id", {"type": "integer", "x-db-type": "bigint"}, required=True, position=1),
            _col("client_id", {"type": "string", "format": "uuid"}, required=True, position=2),
        ),
        primary_key=("id",),
        unique_keys=(("id",),),
        foreign_keys=(
            CatalogForeignKey(
                columns=("client_id",), ref_schema="app", ref_object="client",
                ref_columns=("id",),
            ),
        ),
    )
    schema = _extract(obj)
    assert schema["properties"]["client_id"]["x-references"] == {
        "table": "app.client", "column": "id",
    }
    assert schema["x-unique-keys"] == [["id"]]


def test_fk_non_appliquee() -> None:
    obj = CatalogObject(
        schema="app", name="t", kind="table",
        columns=(_col("ref_id", {"type": "integer"}, required=False, position=1),),
        foreign_keys=(
            CatalogForeignKey(
                columns=("ref_id",), ref_schema="app", ref_object="autre",
                ref_columns=("id",), enforced=False,
            ),
        ),
    )
    schema = _extract(obj)
    assert schema["properties"]["ref_id"]["x-references"]["enforced"] is False


def test_vue_depend_de() -> None:
    obj = CatalogObject(
        schema="app", name="v", kind="view",
        columns=(_col("id", {"type": "integer"}, required=False, position=1),),
        depends_on=("app.commande",),
    )
    schema = _extract(obj)
    assert schema["x-kind"] == "view"
    assert schema["x-depends-on"] == ["app.commande"]


def test_extra_au_niveau_objet() -> None:
    obj = CatalogObject(
        schema="app", name="v", kind="view",
        columns=(_col("id", {"type": "integer"}, required=False, position=1),),
        extra={"x-indexed": True},
    )
    schema = _extract(obj)
    assert schema["x-indexed"] is True


def test_source_deterministe_et_structure_seule() -> None:
    obj = CatalogObject(
        schema="app", name="t", kind="table",
        columns=(_col("id", {"type": "integer"}, required=True, position=1),),
    )
    # Deux sérialisations du même objet → mêmes octets (checksum stable).
    assert object_to_source(obj) == object_to_source(obj)


def test_cle_schema_objet() -> None:
    obj = CatalogObject(schema="dbo", name="Order", kind="table", columns=())
    assert source_key(obj) == "dbo.Order"
    objects = SqlExtractor("postgres").parse({source_key(obj): object_to_source(obj)})
    assert objects[0].key == "dbo-order"
