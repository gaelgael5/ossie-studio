"""Recherche par l'index (3/6, ticket fc5bb3b4) : domaines, datasets, relations et
références de datasets listés par l'index des documents, sans relire `domaines/`."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.domains.documents import (
    Dataset,
    Domain,
    DomainField,
    Reference,
    Relationship,
    write_domain,
)
from ossie_studio.mcp_surface import document_reads, document_type_domains
from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

KEY = "dom-index-key"


def _domain_files(tmp: Path, domain: Domain) -> dict[str, str | None]:
    root = tmp / f"domaine-{domain.name}"
    write_domain(root, domain)
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file()}


VENTES = Domain(
    name="ventes",
    label="Ventes",
    description="Le domaine des ventes.",
    datasets=[
        Dataset("Commandes", "raw:boutique/commandes", True, False, description="Les commandes.",
                fields=[DomainField("id", "id"), DomainField("client", "client_id")],
                primary_key=["id"]),
        Dataset("Clients", "raw:boutique/clients", True, False,
                fields=[DomainField("id", "id")], primary_key=["id"]),
        Dataset("Paniers", "raw:boutique/paniers", False, True, fields=[DomainField("id", "id")]),
    ],
    relationships=[Relationship("commande_client", "Commandes", "Clients", ["client"], ["id"])],
    references=[Reference("panier_client", "Paniers", "id", "Clients", "field", "n:1")],
)  # fmt: skip


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files = _domain_files(tmp_path, VENTES)
    files["domaines/stock.extend.yaml"] = "label: [ouvert\n"  # illisible
    files["domaines/rh.extend.yaml"] = "version: 1\nlabel: RH\ndatasets:\n  Orphelin: {}\n"
    files["domaines/rh.ossie.yaml"] = "version: '0.1.1'\nsemantic_model: 3\n"  # invalide
    forge_edit(api_space, files)
    async with app.state.pool.acquire() as conn:
        lecture = await conn.fetchval("SELECT id FROM agent_profiles WHERE slug = 'lecture'")
    caller = McpCaller(kind="key", ref="k", agent_profile_id=lecture)

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == KEY else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(app, KEY, "list_documents", args)
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


def _from_files(monkeypatch: pytest.MonkeyPatch) -> None:
    real = document_reads.resolve_document_type

    def without_index(name: str) -> Any:
        return dataclasses.replace(real(name), index_lister=None)

    monkeypatch.setattr(document_reads, "resolve_document_type", without_index)


def _no_domain_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if "/domaines/" in self.as_posix():
            seen.append(self.as_posix())
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        if "/domaines/" in self.as_posix():
            seen.append(self.as_posix())
        return real_bytes(self)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("les fichiers des domaines ne doivent pas être lus")

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(document_type_domains, "list_domains", forbidden)
    return seen


CASES: list[tuple[str, dict[str, Any]]] = [
    ("domaine", {}),
    ("domaine", {"identity": {"name": "ventes"}}),
    ("domaine", {"search": "ventes"}),
    ("dataset", {}),
    ("dataset", {"identity": {"domain": "ventes"}}),
    ("dataset", {"identity": {"domain": "ventes", "name": "Clients"}}),
    ("relation_dataset", {}),
    ("relation_dataset", {"identity": {"domain": "ventes"}}),
    ("reference_dataset", {}),
    ("reference_dataset", {"identity": {"domain": "ventes"}}),
]


@pytest.mark.parametrize(("document_type", "extra"), CASES)
async def test_meme_liste_sans_lire_les_fichiers(
    app: FastAPI,
    space: ApiSpace,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    extra: dict[str, Any],
) -> None:
    """Critères 1 et 2 : la même réponse qu'avec la lecture des fichiers, sans lire
    aucun fichier des domaines."""
    args = {"space_id": space.space_id, "branch_id": space.branch_id}
    args.update(document_type=document_type, **extra)
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _call(app, args)
    seen = _no_domain_file_read(monkeypatch)
    after = await _call(app, args)
    assert after == before
    assert seen == []


async def test_les_listes_rendent_ce_qu_on_attend(app: FastAPI, space: ApiSpace) -> None:
    """La comparaison porte sur des listes non vides, domaines illisibles compris."""
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    error, domains = await _call(app, {**ids, "document_type": "domaine"})
    assert not error
    by_name = {d["identity"]["name"]: d for d in domains["documents"]}
    assert set(by_name) == {"rh", "stock", "ventes"}
    assert by_name["stock"]["warnings"][0].startswith("document_unreadable")
    assert by_name["rh"]["warnings"][0].startswith(("extend_invalid", "ossie_invalid"))
    assert by_name["ventes"]["datasets"] == 3
    error, datasets = await _call(app, {**ids, "document_type": "dataset"})
    assert {d["identity"]["name"] for d in datasets["documents"]} == {
        "Commandes",
        "Clients",
        "Paniers",
    }


async def test_domaine_ajoute_hors_application(
    app: FastAPI, space: ApiSpace, tmp_path: Path
) -> None:
    """Critère 4 : un domaine ajouté par un commit extérieur apparaît à la lecture
    suivante."""
    forge_edit(space, _domain_files(tmp_path, Domain(name="achats", label="Achats")))
    error, found = await _call(
        app,
        {"space_id": space.space_id, "branch_id": space.branch_id, "document_type": "domaine"},
    )
    assert not error and "achats" in {d["identity"]["name"] for d in found["documents"]}
