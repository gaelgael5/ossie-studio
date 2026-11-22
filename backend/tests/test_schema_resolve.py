from __future__ import annotations

import json

from ossie_studio.parsers.base import OutputAction
from ossie_studio.sources.schema_resolve import (
    SchemaIndex,
    is_schema_path,
    resolve_source_schemas,
)


def _schema(title: str, props: dict | None = None) -> str:
    body: dict = {"$id": f"{title.lower()}.json", "title": title, "type": "object"}
    if props is not None:
        body["properties"] = props
    return json.dumps(body)


def test_is_schema_path() -> None:
    assert is_schema_path("a.json")
    assert not is_schema_path("docs/a.json")
    assert not is_schema_path("manifest-csharp.yaml")


def test_index_resolout_nom_unique() -> None:
    index = SchemaIndex({"a-ligne.json": _schema("Ligne")})
    assert index.resolve("Ligne") == "a-ligne.json"
    assert index.resolve("Inconnu") is None


def test_index_ambigu_rend_none() -> None:
    index = SchemaIndex(
        {"a-ligne.json": _schema("Ligne"), "b-ligne.json": _schema("Ligne")}
    )
    assert index.resolve("Ligne") is None


def test_resolout_reference_dans_les_ecritures() -> None:
    commande = _schema("Commande", {"l": {"x-unresolved-type": "Ligne", "x-visibility": "public"}})
    ligne = _schema("Ligne", {"n": {"type": "integer"}})
    writes = [
        OutputAction(path="a-commande.json", content=commande),
        OutputAction(path="a-ligne.json", content=ligne),
        OutputAction(path="manifest-csharp.yaml", content="parsers: {}\n"),
    ]
    new_writes, resolved = resolve_source_schemas({}, writes, [])
    assert resolved == 1
    by_path = {w.path: json.loads(w.content) for w in new_writes if w.path.endswith(".json")}
    assert by_path["a-commande.json"]["properties"]["l"]["$ref"] == "a-ligne.json"
    # Les clefs voisines sont conservées.
    assert by_path["a-commande.json"]["properties"]["l"]["x-visibility"] == "public"
    assert "x-unresolved-type" not in by_path["a-commande.json"]["properties"]["l"]
    # L'index technique n'est pas touché.
    assert any(w.path == "manifest-csharp.yaml" for w in new_writes)


def test_resolout_contre_schema_existant() -> None:
    # Ligne est déjà dans raw/ ; une nouvelle Commande la référence.
    existing = {"a-ligne.json": _schema("Ligne")}
    commande = _schema("Commande", {"l": {"x-unresolved-type": "Ligne"}})
    writes = [OutputAction(path="a-commande.json", content=commande)]
    new_writes, resolved = resolve_source_schemas(existing, writes, [])
    assert resolved == 1
    commande_out = next(w for w in new_writes if w.path == "a-commande.json")
    assert json.loads(commande_out.content)["properties"]["l"]["$ref"] == "a-ligne.json"


def test_monotone_resout_un_ancien_schema_en_attente() -> None:
    # Une Commande en attente est déjà dans raw/ ; Ligne arrive maintenant.
    existing = {
        "a-commande.json": _schema("Commande", {"l": {"x-unresolved-type": "Ligne"}}),
    }
    writes = [OutputAction(path="a-ligne.json", content=_schema("Ligne"))]
    new_writes, resolved = resolve_source_schemas(existing, writes, [])
    assert resolved == 1
    # La Commande (non réécrite par un plugin) est ajoutée aux actions, résolue.
    commande_out = next(w for w in new_writes if w.path == "a-commande.json")
    assert json.loads(commande_out.content)["properties"]["l"]["$ref"] == "a-ligne.json"


def test_non_resolu_reste_en_attente() -> None:
    commande = _schema("Commande", {"a": {"x-unresolved-type": "Autre"}})
    writes = [OutputAction(path="a-commande.json", content=commande)]
    new_writes, resolved = resolve_source_schemas({}, writes, [])
    assert resolved == 0
    assert new_writes is writes  # rien à faire : liste inchangée


def test_idempotent_sur_schema_deja_resolu() -> None:
    existing = {
        "a-ligne.json": _schema("Ligne"),
        "a-commande.json": _schema("Commande", {"l": {"$ref": "a-ligne.json"}}),
    }
    new_writes, resolved = resolve_source_schemas(existing, [], [])
    assert resolved == 0
    assert new_writes == []


def test_cible_supprimee_non_resolue() -> None:
    existing = {"a-ligne.json": _schema("Ligne")}
    commande = _schema("Commande", {"l": {"x-unresolved-type": "Ligne"}})
    writes = [OutputAction(path="a-commande.json", content=commande)]
    # Ligne est supprimée dans la même collecte → plus de cible.
    _, resolved = resolve_source_schemas(existing, writes, ["a-ligne.json"])
    assert resolved == 0
