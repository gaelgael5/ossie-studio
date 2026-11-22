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
    index = SchemaIndex({"a-ligne.json": _schema("Ligne"), "b-ligne.json": _schema("Ligne")})
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


# --- Homonymes : projets référencés et namespaces --------------------------------------


def _cs(title: str, ns: str, props: dict | None = None, usings: list[str] | None = None) -> str:
    body: dict = {
        "$id": f"{ns.lower()}-{title.lower()}.json",
        "title": title,
        "type": "object",
        "x-namespace": ns,
    }
    if usings:
        body["x-usings"] = usings
    if props is not None:
        body["properties"] = props
    return json.dumps(body)


def _tech(entries: dict[str, str]) -> str:
    from ossie_studio.sources.collect_core import TechEntry, TechManifest, dump_tech_manifest

    return dump_tech_manifest(
        TechManifest(
            parsers={},
            entries={
                src: TechEntry(checksum="x", destinations=[dest], branch="main", parser="csharp")
                for dest, src in entries.items()
            },
        )
    )


REF = {"w": {"x-unresolved-type": "WeightAndDimension", "x-visibility": "public"}}


def _resolved(writes: list[OutputAction], path: str) -> dict:
    content = next(a.content for a in writes if a.path == path)
    return json.loads(content)["properties"]["w"]


def test_homonymes_departages_par_le_namespace_de_l_objet() -> None:
    existing = {
        "a.json": _cs("WeightAndDimension", "Colis.TTEvent"),
        "b.json": _cs("WeightAndDimension", "Colis.Shipment"),
        "c.json": _cs("WeightAndDimension", "Colis.Model"),
        "p.json": _cs("ParcelInfos", "Colis.TTEvent", REF),
    }
    writes, count = resolve_source_schemas(existing, [], [])
    assert count == 1
    assert _resolved(writes, "p.json") == {"$ref": "a.json", "x-visibility": "public"}


def test_homonymes_namespace_parent_puis_using() -> None:
    existing = {
        "a.json": _cs("WeightAndDimension", "Colis.Shipment"),
        "b.json": _cs("WeightAndDimension", "Colis"),
        "p.json": _cs("ParcelInfos", "Colis.TTEvent.Deep", REF),
        "q.json": _cs("Other", "Ailleurs", REF, usings=["Colis.Shipment", "System"]),
        "r.json": _cs("Third", "Ailleurs", REF),
    }
    writes, _ = resolve_source_schemas(existing, [], [])
    assert _resolved(writes, "p.json")["$ref"] == "b.json"  # namespace parent « Colis »
    assert _resolved(writes, "q.json")["$ref"] == "a.json"  # using
    assert not any(a.path == "r.json" for a in writes)  # aucun indice : on ne devine pas


def test_homonymes_projet_non_reference_ecarte() -> None:
    existing = {
        "a.json": _cs("WeightAndDimension", "Colis.Shipment"),
        "b.json": _cs("WeightAndDimension", "Colis.Model"),
        "p.json": _cs("ParcelInfos", "Colis.Api", REF, usings=["Colis.Shipment", "Colis.Model"]),
        "manifest-csharp.yaml": _tech(
            {
                "a.json": "src/Shipment/W.cs",
                "b.json": "src/Model/W.cs",
                "p.json": "src/Api/P.cs",
            }
        ),
    }
    files = {
        "src/Api/Api.csproj": b"<Project><ItemGroup>"
        b'<ProjectReference Include="..\\Model\\Model.csproj" /></ItemGroup></Project>',
        "src/Model/Model.csproj": b"<Project/>",
        "src/Shipment/Shipment.csproj": b"<Project/>",
    }
    # Sans la carte des projets, les deux usings laissent un doute.
    writes, count = resolve_source_schemas(existing, [], [])
    assert count == 0
    # Avec : Shipment n'est pas référencé par Api, seul Model reste.
    writes, count = resolve_source_schemas(existing, [], [], files)
    assert count == 1
    assert _resolved(writes, "p.json")["$ref"] == "b.json"


def test_homonymes_toujours_ambigus_restent_non_resolus() -> None:
    existing = {
        "a.json": _cs("WeightAndDimension", "Colis.A"),
        "b.json": _cs("WeightAndDimension", "Colis.B"),
        "p.json": _cs("ParcelInfos", "Ailleurs", REF, usings=["Colis.A", "Colis.B"]),
    }
    _, count = resolve_source_schemas(existing, [], [])
    assert count == 0


def test_csproj_illisible_ou_reference_hors_depot() -> None:
    from ossie_studio.sources.schema_resolve import read_project_map

    projects = read_project_map(
        {
            "Api/Api.csproj": b'<ProjectReference Include="..\\..\\Outside\\O.csproj"/>\xff',
            "Lib/Lib.csproj": b"\x00\x01 pas du xml",
        }
    )
    assert projects.refs == {"Api": set(), "Lib": set()}
    assert projects.project_of("Api/Controllers/X.cs") == "Api"
    assert projects.project_of("Autre/X.cs") is None
