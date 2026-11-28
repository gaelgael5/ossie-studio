"""La recherche passe par l'index (ticket 06171fd3, epic « Index des documents et
validation avant publication ») : list_documents, « qui utilise quoi », la recherche par
identité seule et l'écran des types de cartographie lisent l'index au lieu de parcourir
le dépôt.

Gael : l'index est « un outil de validation **et un outil de recherche** »."""

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
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

KEY = "index-key"
AGENT = "index-agent"
TYPES = {
    "application": ("Application", "ApplicationComponent"),
    "processus": ("Processus", "BusinessProcess"),
    "document": ("Document", "BusinessObject"),
    "acces": ("Accès", "Access"),
}
RELATION = "cartographie/objets/acces/rel-01.yaml"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n"
        )
        for slug, (title, arch) in TYPES.items()
    }
    files.update(
        {
            "cartographie/objets/application/app-01.yaml": (
                "cle: APP-01\ntype: application\nnom: Caisse centrale\n"
            ),
            "cartographie/objets/application/app-02.yaml": (
                "cle: APP-02\ntype: application\nnom: Stock\n"
            ),
            "cartographie/objets/processus/prc-01.yaml": (
                "cle: PRC-01\ntype: processus\nnom: Encaisser\n"
            ),
            "cartographie/objets/document/doc-01.yaml": (
                "cle: DOC-01\ntype: document\nnom: Ticket de caisse\n"
            ),
            # Une relation en clé seule (forme d'avant le ticket 421832ee) et une en type/CLÉ.
            RELATION: (
                "cle: REL-01\ntype: acces\nsource: PRC-01\ncible: document/DOC-01\nnom: Écrit\n"
            ),
            # Illisible : ignoré, comme par la lecture des fichiers.
            "cartographie/objets/application/casse.yaml": "cle: [ouvert\n",
        }
    )
    forge_edit(api_space, files)
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


async def _call(
    app: FastAPI, token: str, tool: str, space: ApiSpace, **args: Any
) -> tuple[bool, dict[str, Any]]:
    result = await call_tool(
        app, token, tool, {"space_id": space.space_id, "branch_id": space.branch_id, **args}
    )
    if result.is_error:
        return True, json.loads(result.content[0].text)
    data: dict[str, Any] = result.structured_content
    return False, data


async def _list(app: FastAPI, space: ApiSpace, token: str = KEY, **args: Any) -> dict[str, Any]:
    error, data = await _call(app, token, "list_documents", space, **args)
    assert not error, data
    return data


def _from_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_documents par la lecture des fichiers, comme avant le ticket."""
    real = document_reads.resolve_document_type

    def without_index(name: str) -> Any:
        return dataclasses.replace(real(name), index_lister=None)

    monkeypatch.setattr(document_reads, "resolve_document_type", without_index)


def _no_carto_file_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Compte les lectures de fichiers de la cartographie, et interdit sa lecture complète."""
    seen: list[str] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if "/cartographie/" in self.as_posix():
            seen.append(self.as_posix())
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        if "/cartographie/" in self.as_posix():
            seen.append(self.as_posix())
        return real_bytes(self)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read_cartographie ne doit pas être appelé")

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(document_type_lists, "read_cartographie", forbidden)
    return seen


@pytest.mark.parametrize(
    "args",
    [
        {"document_type": "carto_objet", "identity": {"type": "application"}, "search": "caisse"},
        {"document_type": "carto_objet"},
        {"document_type": "carto_objet", "filters": {"nature": "relation"}},
        {"document_type": "carto_objet", "filters": {"source": "PRC-01"}},
        {"document_type": "carto_objet", "filters": {"cible": "document/DOC-01"}},
        {"document_type": "carto_type"},
        {"document_type": "carto_type", "filters": {"archimate": "ApplicationComponent"}},
    ],
)
async def test_meme_liste_sans_lire_les_fichiers(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]
) -> None:
    """Critère 1 : list_documents rend le même résultat qu'avant, sans relire les
    fichiers de la cartographie."""
    with monkeypatch.context() as files:
        _from_files(files)
        before = await _list(app, space, **args)
    seen = _no_carto_file_read(monkeypatch)
    after = await _list(app, space, **args)
    assert after == before
    assert seen == []
    if args.get("search") == "caisse":
        assert [d["identity"] for d in after["documents"]] == [
            {"type": "application", "key": "APP-01"}
        ]


async def test_suppression_refusee_relations_de_l_index(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critère 2 : supprimer un objet visé par une relation est refusé ; la liste des
    relations vient de l'index (la suppression sur les fichiers n'est pas atteinte)."""
    from ossie_studio.api import cartographie_objects

    def not_reached(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("le refus doit venir de l'index")

    base = f"{space.base}/cartographie/objets"
    with monkeypatch.context() as patch:
        patch.setattr(cartographie_objects, "delete_object", not_reached)
        for ref in ("document/DOC-01", "processus/PRC-01"):
            refused = await space.client.delete(f"{base}/{ref}")
            assert refused.status_code == 409, refused.text
            body = refused.json()
            assert body["error"] == "carto_object_in_use"
            assert body["error_args"]["relations"] == ["acces/REL-01"]
    free = await space.client.delete(f"{base}/application/APP-02")
    assert free.status_code == 200, free.text


async def test_objet_enregistre_visible_tout_de_suite(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : un objet enregistré à l'instant apparaît dans list_documents
    immédiatement, avant toute passe de validation."""
    await _list(app, space, AGENT, document_type="carto_objet", branch="draft")
    created = await space.client.post(
        f"{space.base}/cartographie/objets",
        json={"type": "application", "key": "APP-03", "name": "Fidélité"},
    )
    assert created.status_code == 201, created.text
    found = await _list(
        app, space, AGENT, document_type="carto_objet", branch="draft", search="fidélité"
    )
    assert [d["identity"]["key"] for d in found["documents"]] == ["APP-03"]
    assert found["documents"][0]["unpublished"] is True
    async with app.state.pool.acquire() as conn:
        validated = await conn.fetchval(
            "SELECT count(*) FROM doc_index_view WHERE validated_at IS NOT NULL"
        )
    assert validated == 0
    # La vue publiée ne le montre pas.
    published = await _list(app, space, document_type="carto_objet", search="fidélité")
    assert published["documents"] == []


