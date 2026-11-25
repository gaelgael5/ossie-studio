"""Vue « Application » : déplacer les ancres des liens (epic « Cartographie
applicative dans raw », ticket a1fd0663). Le choix va dans le `.view.yaml` du
module de départ, dans le brouillon de la personne ; seule l'entrée du lien
change ; la collecte garde les ancres choisies."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.parsers.application import (
    DEFAULT_LINK,
    module_key,
    module_schema,
    set_view_link,
    view_file,
)
from ossie_studio.parsers.conventions import ConventionError, dump_schema, load_schema, schema_file
from ossie_studio.sources.explorer import clear_caches
from ossie_studio.sources.module_views import sync_module_views
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, forge_read, make_api_space
from tests.test_raw_explorer import _manifest

RAW = "sources/app/raw"
API = "src/Boutique.Api/Boutique.Api.csproj"
DOMAIN = "src/Boutique.Domain/Boutique.Domain.csproj"
INFRA = "src/Boutique.Infra/Boutique.Infra.csproj"
KNOWN = [API, DOMAIN, INFRA]
API_ID, DOMAIN_ID, INFRA_ID = (schema_file(module_key(p)) for p in KNOWN)
API_VIEW = view_file(module_key(API))
TOP = {"side": "top", "at": 0.5}


def _module(project: str, deps: tuple[str, ...] = ()) -> str:
    return dump_schema(
        module_schema(
            project=project,
            name=project.rsplit("/", 1)[-1].removesuffix(".csproj"),
            language="csharp",
            nature="library",
            dependencies=deps,
            known_projects=KNOWN,
        )
    )


def _view(links: dict[str, Any]) -> str:
    return dump_schema({"links": links})


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(
        space,
        {
            "sources/app/manifest.yaml": _manifest("app"),
            f"{RAW}/{API_ID}": _module(API, (DOMAIN, INFRA)),
            f"{RAW}/{DOMAIN_ID}": _module(DOMAIN),
            f"{RAW}/{INFRA_ID}": _module(INFRA, (DOMAIN,)),
            # Le lien Api → Infra a déjà une ancre choisie : elle doit rester.
            f"{RAW}/{API_VIEW}": _view(
                {
                    DOMAIN_ID: DEFAULT_LINK,
                    INFRA_ID: {"from": {"side": "bottom", "at": 0.25}, "to": "auto"},
                }
            ),
        },
    )
    return space


async def _put(client: AsyncClient, sp: ApiSpace, **body: Any) -> Any:
    payload = {"module": API_ID, "target": DOMAIN_ID, "from": "auto", "to": "auto", **body}
    return await client.put(f"{sp.base}/raw-explorer/app/module/link", json=payload)


async def _anchors(sp: ApiSpace) -> dict[str, Any]:
    r = await sp.client.get(f"{sp.base}/raw-explorer/app/application")
    assert r.status_code == 200, r.text
    return {m["id"]: m["anchors"] for m in r.json()["modules"]}


async def _draft_view(app: FastAPI, sp: ApiSpace) -> dict[str, Any]:
    workdir = await admin_workdir(app, sp)
    text = await workdir.read_file(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, f"{RAW}/{API_VIEW}"
    )
    assert text is not None
    data: dict[str, Any] = load_schema(text)
    return data


# --- Critères 1 et 2 : déplacer l'arrivée, écrit dans le brouillon, gardé à la réouverture --


async def test_deplacer_l_arrivee_d_un_lien(app: FastAPI, sp: ApiSpace) -> None:
    r = await _put(sp.client, sp, to=TOP)
    assert r.status_code == 200, r.text
    assert r.json() == {"module": API_ID, "target": DOMAIN_ID, "from": "auto", "to": TOP}
    # Écrit dans le `.view` du module de départ, dans le brouillon ; l'autre entrée
    # est gardée telle quelle.
    view = await _draft_view(app, sp)
    assert view["links"] == {
        DOMAIN_ID: {"from": "auto", "to": TOP},
        INFRA_ID: {"from": {"side": "bottom", "at": 0.25}, "to": "auto"},
    }
    # La publication n'a rien reçu : le brouillon seul porte le changement.
    published = load_schema(forge_read(sp, f"{RAW}/{API_VIEW}") or "")
    assert published["links"][DOMAIN_ID] == DEFAULT_LINK
    # Rouvrir la vue : l'ancre est appliquée (les liens sans choix n'apparaissent pas).
    clear_caches()
    anchors = await _anchors(sp)
    assert anchors[API_ID] == {
        DOMAIN_ID: {"from": "auto", "to": TOP},
        INFRA_ID: {"from": {"side": "bottom", "at": 0.25}, "to": "auto"},
    }
    assert anchors[DOMAIN_ID] == {}
    # Même choix une seconde fois : rien à écrire.
    again = await _put(sp.client, sp, to=TOP)
    assert again.status_code == 200


# --- Critère 5 : remettre l'ancre calculée ------------------------------------------------


async def test_remettre_l_ancre_calculee(app: FastAPI, sp: ApiSpace) -> None:
    await _put(sp.client, sp, to=TOP)
    r = await _put(sp.client, sp)  # auto aux deux bouts
    assert r.status_code == 200, r.text
    assert (await _draft_view(app, sp))["links"][DOMAIN_ID] == DEFAULT_LINK
    assert DOMAIN_ID not in (await _anchors(sp))[API_ID]


# --- Critère 6 : sans le droit de collecter, pas d'écriture ---------------------------------


async def test_droits_et_erreurs(app: FastAPI, sp: ApiSpace) -> None:
    await create_plain_user(app, "lecteur@example.org", "mot-de-passe-lecteur")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lecteur@example.org", "mot-de-passe-lecteur")
        assert (await _put(other, sp, to=TOP)).status_code == 403
    # Un lien que le module n'a pas, un module inconnu, une ancre hors bornes.
    assert (await _put(sp.client, sp, target=API_ID)).status_code == 404
    assert (await _put(sp.client, sp, module="inconnu.schema.yaml")).status_code == 404
    assert (await _put(sp.client, sp, to={"side": "top", "at": 2})).status_code == 422
    assert (await _put(sp.client, sp, to={"side": "milieu", "at": 0.5})).status_code == 422
    # Un `.view` illisible n'est jamais écrasé.
    forge_edit(sp, {f"{RAW}/{API_VIEW}": "links: [pas, valide"})
    clear_caches()
    refused = await _put(sp.client, sp, to=TOP)
    assert refused.status_code == 409
    assert refused.json()["error"] == "view_unreadable"


# --- Critères 3 et 4 : la collecte garde les ancres, retire celle d'un lien disparu -------


def test_la_collecte_garde_les_ancres() -> None:
    existing = {
        API_ID: _module(API, (DOMAIN, INFRA)),
        DOMAIN_ID: _module(DOMAIN),
        INFRA_ID: _module(INFRA, (DOMAIN,)),
        API_VIEW: _view(
            {DOMAIN_ID: {"from": "auto", "to": TOP}, INFRA_ID: {"from": TOP, "to": "auto"}}
        ),
        view_file(module_key(INFRA)): _view({DOMAIN_ID: DEFAULT_LINK}),
        view_file(module_key(DOMAIN)): _view({}),
    }
    # Recollecte sans changement : aucun `.view` réécrit.
    writes, deletes = sync_module_views(existing, [], [])
    assert (writes, deletes) == ([], [])
    # Domain supprimé de la source : son entrée disparaît, l'autre reste.
    writes, deletes = sync_module_views(existing, [], [DOMAIN_ID])
    by_path = {w.path: load_schema(w.content) for w in writes}
    assert by_path[API_VIEW]["links"] == {INFRA_ID: {"from": TOP, "to": "auto"}}
    assert view_file(module_key(DOMAIN)) in deletes


def test_une_seule_entree_change() -> None:
    text = _view({DOMAIN_ID: DEFAULT_LINK, INFRA_ID: {"from": TOP, "to": "auto"}})
    updated = set_view_link(text, DOMAIN_ID, {"from": "auto", "to": TOP})
    assert updated is not None
    assert load_schema(updated)["links"][INFRA_ID] == {"from": TOP, "to": "auto"}
    assert set_view_link(updated, DOMAIN_ID, {"from": "auto", "to": TOP}) is None
    # Fichier absent : créé avec cette seule entrée (la collecte complétera).
    created = set_view_link(None, DOMAIN_ID, DEFAULT_LINK)
    assert created is not None and load_schema(created) == {"links": {DOMAIN_ID: DEFAULT_LINK}}
    with pytest.raises(ConventionError):
        set_view_link("links: [", DOMAIN_ID, DEFAULT_LINK)
