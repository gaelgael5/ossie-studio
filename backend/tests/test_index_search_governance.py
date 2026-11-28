"""Recherche par l'index (5/6, ticket bb279dd6) : tags, classifications, certifications,
règles et profils de relation listés par l'index des documents, sans relire
`governance/`."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.governance.files import DiskFiles, IndexFiles, file_facts
from ossie_studio.mcp_surface import document_reads, document_type_lists
from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "gov-index-key"
AGENT = "gov-index-agent"

GOVERNANCE = {
    # Tags : hiérarchie par dossiers, id déduit du chemin, fichier illisible ignoré.
    "governance/tags/metier.yaml": "id: metier\ninfo: {owner: gael, description: Le métier}\n",
    "governance/tags/metier/ventes.yaml": (
        "id: metier/ventes\ninfo: {owner: gael, description: Les ventes}\n"
        "links: {doc: 'https://exemple.org/ventes'}\nproperties: {niveau: 2}\n"
    ),
    "governance/tags/technique.yaml": "info: {description: Sans id}\n",
    "governance/tags/cassee.yaml": "id: [ouvert\n",
    "governance/tags/LISEZMOI.md": "pas un tag\n",
    "governance/certifications/or.yaml": "id: or\nname: Or\nrank: 1\nslug: or\n",
    "governance/certifications/argent.yaml": "name: Argent\nrank: 2\nslug: argent\n",
    "governance/classifications/confidentialite.yaml": (
        "id: confidentialite\nname: Confidentialité\ndisplay_order: 1\n"
        "levels:\n- {id: c1, name: public, rank: 1}\n- {id: c2, name: interne, rank: 2}\n"
    ),
    "governance/classifications/rgpd.yaml": (
        "id: rgpd\nname: RGPD\nlevels:\n- {id: r1, name: personnelle}\n"
    ),
    # Règles de tags : valide, invalide, illisible ; une règle native désactivée.
    "governance/tag-rules/controleurs.yaml": (
        "name: Contrôleurs\nfiles: ['*.cs']\nwhen: {inherits: [ControllerBase]}\n"
        "tags: [technique/service]\n"
    ),
    "governance/tag-rules/mauvaise.yaml": "name: Mauvaise\nwhen: {inconnue: [x]}\ntags: [a]\n",
    "governance/tag-rules/cassee.yaml": "name: [ouvert\n",
    "governance/tag-rules-builtin.yaml": "disabled: [ossie-csharp-signalr-hubs]\n",
    # Règles de classification : valide, schéma inconnu ; une native désactivée ; les
    # retraits de classification ne changent pas les listes.
    "governance/classification-rules/emails.yaml": (
        "name: Emails\nwhen: {field: ['*email*']}\nscheme: rgpd\nvalues: [personnelle]\n"
    ),
    "governance/classification-rules/orpheline.yaml": (
        "name: Orpheline\nwhen: {field: [a]}\nscheme: inconnu\nvalues: [x]\n"
    ),
    "governance/classification-rules-builtin.yaml": "disabled: [ossie-rgpd-email]\n",
    "governance/classification-removals.yaml": "removals: []\n",
    # Profils : le profil fourni personnalisé, un profil de l'espace, un illisible.
    "governance/relation-profiles/ossie-datavault.yaml": (
        "name: Data Vault maison\nrules:\n"
        "- {column: '(?<nom>[A-Z]+)_HK', table: '{nom}_HUB', target_column: '{nom}_HK'}\n"
    ),
    "governance/relation-profiles/etoile.yaml": (
        "name: Étoile\ndescription: Les clés étrangères en _ID\nrules:\n"
        "- {column: '(?<nom>[A-Z]+)_ID', table: '{nom}', target_column: 'ID'}\n"
    ),
    "governance/relation-profiles/cassee.yaml": "rules: [ouvert\n",
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(api_space, dict(GOVERNANCE))
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


def _no_governance_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if "/governance/" in self.as_posix():
            seen.append(self.as_posix())
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        if "/governance/" in self.as_posix():
            seen.append(self.as_posix())
        return real_bytes(self)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("les fichiers de la gouvernance ne doivent pas être lus")

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(document_type_lists, "DiskFiles", forbidden)
    return seen


CASES: list[tuple[str, dict[str, Any]]] = [
    ("tag", {}),
    ("tag", {"identity": {"id": "metier/ventes"}}),
    ("tag", {"search": "ventes"}),
    ("classification", {}),
    ("classification", {"identity": {"id": "rgpd"}}),
    ("certification", {}),
    ("certification", {"search": "argent"}),
    ("regle_tag", {}),
    ("regle_tag", {"identity": {"id": "controleurs"}}),
    ("regle_tag", {"search": "csharp"}),
    ("regle_classification", {}),
    ("regle_classification", {"identity": {"id": "orpheline"}}),
    ("profil_relation", {}),
    ("profil_relation", {"search": "étoile"}),
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
    aucun fichier de la gouvernance."""
    args = {"space_id": space.space_id, "branch_id": space.branch_id}
    args.update(document_type=document_type, **extra)
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _call(app, KEY, args)
    seen = _no_governance_file_read(monkeypatch)
    after = await _call(app, KEY, args)
    assert after == before
    assert seen == []