async def test_fichier_modifie_hors_application(app: FastAPI, space: ApiSpace) -> None:
    """Critère 4 : un fichier ajouté hors de l'application apparaît dans la recherche
    après la comparaison disque / index (faite à la lecture, la tête ayant bougé)."""
    assert (await _list(app, space, document_type="carto_objet", search="paiement"))["total"] == 0
    forge_edit(
        space,
        {
            "cartographie/objets/application/app-09.yaml": (
                "cle: APP-09\ntype: application\nnom: Paiement\n"
            )
        },
    )
    found = await _list(app, space, document_type="carto_objet", search="paiement")
    assert [d["identity"]["key"] for d in found["documents"]] == ["APP-09"]


async def test_lecture_par_cle_seule(app: FastAPI, space: ApiSpace) -> None:
    """Retrouver un document par son identité seule passe par l'index : une clé sans son
    type ; une clé portée par deux types est refusée avec les candidats."""
    error, found = await _call(
        app, KEY, "read_document", space, document_type="carto_objet", identity={"key": "APP-01"}
    )
    assert not error, found
    assert found["identity"] == {"type": "application", "key": "APP-01"}
    assert found["path"] == "cartographie/objets/application/app-01.yaml"
    error, missing = await _call(
        app, KEY, "read_document", space, document_type="carto_objet", identity={"key": "NOPE"}
    )
    assert error and missing["error"] == "document_not_found"
    forge_edit(
        space,
        {
            "cartographie/objets/processus/app-01.yaml": (
                "cle: APP-01\ntype: processus\nnom: Homonyme\n"
            )
        },
    )
    error, twice = await _call(
        app, KEY, "read_document", space, document_type="carto_objet", identity={"key": "APP-01"}
    )
    assert error and twice["error"] == "identity_ambiguous"
    assert {c["type"] for c in twice["candidates"]} == {"application", "processus"}


async def test_ecran_des_types_par_l_index(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'écran des types lit l'index : mêmes comptes, sans lire la cartographie."""
    from ossie_studio.cartographie import service

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read_cartographie ne doit pas être appelé")

    monkeypatch.setattr(service, "read_cartographie", forbidden)
    answer = await space.client.get(f"{space.base}/cartographie/types")
    assert answer.status_code == 200, answer.text
    counts = {t["slug"]: t["objects"] for t in answer.json()["types"]}
    assert counts == {"acces": 1, "application": 2, "document": 1, "processus": 1}
    titles = [t["title"] for t in answer.json()["types"]]
    assert titles == sorted(titles, key=str.casefold)


async def test_ecran_des_objets_par_l_index(
    app: FastAPI, space: ApiSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'écran des objets lit l'index (Gael, 28/11 : « oui ») : extrait de la
    documentation, premières propriétés, nombre de relations et extrémités, sans lire
    la cartographie."""
    from ossie_studio.api import cartographie_objects
    from ossie_studio.cartographie import service

    forge_edit(
        space,
        {
            "cartographie/objets/application/app-02.yaml": (
                "cle: APP-02\ntype: application\nnom: Stock\n"
                'documentation: "# Gestion du **stock**"\n'
                "proprietes:\n  a: 1\n  b: ''\n  c: true\n  d: [x, y]\n  e: z\n"
            )
        },
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read_cartographie ne doit pas être appelé")

    monkeypatch.setattr(service, "read_cartographie", forbidden)
    monkeypatch.setattr(cartographie_objects, "read_cartographie", forbidden)
    answer = await space.client.get(f"{space.base}/cartographie/objets")
    assert answer.status_code == 200, answer.text
    objects = {o["ref"]: o for o in answer.json()["objects"]}
    stock = objects["application/APP-02"]
    assert stock["excerpt"] == "Gestion du stock"
    assert stock["properties"] == [
        {"name": "a", "value": "1"},
        {"name": "c", "value": "oui"},
        {"name": "d", "value": "2 élément(s)"},
    ]
    assert (stock["type_title"], stock["archimate"]) == ("Application", "ApplicationComponent")
    relation = objects["acces/REL-01"]
    assert relation["is_relation"] is True
    assert (relation["source"], relation["target"]) == ("processus/PRC-01", "document/DOC-01")
    assert objects["processus/PRC-01"]["relations"] == 1
    assert objects["document/DOC-01"]["relations"] == 1
    names = [o["name"] for o in answer.json()["objects"]]
    assert names == sorted(names, key=str.casefold)
