"""Recherche par l'index (1/6, ticket 4618ee38) : schémas raw, schémas d'assets,
extensions raw et points d'entrée listés par l'index des documents, sans relire
`raw/` ni `assets/`.

Gael : l'index est « un outil de validation **et un outil de recherche** » ; « tout
mettre dans l'index des documents », pour qu'il n'y ait qu'un seul index à terme."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from ossie_studio.mcp_surface import document_reads, document_type_lists
from ossie_studio.parsers.application import module_key
from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit
from tests.test_mcp_application import AGENT, KEY, PROJECTS, RAW, space  # noqa: F401

ASSETS = "sources/boutique/assets"
API = f"{module_key(PROJECTS['Api'])}.schema.yaml"


def _class(name: str, **props: Any) -> str:
    schema: dict[str, Any] = {
        "$id": f"{name.lower()}.schema.yaml",
        "title": name,
        "type": "object",
        "x-kind": "class",
    }
    if props:
        schema["properties"] = {k: {"type": v} for k, v in props.items()}
    return dump_schema(schema)


@pytest.fixture
async def boutique(space: ApiSpace) -> ApiSpace:  # noqa: F811
    """La source « boutique » de la cartographie applicative, avec des règles de tri,
    des assets (complet, partiel, orphelin), une extension d'asset, une vue de module
    et un schéma illisible."""
    manifest = SourceManifest(
        slug="boutique",
        label="Boutique",
        type="git",
        connection="gh",
        parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
        triage=[{"target": "object-name", "pattern": "Fant*", "kind": "glob", "label": "fantômes"}],
    )
    forge_edit(
        space,
        {
            "sources/boutique/manifest.yaml": dump_manifest(manifest),
            f"{RAW}/client.schema.yaml": _class("Client", id="string", nom="string"),
            f"{RAW}/casse.schema.yaml": "title: [ouvert\n",
            f"{RAW}/{module_key(PROJECTS['Api'])}.view.yaml": dump_schema({"links": {}}),
            f"{ASSETS}/commande.schema.yaml": _class("Commande"),
            f"{ASSETS}/commande.extend.yaml": dump_schema({"object": {"description": "Une."}}),
            f"{ASSETS}/client.schema.yaml": _class("Client", id="string"),
            f"{ASSETS}/fantome.schema.yaml": _class("Fantome"),
        },
    )
    return space


def _ids(api_space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": api_space.space_id, "branch_id": api_space.branch_id, **extra}


async def _call(app: FastAPI, token: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(app, token, "list_documents", args)
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


def _from_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_documents par la lecture des fichiers, comme avant le ticket."""
    real = document_reads.resolve_document_type

    def without_index(name: str) -> Any:
        return dataclasses.replace(real(name), index_lister=None)

    monkeypatch.setattr(document_reads, "resolve_document_type", without_index)