async def test_les_listes_rendent_ce_qu_on_attend(app: FastAPI, space: ApiSpace) -> None:
    """Critère 5 et non-vacuité : natives au même rang, fichiers illisibles traités comme
    avant (ignorés pour les objets, listés avec leur erreur pour les règles et profils)."""
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}

    async def listed(document_type: str) -> list[dict[str, Any]]:
        error, found = await _call(app, KEY, {**ids, "document_type": document_type})
        assert not error, found
        documents: list[dict[str, Any]] = found["documents"]
        return documents

    tags = await listed("tag")
    assert [t["identity"]["id"] for t in tags] == ["metier", "metier/ventes", "technique"]
    assert all(t["version"] for t in tags)
    rules = {r["identity"]["id"]: r for r in await listed("regle_tag")}
    assert rules["ossie-csharp-signalr-hubs"]["enabled"] is False
    assert rules["cassee"]["errors"] == ["tag_rule_unreadable"]
    assert rules["mauvaise"]["errors"] and rules["controleurs"]["errors"] == []
    assert next(iter(rules)).startswith("ossie-")  # les natives d'abord
    classes = {r["identity"]["id"]: r for r in await listed("regle_classification")}
    assert classes["ossie-rgpd-email"]["enabled"] is False
    assert classes["orpheline"]["effect_errors"]
    profiles = {p["identity"]["id"]: p for p in await listed("profil_relation")}
    assert profiles["ossie-datavault"]["customized"] is True
    assert profiles["cassee"]["errors"] == ["relation_profile_unreadable"]
    assert {c["identity"]["slug"] for c in await listed("certification")} == {"or", "argent"}


async def test_tag_enregistre_visible_tout_de_suite(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : un tag enregistré dans le brouillon apparaît aussitôt, sans passe de
    validation."""
    await draft_write(
        app, space, "governance/tags/nouveau.yaml", {"id": "nouveau", "info": {"owner": "x"}}
    )
    args = {
        "space_id": space.space_id,
        "branch_id": space.branch_id,
        "document_type": "tag",
        "branch": "draft",
        "identity": {"id": "nouveau"},
    }
    error, found = await _call(app, AGENT, args)
    assert not error, found
    assert [d["identity"]["id"] for d in found["documents"]] == ["nouveau"]
    assert found["documents"][0]["unpublished"] is True


async def test_profil_ajoute_hors_application(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : un fichier ajouté par un commit extérieur apparaît à la lecture
    suivante."""
    forge_edit(space, {"governance/certifications/bronze.yaml": "name: Bronze\nslug: bronze\n"})
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    error, found = await _call(app, KEY, {**ids, "document_type": "certification"})
    assert not error and "bronze" in {d["identity"]["slug"] for d in found["documents"]}


def test_l_index_garde_ce_que_les_listes_lisent(tmp_path: Path) -> None:
    """L'accès par l'index rend les mêmes fichiers, dans le même ordre, que le disque ; il
    ne garde pas les fichiers que les listes ne lisent pas."""
    files: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for rel, text in GOVERNANCE.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        files[rel] = ("blob", file_facts(rel, text.encode()))
    disk, index = DiskFiles(tmp_path), IndexFiles(files)
    for folder, recursive in [
        ("governance/tags", True),
        ("governance/tag-rules", False),
        ("governance/relation-profiles", False),
        ("governance/absent", True),
    ]:
        listed = disk.yaml_files(folder, recursive=recursive)
        assert index.yaml_files(folder, recursive=recursive) == listed
        assert all(index.read_text(rel) == disk.read_text(rel) for rel in listed)
    assert file_facts("governance/classification-removals.yaml", b"removals: []\n") is None
    assert file_facts("governance/tags/x.yaml", b"\xff") == {"governance_text": None}
    assert file_facts("sources/a/manifest.yaml", b"slug: a\n") is None
    with pytest.raises(UnicodeDecodeError):
        IndexFiles({"governance/tags/x.yaml": ("b", {"governance_text": None})}).read_text(
            "governance/tags/x.yaml"
        )
    with pytest.raises(FileNotFoundError):
        index.read_text("governance/tags/absent.yaml")
