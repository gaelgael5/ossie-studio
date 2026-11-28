"""Recherche par l'index (4/6, ticket 3492625e) : sources, contrats et règles de tri
listés par l'index des documents, sans relire `sources/`."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface import document_reads, document_type_lists
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.sources.collect_core import (
    TechEntry,
    TechManifest,
    checksum,
    dump_tech_manifest,
)
from ossie_studio.sources.contracts import OPENAPI_INDEX, ContractEntry, dump_index
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "src-index-key"
AGENT = "src-index-agent"
CONTRACT = b"openapi: 3.0.0\ninfo: {title: Api, version: '1'}\npaths: {}\n"


def _manifest(slug: str, type_: str = "git", triage: list[Any] | None = None) -> str:
    parameters: dict[str, Any] = (
        {"repository": "o/r", "branch": "main", "parsers": ["csharp"]}
        if type_ == "git"
        else {"url": "https://exemple.org/openapi.yaml"}
    )
    return dump_manifest(
        SourceManifest(
            slug=slug,
            label=slug.capitalize(),
            type=type_,
            connection="gh",
            parameters=parameters,
            triage=triage or [],
        )
    )


def _entry(version: str, file: str) -> ContractEntry:
    return ContractEntry(version, file, f"{version}.yaml", "3.0.0", "1", 0, 0, "2026-11-28", "gael")


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    rule = {"target": "object-name", "pattern": "Tmp*", "kind": "glob"}
    forge_edit(
        api_space,
        {
            "sources/boutique/manifest.yaml": _manifest("boutique", triage=[rule]),
            "sources/api/manifest.yaml": _manifest("api", "openapi"),
            "sources/api/contrats/contrats.yaml": dump_index(
                [_entry("v1", "v1.yaml"), _entry("v2", "v2.yaml")]
            ),
            "sources/api/contrats/v1.yaml": CONTRACT.decode(),
            "sources/api/contrats/v2.yaml": CONTRACT.decode() + "# v2\n",
            f"sources/api/raw/{OPENAPI_INDEX}": dump_tech_manifest(
                TechManifest(
                    entries={
                        "v1.yaml": TechEntry(checksum(CONTRACT), ["a.schema.yaml"], "", "openapi")
                    }
                )
            ),
            "sources/cassee/manifest.yaml": "slug: [ouvert\n",
            "sources/autre/manifest.yaml": _manifest("pas-autre"),
            "sources/sans/README.md": "rien\n",
        },
    )
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    callers = {
        KEY: McpCaller(kind="key", ref="k", agent_profile_id=lecture),
        AGENT: McpCaller(
            kind="oauth", ref="c", agent_profile_id=lecture, user_id=admin_id, client_id="claude"
        ),
    }

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return callers.get(token)

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, token: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(app, token, "list_documents", args)
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


def _from_files(monkeypatch: pytest.MonkeyPatch) -> None:
    real = document_reads.resolve_document_type

    def without_index(name: str) -> Any:
        return dataclasses.replace(real(name), index_lister=None)

    monkeypatch.setattr(document_reads, "resolve_document_type", without_index)


def _no_source_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
    for name in ("read_sources_tree", "read_contracts_view", "read_tri_rules_raw"):
        monkeypatch.setattr(document_type_lists, name, forbidden)
    return seen


CASES: list[tuple[str, dict[str, Any]]] = [
    ("source", {}),
    ("source", {"filters": {"type": "openapi"}}),
    ("source", {"identity": {"source": "boutique"}}),
    ("source", {"search": "openapi"}),
    ("contrat", {}),
    ("contrat", {"identity": {"source": "api"}}),
    ("contrat", {"identity": {"source": "inconnue"}}),
    ("regles_tri", {}),
    ("regles_tri", {"identity": {"source": "boutique"}}),
]


@pytest.mark.parametrize(("document_type", "extra"), CASES)
async def test_meme_liste_sans_lire_les_fichiers(
    app: FastAPI,
    space: ApiSpace,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    extra: dict[str, Any],
) -> None:
    """Critères 1 et 2 : la même réponse qu'avec la lecture des fichiers (refus nommés
    compris), sans lire aucun fichier des sources."""
    args = {"space_id": space.space_id, "branch_id": space.branch_id}
    args.update(document_type=document_type, **extra)
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _call(app, KEY, args)
    seen = _no_source_file_read(monkeypatch)
    after = await _call(app, KEY, args)
    assert after == before
    assert seen == []


async def test_les_listes_rendent_ce_qu_on_attend(app: FastAPI, space: ApiSpace) -> None:
    """La comparaison porte sur des listes non vides : erreurs de manifeste, collecte."""
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    error, sources = await _call(app, KEY, {**ids, "document_type": "source"})
    assert not error
    by_slug = {d["identity"]["source"]: d for d in sources["documents"]}
    assert by_slug["sans"]["error"] == "manifest_missing"
    assert by_slug["autre"]["error"] == "slug_mismatch"
    assert by_slug["cassee"]["ok"] is False
    assert by_slug["boutique"]["ok"] is True and by_slug["boutique"]["version"]
    error, contracts = await _call(app, KEY, {**ids, "document_type": "contrat"})
    collected = {d["identity"]["version"]: d["collected"] for d in contracts["documents"]}
    assert collected == {"v1": True, "v2": False}
    error, rules = await _call(app, KEY, {**ids, "document_type": "regles_tri"})
    assert {d["identity"]["source"]: d["rules"] for d in rules["documents"]}["boutique"] == 1


async def test_source_enregistree_visible_tout_de_suite(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : une règle de tri enregistrée dans le brouillon compte aussitôt."""
    await draft_write(
        app,
        space,
        "sources/api/manifest.json",
        {"note": "pas un manifeste : la liste n'en tient pas compte"},
    )
    args = {
        "space_id": space.space_id,
        "branch_id": space.branch_id,
        "document_type": "source",
        "branch": "draft",
        "identity": {"source": "api"},
    }
    error, found = await _call(app, AGENT, args)
    assert not error, found
    assert [d["identity"]["source"] for d in found["documents"]] == ["api"]
    assert found["documents"][0]["unpublished_changes"] == 1