def _no_source_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Compte les lectures de fichiers sous `sources/`, et interdit la lecture des
    sources par les fonctions des listes."""
    seen: list[str] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if "/sources/" in self.as_posix():
            seen.append(self.as_posix())
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        if "/sources/" in self.as_posix():
            seen.append(self.as_posix())
        return real_bytes(self)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("les fichiers des sources ne doivent pas être lus")

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    for name in ("list_curation", "collect_source_entries", "read_sources_tree", "read_tri_rules"):
        monkeypatch.setattr(document_type_lists, name, forbidden)
    return seen


CASES: list[tuple[str, dict[str, Any]]] = [
    ("schema_raw", {"identity": {"source": "boutique"}}),
    ("schema_raw", {}),
    ("schema_raw", {"identity": {"source": "boutique"}, "filters": {"kind": "module"}}),
    ("schema_raw", {"identity": {"source": "boutique"}, "filters": {"kind": "class"}}),
    ("schema_raw", {"identity": {"source": "boutique"}, "filters": {"module": "Boutique.Api"}}),
    ("schema_raw", {"search": "commande"}),
    ("schema_raw", {"identity": {"source": "inconnue"}}),
    ("schema_raw", {"identity": {"source": "boutique"}, "filters": {"module": "Nope"}}),
    ("extension_raw", {"identity": {"source": "boutique"}}),
    ("extension_raw", {"identity": {"source": "boutique"}, "filters": {"module": "Boutique.Api"}}),
    ("point_entree", {"identity": {"source": "boutique"}}),
    ("point_entree", {}),
    ("point_entree", {"identity": {"source": "boutique"}, "filters": {"module": "Boutique.Api"}}),
    (
        "point_entree",
        {"identity": {"source": "boutique"}, "filters": {"contract_status": "linked"}},
    ),
    ("point_entree", {"identity": {"source": "boutique"}, "filters": {"kind": "http"}}),
    ("schema_asset", {"identity": {"source": "boutique"}}),
    ("schema_asset", {}),
]


@pytest.mark.parametrize(("document_type", "extra"), CASES)
async def test_meme_liste_sans_lire_les_fichiers(
    app: FastAPI,
    boutique: ApiSpace,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    extra: dict[str, Any],
) -> None:
    """Critères 1 et 2 : pour chaque type et chaque filtre, la même réponse qu'avec la
    lecture des fichiers (refus nommés compris), sans lire aucun fichier des sources."""
    args = _ids(boutique, document_type=document_type, **extra)
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _call(app, KEY, args)
    seen = _no_source_file_read(monkeypatch)
    after = await _call(app, KEY, args)
    assert after == before
    assert seen == []


async def test_les_listes_rendent_ce_qu_on_attend(app: FastAPI, boutique: ApiSpace) -> None:
    """La comparaison porte sur des listes non vides : rétention, règle de tri,
    orphelin, extension, points d'entrée."""
    error, assets = await _call(app, KEY, _ids(boutique, document_type="schema_asset"))
    assert not error
    by_name = {d["identity"]["name"]: d for d in assets["documents"]}
    assert by_name["commande.schema.yaml"]["retained"] == "full"
    assert by_name["client.schema.yaml"]["retained"] == "partial"
    assert by_name["fantome.schema.yaml"]["orphan"] is True
    assert by_name["fantome.schema.yaml"]["excluded_by"] == "fantômes"
    assert all(d["version"] for d in assets["documents"])
    error, points = await _call(app, KEY, _ids(boutique, document_type="point_entree"))
    assert not error and points["total"] == 3
    error, raw = await _call(app, KEY, _ids(boutique, document_type="schema_raw"))
    names = {d["identity"]["name"] for d in raw["documents"]}
    assert "casse.schema.yaml" not in names and API in names


async def test_schema_enregistre_visible_tout_de_suite(app: FastAPI, boutique: ApiSpace) -> None:
    """Critère 3 : un schéma enregistré dans le brouillon apparaît aussitôt (branch
    draft), sans passe de validation."""
    await draft_write(
        app, boutique, f"{RAW}/panier.schema.yaml", {"title": "Panier", "x-kind": "class"}
    )
    args = _ids(boutique, document_type="schema_raw", branch="draft", search="panier")
    error, found = await _call(app, AGENT, args)
    assert not error, found
    assert [d["identity"]["name"] for d in found["documents"]] == ["panier.schema.yaml"]


async def test_fichier_ajoute_hors_application(app: FastAPI, boutique: ApiSpace) -> None:
    """Critère 4 : un schéma ajouté par un commit extérieur apparaît à la lecture
    suivante."""
    args = _ids(boutique, document_type="schema_raw", search="livraison")
    assert (await _call(app, KEY, args))[1]["total"] == 0
    forge_edit(boutique, {f"{RAW}/livraison.schema.yaml": _class("Livraison")})
    error, found = await _call(app, KEY, args)
    assert not error and [d["identity"]["name"] for d in found["documents"]] == [
        "livraison.schema.yaml"
    ]
