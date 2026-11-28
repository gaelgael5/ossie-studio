"""Recherche par l'index (2/6, ticket c854ad1e) : modèles et valeurs des référentiels
listés par l'index des documents, sans relire `referentiels/`.

Gael : « oui » à garder les valeurs entières dans l'index, pour qu'il n'y ait qu'un
seul index."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.mcp_surface import document_reads, document_type_lists
from ossie_studio.mcp_surface.caller import McpCaller
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space

KEY = "ref-index-key"
AGENT = "ref-index-agent"
PAYS = {
    "title": "Pays",
    "description": "Les pays.",
    "type": "object",
    "x-key": ["code"],
    "x-tags": ["geo"],
    "required": ["code", "nom"],
    "properties": {"code": {"type": "string"}, "nom": {"type": "string"}},
}
TARIFS = {
    "title": "Tarifs",
    "type": "object",
    "x-key": ["zone", "palier"],
    "required": ["zone", "palier", "prix"],
    "properties": {
        "zone": {"type": "string"},
        "palier": {"type": "integer"},
        "prix": {"type": "number"},
        "actif": {"type": "boolean"},
        "options": {"type": "object"},
    },
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(
        api_space,
        {
            "referentiels/pays/modele.schema.yaml": yaml.safe_dump(PAYS),
            "referentiels/pays/valeurs.json": json.dumps(
                [{"code": "FR", "nom": "France"}, {"code": "BE", "nom": "Belgique"}]
            ),
            "referentiels/tarifs/modele.schema.yaml": yaml.safe_dump(TARIFS),
            "referentiels/tarifs/valeurs.json": json.dumps(
                [
                    {"zone": "nord", "palier": 2, "prix": 9.5, "actif": True},
                    {"zone": "nord", "palier": 1, "prix": 12, "actif": False},
                    {"zone": "sud", "palier": 1, "prix": 10, "actif": True},
                ]
            ),
            # Valeurs illisibles : le référentiel est listé avec son erreur.
            "referentiels/casse/modele.schema.yaml": yaml.safe_dump({**PAYS, "title": "Casse"}),
            "referentiels/casse/valeurs.json": "[ouvert",
            # Une déclaration d'import illisible rend aussi le référentiel illisible.
            "referentiels/importe/modele.schema.yaml": yaml.safe_dump({**PAYS, "title": "Imp"}),
            "referentiels/importe/imports/fichier.yaml": "a: [b\n",
            # Sans modèle : ce n'est pas un référentiel.
            "referentiels/vide/valeurs.json": "[]",
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


def _ids(api_space: ApiSpace, **extra: Any) -> dict[str, Any]:
    return {"space_id": api_space.space_id, "branch_id": api_space.branch_id, **extra}


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


def _no_referentiel_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if "/referentiels/" in self.as_posix():
            seen.append(self.as_posix())
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        if "/referentiels/" in self.as_posix():
            seen.append(self.as_posix())
        return real_bytes(self)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("les fichiers des référentiels ne doivent pas être lus")

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    for name in ("list_referentiels", "read_referentiel"):
        monkeypatch.setattr(document_type_lists, name, forbidden)
    return seen


CASES: list[tuple[str, dict[str, Any]]] = [
    ("referentiel_modele", {}),
    ("referentiel_modele", {"search": "pays"}),
    ("referentiel_valeur", {}),
    ("referentiel_valeur", {"identity": {"referentiel": "pays"}}),
    ("referentiel_valeur", {"identity": {"referentiel": "tarifs"}}),
    (
        "referentiel_valeur",
        {"identity": {"referentiel": "pays"}, "filters": {"equals": {"code": "FR"}}},
    ),
    (
        "referentiel_valeur",
        {"identity": {"referentiel": "tarifs"}, "filters": {"equals": {"palier": 1}}},
    ),
    (
        "referentiel_valeur",
        {"identity": {"referentiel": "tarifs"}, "filters": {"equals": {"actif": True}}},
    ),
    (
        "referentiel_valeur",
        {"identity": {"referentiel": "tarifs"}, "filters": {"equals": {"options": 1}}},
    ),
    ("referentiel_valeur", {"filters": {"equals": {"code": "FR"}}}),
    ("referentiel_valeur", {"identity": {"referentiel": "casse"}}),
    ("referentiel_valeur", {"identity": {"referentiel": "importe"}}),
    ("referentiel_valeur", {"identity": {"referentiel": "inconnu"}}),
    ("referentiel_valeur", {"search": "belgique"}),
]


@pytest.mark.parametrize(("document_type", "extra"), CASES)
async def test_meme_liste_sans_lire_les_fichiers(
    app: FastAPI,
    space: ApiSpace,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    extra: dict[str, Any],
) -> None:
    """Critères 1 et 2 : pour chaque type et chaque filtre, la même réponse qu'avec la
    lecture des fichiers (refus nommés compris), sans lire aucun fichier des
    référentiels."""
    args = _ids(space, document_type=document_type, **extra)
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _call(app, KEY, args)
    seen = _no_referentiel_file_read(monkeypatch)
    after = await _call(app, KEY, args)
    assert after == before
    assert seen == []


async def test_les_listes_rendent_ce_qu_on_attend(app: FastAPI, space: ApiSpace) -> None:
    """La comparaison porte sur des listes non vides : erreurs, clés composées, tri."""
    error, models = await _call(app, KEY, _ids(space, document_type="referentiel_modele"))
    assert not error
    by_name = {d["identity"]["referentiel"]: d for d in models["documents"]}
    assert set(by_name) == {"casse", "importe", "pays", "tarifs"}
    assert by_name["casse"]["error"] == "referentiel_unreadable"
    assert by_name["importe"]["error"] == "referentiel_unreadable"
    assert (by_name["pays"]["count"], by_name["pays"]["tags"]) == (2, ["geo"])
    error, values = await _call(
        app,
        KEY,
        _ids(space, document_type="referentiel_valeur", identity={"referentiel": "tarifs"}),
    )
    assert [d["identity"]["key"] for d in values["documents"]] == [
        {"zone": "nord", "palier": 1},
        {"zone": "nord", "palier": 2},
        {"zone": "sud", "palier": 1},
    ]


async def test_valeur_enregistree_visible_tout_de_suite(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : une valeur enregistrée dans le brouillon apparaît aussitôt."""
    await draft_write(
        app,
        space,
        "referentiels/pays/valeurs.json",
        [{"code": "FR", "nom": "France"}, {"code": "IT", "nom": "Italie"}],
    )
    args = _ids(
        space,
        document_type="referentiel_valeur",
        branch="draft",
        identity={"referentiel": "pays"},
    )
    error, found = await _call(app, AGENT, args)
    assert not error, found
    assert [d["identity"]["key"] for d in found["documents"]] == ["FR", "IT"]


async def test_fichier_change_hors_application(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : un référentiel ajouté par un commit extérieur apparaît à la lecture
    suivante."""
    forge_edit(
        space,
        {
            "referentiels/devises/modele.schema.yaml": yaml.safe_dump({**PAYS, "title": "Dev"}),
            "referentiels/devises/valeurs.json": json.dumps([{"code": "EUR", "nom": "Euro"}]),
        },
    )
    error, found = await _call(
        app, KEY, _ids(space, document_type="referentiel_valeur", search="euro")
    )
    assert not error and [d["identity"] for d in found["documents"]] == [
        {"referentiel": "devises", "key": "EUR"}
    ]
