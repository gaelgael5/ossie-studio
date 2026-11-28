"""Lecture des référentiels publiés (epic « Référentiels ») : l'API HTTP
(ticket 2c321d50) et le service MCP (ticket e65b590b), même lecture, même droit.

Seule la version publiée est servie, jamais un brouillon ; chaque réponse de l'API
dit de quel commit publié elle provient. Côté MCP, les référentiels se lisent par
les primitives communes `list_documents`, `read_document` et `read_schema` (ticket
2113e7bc) ; le droit d'une clé d'API sur l'API HTTP suit ces mêmes primitives."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.api.branches import space_credentials
from ossie_studio.mcp_surface.catalog import BY_ID
from ossie_studio.referentiels.model import dump_model
from ossie_studio.referentiels.repo import model_path, values_path, write_referentiel
from ossie_studio.referentiels.service import ReferentielTarget
from ossie_studio.referentiels.values import dump_values
from tests.mcp_helpers import call_tool, mcp_client, tool_names
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space

READS = {"list_documents", "read_document", "read_schema"}

MODEL: dict[str, Any] = {
    "title": "Délais de livraison",
    "description": "Délai par pays et mode.",
    "type": "object",
    "x-key": ["pays", "mode"],
    "required": ["pays", "mode"],
    "properties": {
        "pays": {"type": "string"},
        "mode": {"type": "string", "enum": ["site", "domicile"]},
        "actif": {"type": "boolean"},
        "delai": {"type": "integer"},
        "paliers": {
            "type": "array",
            "items": {"type": "object", "properties": {"poids_max": {"type": "number"}}},
        },
    },
}
VALUES: list[dict[str, Any]] = [
    {"pays": "FR", "mode": "site", "actif": True, "delai": 2, "paliers": [{"poids_max": 1}]},
    {"pays": "FR", "mode": "domicile", "actif": True, "delai": 3},
    {"pays": "BE", "mode": "site", "actif": False, "delai": 4},
    {"pays": "DE", "mode": "site", "actif": True, "delai": 5},
]


def _publish(space: ApiSpace, values: list[dict[str, Any]]) -> str:
    """Publie le référentiel « delais » (commit sur la forge) ; rend le commit."""
    forge_edit(
        space,
        {
            model_path("delais"): dump_model(MODEL),
            values_path("delais"): dump_values(MODEL, values),
        },
    )
    return subprocess.run(
        ["git", "--git-dir", space.url.removeprefix("file://"), "rev-parse", "work/principale"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


@pytest.fixture
async def anonymous(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def _key(app: FastAPI, client: AsyncClient, primitives: list[str]) -> str:
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ($1, $2, false) "
            "RETURNING id",
            f"profil-{uuid.uuid4().hex[:6]}",
            primitives,
        )
    response = await client.post(
        "/api/api-keys", json={"label": "Consommateur", "agent_profile_id": str(profile)}
    )
    assert response.status_code == 201, response.text
    secret: str = response.json()["secret"]
    return secret


def _base(space: ApiSpace) -> str:
    return f"/api/spaces/{space.space_id}/referentiels"


async def _get(http: AsyncClient, url: str, key: str | None = None, **params: Any) -> Any:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return await http.get(url, params=params, headers=headers)


# --- Critères 1 et 2 : la version publiée, jamais un brouillon ; le commit suit -------------------


async def test_sert_la_version_publiee_jamais_le_brouillon(app: FastAPI, space: ApiSpace) -> None:
    first = _publish(space, VALUES[:2])
    listing = (await space.client.get(_base(space))).json()
    assert listing == {
        "commit": first,
        "referentiels": [
            {
                "name": "delais",
                "title": "Délais de livraison",
                "description": "Délai par pays et mode.",
                "key": ["pays", "mode"],
                "tags": [],
                "count": 2,
            }
        ],
    }

    # Une modification dans le brouillon de la personne : jamais servie.
    async with app.state.pool.acquire() as conn:
        git_space, username, token = await space_credentials(
            conn, app.state.crypto, uuid.UUID(space.space_id)
        )
    target = ReferentielTarget(
        await admin_workdir(app, space), git_space, "work/principale", username, token, "a@x"
    )
    await target.write(
        "delais",
        lambda root: write_referentiel(root, "delais", MODEL, VALUES, create=False),
    )
    after_draft = (await space.client.get(_base(space))).json()
    assert after_draft["commit"] == first
    assert after_draft["referentiels"][0]["count"] == 2

    # Publié : la nouvelle version est servie, le commit change.
    second = _publish(space, VALUES)
    assert second != first
    published = (await space.client.get(_base(space))).json()
    assert (published["commit"], published["referentiels"][0]["count"]) == (second, 4)
    model = (await space.client.get(f"{_base(space)}/delais/modele")).json()
    assert (model["commit"], model["model"]) == (second, MODEL)


# --- Critère 3 : lecture par clé composée ---------------------------------------------------------


async def test_lecture_par_cle_composee(space: ApiSpace) -> None:
    commit = _publish(space, VALUES)
    url = f"{_base(space)}/delais/valeurs/cle"
    found = await space.client.get(url, params={"key": ["pays:FR", "mode:domicile"]})
    assert found.status_code == 200, found.text
    assert found.json() == {"commit": commit, "name": "delais", "value": VALUES[1]}

    incomplete = await space.client.get(url, params={"key": ["pays:FR"]})
    assert incomplete.status_code == 400
    assert incomplete.json()["error"] == "referentiel_key_incomplete"
    assert incomplete.json()["error_args"]["expected"] == ["pays", "mode"]
    assert incomplete.json()["error_args"]["missing"] == ["mode"]

    absent = await space.client.get(url, params={"key": ["pays:IT", "mode:site"]})
    assert absent.status_code == 404
    assert absent.json()["error"] == "referentiel_value_not_found"


# --- Critère 4 : filtre et pagination, ordre stable -----------------------------------------------


async def test_filtre_et_pagination_ordre_stable(space: ApiSpace) -> None:
    _publish(space, VALUES)
    url = f"{_base(space)}/delais/valeurs"
    all_values = (await space.client.get(url)).json()
    # Tri par clé (pays, mode).
    assert [(v["pays"], v["mode"]) for v in all_values["values"]] == [
        ("BE", "site"),
        ("DE", "site"),
        ("FR", "domicile"),
        ("FR", "site"),
    ]
    page = (await space.client.get(url, params={"filter": "mode:site", "limit": 2})).json()
    assert [(v["pays"], page["total"], page["has_more"]) for v in page["values"]] == [
        ("BE", 3, True),
        ("DE", 3, True),
    ]
    rest = (
        await space.client.get(url, params={"filter": "mode:site", "limit": 2, "offset": 2})
    ).json()
    assert [v["pays"] for v in rest["values"]] == ["FR"] and rest["has_more"] is False
    typed = (await space.client.get(url, params={"filter": ["actif:true", "delai:3"]})).json()
    assert [(v["pays"], v["mode"]) for v in typed["values"]] == [("FR", "domicile")]
    unknown = await space.client.get(url, params={"filter": "paliers:1"})
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "referentiel_filter_unknown"


# --- Critère 5 : inexistant, rien de publié -------------------------------------------------------


async def test_inexistant_404_et_espace_sans_referentiel(space: ApiSpace) -> None:
    empty = (await space.client.get(_base(space))).json()
    assert empty["referentiels"] == []
    for path in ("/absent/modele", "/absent/valeurs"):
        response = await space.client.get(f"{_base(space)}{path}")
        assert response.status_code == 404, path
        assert response.json()["error"] == "referentiel_not_found"


# --- Critère 6 : authentification et droits (un seul droit avec le MCP) ---------------------------


async def test_authentification_et_droit_par_profil(
    app: FastAPI, client: AsyncClient, space: ApiSpace, anonymous: AsyncClient
) -> None:
    commit = _publish(space, VALUES)
    url = _base(space)
    assert (await _get(anonymous, url)).status_code == 401
    assert (await _get(anonymous, url, key="osk_inconnue")).status_code == 401

    reader = await _key(app, client, sorted(READS))
    response = await _get(anonymous, url, key=reader)
    assert response.status_code == 200, response.text
    assert response.json()["commit"] == commit

    # Un profil qui n'a que la liste (list_documents) : liste et recherche oui,
    # modèle non (read_schema).
    lister = await _key(app, client, ["list_documents"])
    assert (await _get(anonymous, url, key=lister)).status_code == 200
    assert (await _get(anonymous, f"{url}/delais/valeurs", key=lister)).status_code == 200
    denied = await _get(anonymous, f"{url}/delais/modele", key=lister)
    assert denied.status_code == 403

    # Révoquée : refusée.
    keys = (await client.get("/api/api-keys")).json()["keys"]
    for item in keys:
        await client.post(f"/api/api-keys/{item['id']}/revoke")
    assert (await _get(anonymous, url, key=reader)).status_code == 401


# --- Critère 7 : contrat OpenAPI ------------------------------------------------------------------


def test_contrat_openapi_decrit_les_operations(app: FastAPI) -> None:
    contract = app.openapi()
    base = "/api/spaces/{space_id}/referentiels"
    for path in ("", "/{name}/modele", "/{name}/valeurs", "/{name}/valeurs/cle"):
        operation = contract["paths"][f"{base}{path}"]["get"]
        assert {"HTTPBearer": []} in operation.get("security", []), path
    params = {p["name"] for p in contract["paths"][f"{base}/{{name}}/valeurs"]["get"]["parameters"]}
    assert {"filter", "limit", "offset"} <= params


# --- Service MCP : mêmes données, même droit ------------------------------------------------------


async def test_mcp_meme_lecture_que_l_api(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    _publish(space, VALUES)
    reader = await _key(app, client, sorted(READS))
    assert READS <= set(await tool_names(app, reader))
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}

    async def mcp(tool: str, **args: Any) -> dict[str, Any]:
        result = await call_tool(app, reader, tool, {**ids, **args})
        assert not result.is_error, result.content[0].text
        data: dict[str, Any] = result.structured_content
        return data

    # La liste : ce que rend l'API, avec le chemin et la version du modèle.
    listing = await mcp("list_documents", document_type="referentiel_modele")
    api_listing = (await space.client.get(_base(space))).json()
    [item] = listing["documents"]
    assert item["identity"] == {"referentiel": "delais"}
    assert item["path"] == "referentiels/delais/modele.schema.yaml" and item["version"]
    fields = ("title", "description", "key", "tags", "count")
    assert api_listing["referentiels"] == [{"name": "delais", **{f: item[f] for f in fields}}]

    # Le modèle se lit par read_schema, avec sa clé.
    model = await mcp("read_schema", document_type="referentiel_valeur", referentiel="delais")
    api_model = (await space.client.get(f"{_base(space)}/delais/modele")).json()
    assert model["kind"] == "referentiel" and model["schema"] == api_model["model"]
    assert model["schema"]["x-key"] == ["pays", "mode"]
    assert model["extras"] == {"name": "delais", "key": ["pays", "mode"]}

    # Une valeur par sa clé composée : read_document.
    key = {"pays": "FR", "mode": "site"}
    value = await mcp(
        "read_document",
        document_type="referentiel_valeur",
        identity={"referentiel": "delais", "key": key},
    )
    assert value["content"] == VALUES[0]

    # Recherche par égalité, bornée, avec indication qu'il en reste : les mêmes
    # valeurs, dans le même ordre, que l'API (et que search_referentiel_values).
    by_ref = {"referentiel": "delais"}
    found = await mcp(
        "list_documents",
        document_type="referentiel_valeur",
        identity=by_ref,
        filters={"equals": {"mode": "site"}},
        limit=1,
    )
    assert (len(found["documents"]), found["total"], found["has_more"]) == (1, 3, True)
    every = await mcp(
        "list_documents",
        document_type="referentiel_valeur",
        identity=by_ref,
        filters={"equals": {"mode": "site"}},
    )
    api_values = (
        await space.client.get(f"{_base(space)}/delais/valeurs", params={"filter": "mode:site"})
    ).json()
    assert [d["value"] for d in every["documents"]] == api_values["values"]
    assert every["documents"][0]["identity"]["key"] == {"pays": "BE", "mode": "site"}

    # Recherche texte sur les propriétés simples des valeurs.
    text = await mcp(
        "list_documents", document_type="referentiel_valeur", identity=by_ref, search="domi"
    )
    assert [d["value"] for d in text["documents"]] == [VALUES[1]]
    unknown = await call_tool(
        app,
        reader,
        "list_documents",
        {
            **ids,
            "document_type": "referentiel_valeur",
            "identity": by_ref,
            "filters": {"equals": {"x": 1}},
        },
    )
    assert unknown.is_error

    incomplete = await call_tool(
        app,
        reader,
        "read_document",
        {
            **ids,
            "document_type": "referentiel_valeur",
            "identity": {"referentiel": "delais", "key": {"pays": "FR"}},
        },
    )
    assert incomplete.is_error


async def test_mcp_profil_sans_acces_ne_voit_rien(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    other = await _key(app, client, ["list_spaces"])
    assert not READS & set(await tool_names(app, other))
    refused = await call_tool(
        app,
        other,
        "list_documents",
        {
            "space_id": space.space_id,
            "branch_id": space.branch_id,
            "document_type": "referentiel_modele",
        },
    )
    assert refused.is_error


async def test_aucune_primitive_d_ecriture(
    app: FastAPI, client: AsyncClient, space: ApiSpace
) -> None:
    for name in READS:
        assert BY_ID[name].access == "read", name
        assert not BY_ID[name].person_only, name
    reader = await _key(app, client, sorted(READS))
    async with mcp_client(app, reader) as mcp:
        tools = {t.name: t.annotations for t in (await mcp.list_tools()).tools}
    for name in READS:
        assert tools[name].read_only_hint is True, name
