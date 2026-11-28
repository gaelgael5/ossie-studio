"""Lier des propriétés d'un type de cartographie à un référentiel (ticket 43478978).

Décisions de Gael (28/11) : le lien se déclare dans `<slug>.extend.yaml` à côté du
type ; un référentiel porte des tags libres ; une valeur absente du référentiel est
refusée à l'écran et par l'agent, signalée sur un objet déjà enregistré, et, à
l'import, la ligne est taguée et la personne peut la refuser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, CartoType
from ossie_studio.cartographie.referentiel_links import (
    ReferentielLink,
    dump_extend,
    parse_extend,
)
from ossie_studio.cartographie.space import (
    delete_type,
    read_cartographie,
    save_links,
    save_object,
    save_type,
)
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.referentiels.model import check_model
from ossie_studio.referentiels.published import summaries
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

PAYS_MODEL = (
    "title: Pays\ntype: object\nx-key: [code]\nx-tags: [entreprise]\n"
    "required: [code]\nproperties:\n  code:\n    type: string\n  libelle:\n    type: string\n"
)
PAYS_VALUES = [{"code": "DE", "libelle": "Allemagne"}, {"code": "FR", "libelle": "France"}]
SITES_MODEL = (
    "title: Sites\ntype: object\nx-key: [pays, site]\nx-tags: [contrainte]\n"
    "required: [pays, site]\nproperties:\n  pays:\n    type: string\n"
    "  site:\n    type: string\n  niveau:\n    type: integer\n"
)
SITES_VALUES = [{"pays": "FR", "site": "LYON"}, {"pays": "FR", "site": "PARIS"}]
APP_TYPE = (
    "title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
    "properties:\n  pays:\n    type: string\n  site_pays:\n    type: string\n"
    "  site:\n    type: string\n  niveau:\n    type: integer\n"
)
LINKS = "referentiels:\n- referentiel: pays\n  proprietes:\n    pays: code\n"


def _files(**extra: str | None) -> dict[str, str | None]:
    files: dict[str, str | None] = {
        "referentiels/pays/modele.schema.yaml": PAYS_MODEL,
        "referentiels/pays/valeurs.json": json.dumps(PAYS_VALUES),
        "referentiels/sites/modele.schema.yaml": SITES_MODEL,
        "referentiels/sites/valeurs.json": json.dumps(SITES_VALUES),
        "cartographie/types/application.schema.yaml": APP_TYPE,
        "cartographie/types/processus.schema.yaml": (
            "title: Processus\nx-archimate: BusinessProcess\ntype: object\n"
        ),
        "cartographie/types/application.extend.yaml": LINKS,
        "cartographie/objets/application/app-01.yaml": (
            "cle: APP-01\ntype: application\nnom: Caisse\nproprietes:\n  pays: FR\n"
        ),
        "cartographie/objets/application/app-02.yaml": (
            "cle: APP-02\ntype: application\nnom: Ancienne\nproprietes:\n  pays: IT\n"
        ),
    }
    files.update(extra)
    return files


def _root(tmp_path: Path, **extra: str | None) -> Path:
    for rel, text in _files(**extra).items():
        target = tmp_path / rel
        if text is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _codes(problems: list[Any]) -> list[tuple[str, str, str]]:
    return sorted((p.path, p.code, p.field) for p in problems)


# --- Tags des référentiels ----------------------------------------------------------------


def test_tags_libres_dans_le_modele(tmp_path: Path) -> None:
    base: dict[str, Any] = {"type": "object", "x-key": ["code"], "required": ["code"]}
    base["properties"] = {"code": {"type": "string"}}
    assert check_model({**base, "x-tags": ["entreprise", "contrainte"]}) == []
    assert [p.code for p in check_model({**base, "x-tags": ["a", "A"]})] == ["tags_duplicate"]
    for bad in ("entreprise", [""], [" x"], ["x" * 41], [1]):
        assert [p.code for p in check_model({**base, "x-tags": bad})] == ["tags_invalid"]
    root = _root(tmp_path)
    tags = {s["name"]: s["tags"] for s in summaries(root)}
    assert tags == {"pays": ["entreprise"], "sites": ["contrainte"]}


# --- Fichier d'extension --------------------------------------------------------------------


def test_extension_lue_et_ecrite() -> None:
    links = [ReferentielLink("sites", {"site_pays": "pays", "site": "site"})]
    links.append(ReferentielLink("pays", {"pays": "code"}))
    text = dump_extend(links)
    assert text.startswith("referentiels:\n- referentiel: pays\n")
    assert parse_extend(text) == sorted(links, key=lambda link: link.referentiel)
    assert parse_extend("") == []
    for bad in ("- a\n", "referentiels: x\n", "referentiels:\n- referentiel: pays\n"):
        with pytest.raises(CartographieError) as caught:
            parse_extend(bad)
        assert caught.value.code == "type_extend_unreadable"


def test_lecture_signale_liens_casses_et_valeurs_absentes(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        **{
            "cartographie/types/application.extend.yaml": (
                LINKS + "- referentiel: inconnu\n  proprietes:\n    site: code\n"
                "- referentiel: sites\n  proprietes:\n    niveau: site\n"
            ),
            "cartographie/types/fantome.extend.yaml": LINKS,
        },
    )
    space = read_cartographie(root)
    assert set(space.links) == {"application"}
    assert space.referentiels["pays"].tags == ["entreprise"]
    found = [p for p in space.problems if "referentiel" in p.code or "extend" in p.code]
    assert _codes(found) == [
        ("cartographie/objets/application/app-02.yaml", "property_referentiel_unknown", "pays"),
        ("cartographie/types/application.extend.yaml", "referentiel_link_incomplete", "niveau"),
        ("cartographie/types/application.extend.yaml", "referentiel_link_unknown", "site"),
        ("cartographie/types/fantome.extend.yaml", "type_extend_orphan", ""),
    ]
    absent = next(p for p in found if p.code == "property_referentiel_unknown")
    assert absent.detail == "pays : IT"


def test_lien_valide_ou_refuse(tmp_path: Path) -> None:
    root = _root(tmp_path)
    composite = ReferentielLink("sites", {"site_pays": "pays", "site": "site"})
    save_links(root, "application", [ReferentielLink("pays", {"pays": "code"}), composite])
    assert len(read_cartographie(root).links["application"]) == 2
    refusals = {
        "referentiel_link_incomplete": [ReferentielLink("sites", {"site": "site"})],
        "referentiel_link_type_mismatch": [ReferentielLink("pays", {"niveau": "code"})],
        "referentiel_link_field": [ReferentielLink("pays", {"absente": "code"})],
        "referentiel_link_unknown": [ReferentielLink("inconnu", {"pays": "code"})],
        "referentiel_link_taken": [
            ReferentielLink("pays", {"pays": "code"}),
            ReferentielLink("sites", {"pays": "pays", "site": "site"}),
        ],
    }
    for code, links in refusals.items():
        with pytest.raises(CartographieError) as caught:
            save_links(root, "application", links)
        assert caught.value.code == "type_links_invalid"
        assert code in [p.code for p in caught.value.problems], code
    save_links(root, "application", [])
    assert not (root / "cartographie/types/application.extend.yaml").exists()


# --- Écran : valeurs refusées ou signalées --------------------------------------------------


def _app(key: str, name: str = "App", **props: Any) -> CartoObject:
    return CartoObject(key=key, type="application", name=name, properties=props)


def test_valeur_absente_refusee_a_l_ecriture(tmp_path: Path) -> None:
    root = _root(tmp_path)
    save_object(root, _app("APP-03", pays="DE"), create=True)
    save_object(root, _app("APP-04"), create=True)  # propriété vide : rien à vérifier
    with pytest.raises(CartographieError) as caught:
        save_object(root, _app("APP-05", pays="ES"), create=True)
    [problem] = caught.value.problems
    assert (problem.code, problem.field, problem.detail) == (
        "property_referentiel_unknown",
        "pays",
        "pays : ES",
    )
    # Valeur déjà absente, inchangée : l'objet s'enregistre (signalé, pas refusé).
    save_object(root, _app("APP-02", "Renommée", pays="IT"), create=False)
    with pytest.raises(CartographieError):
        save_object(root, _app("APP-02", "Renommée", pays="ES"), create=False)


def test_cle_composee(tmp_path: Path) -> None:
    root = _root(tmp_path)
    save_links(
        root, "application", [ReferentielLink("sites", {"site_pays": "pays", "site": "site"})]
    )
    save_object(root, _app("APP-03", site_pays="FR", site="LYON"), create=True)
    with pytest.raises(CartographieError) as caught:
        save_object(root, _app("APP-04", site_pays="FR", site="NICE"), create=True)
    assert caught.value.problems[0].detail == "sites : FR+NICE"
    with pytest.raises(CartographieError):  # une partie seulement : absente
        save_object(root, _app("APP-05", site="LYON"), create=True)


def test_type_renomme_retire_ou_supprime(tmp_path: Path) -> None:
    root = _root(tmp_path)
    space = read_cartographie(root)
    current = space.types["application"]
    props = dict(current.properties)
    renamed = CartoType(
        "application",
        current.title,
        current.archimate,
        {("pays_code" if k == "pays" else k): v for k, v in props.items()},
    )
    save_type(root, renamed, create=False)
    assert read_cartographie(root).links["application"][0].properties == {"pays_code": "code"}
    removed = CartoType("application", current.title, current.archimate, {"site": props["site"]})
    with pytest.raises(CartographieError) as caught:
        save_type(root, removed, create=False)
    assert caught.value.code in ("property_linked", "property_in_use")
    save_links(root, "processus", [])
    (root / "cartographie/types/processus.extend.yaml").write_text(LINKS, encoding="utf-8")
    delete_type(root, "processus")
    assert not (root / "cartographie/types/processus.extend.yaml").exists()


def test_retirer_une_propriete_liee_sans_objet(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        **{
            "cartographie/objets/application/app-01.yaml": None,
            "cartographie/objets/application/app-02.yaml": None,
        },
    )
    current = read_cartographie(root).types["application"]
    props = {k: v for k, v in current.properties.items() if k != "pays"}
    with pytest.raises(CartographieError) as caught:
        save_type(root, CartoType("application", "A", current.archimate, props), create=False)
    assert caught.value.code == "property_linked"
    assert caught.value.detail["properties"] == {"pays": "pays"}
    retyped = dict(current.properties, pays={"type": "integer"})
    with pytest.raises(CartographieError) as caught:
        save_type(root, CartoType("application", "A", current.archimate, retyped), create=False)
    assert caught.value.code == "type_invalid"
    assert [p.code for p in caught.value.problems] == ["referentiel_link_type_mismatch"]


# --- Écran (API) ----------------------------------------------------------------------------


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    forge_edit(api_space, _files())
    return api_space


def _carto(space: ApiSpace, rest: str) -> str:
    return f"{space.base}/cartographie{rest}"


async def test_bloc_liens_du_type(space: ApiSpace) -> None:
    answer = await space.client.get(_carto(space, "/types/application/referentiels"))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    [link] = body["links"]
    assert (link["referentiel"], link["proprietes"], link["absent"]) == (
        "pays",
        {"pays": "code"},
        1,
    )
    assert link["target"]["tags"] == ["entreprise"]
    assert [r["name"] for r in body["referentiels"]] == ["pays", "sites"]
    assert {"name": "niveau", "title": "niveau", "type": "integer"} in body["properties"]
    composite = {"referentiel": "sites", "proprietes": {"site_pays": "pays", "site": "site"}}
    saved = await space.client.put(
        _carto(space, "/types/application/referentiels"),
        json={"links": [{"referentiel": "pays", "proprietes": {"pays": "code"}}, composite]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] and len(saved.json()["links"]) == 2
    assert saved.json()["state"] == "modified"
    refused = await space.client.put(
        _carto(space, "/types/application/referentiels"),
        json={"links": [{"referentiel": "sites", "proprietes": {"site": "site"}}]},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"] == "carto_type_links_invalid"
    choices = await space.client.get(_carto(space, "/types/application/referentiels/valeurs"))
    by_name = {c["referentiel"]: c for c in choices.json()["links"]}
    assert by_name["pays"]["choices"][1] == {
        "value": {"pays": "FR"},
        "label": "FR",
        "hint": "France",
    }
    assert by_name["sites"]["properties"] == ["site_pays", "site"]
    assert by_name["sites"]["choices"][0]["value"] == {"site_pays": "FR", "site": "LYON"}


async def test_fiche_et_ecriture_a_l_ecran(space: ApiSpace) -> None:
    fiche = (await space.client.get(_carto(space, "/objets/application/APP-02"))).json()
    [link] = fiche["referentiel_links"]
    assert (link["title"], link["value"], link["status"]) == ("Pays", "IT", "absent")
    assert any(p["code"] == "property_referentiel_unknown" for p in fiche["problems"])
    ok = (await space.client.get(_carto(space, "/objets/application/APP-01"))).json()
    assert ok["referentiel_links"][0]["status"] == "found"
    created = await space.client.post(
        _carto(space, "/objets"),
        json={"type": "application", "key": "APP-09", "name": "N", "properties": {"pays": "XX"}},
    )
    assert created.status_code == 400, created.text
    assert created.json()["error_args"]["problems"][0]["code"] == "property_referentiel_unknown"


# --- Import : lignes taguées --------------------------------------------------------------


TEMPLATE = '{"cle": $.cle, "nom": $.nom, "proprietes": {"pays": $.pays}}'


def _imports(space: ApiSpace, rest: str = "") -> str:
    return _carto(space, f"/types/application/imports{rest}")


async def _declare(space: ApiSpace, deletion: str = "keep") -> None:
    created = await space.client.post(
        _imports(space), json={"label": "Fichier", "slug": "fichier", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    await space.client.put(
        _imports(space, "/fichier/lecture"),
        json={"separator": ";", "encoding": "utf-8", "header": True},
    )
    await space.client.put(_imports(space, "/fichier/template"), json={"template": TEMPLATE})
    await space.client.put(_imports(space, "/fichier/suppression"), json={"deletion": deletion})


CSV = b"cle;nom;pays\nAPP-01;Caisse;FR\nAPP-02;Ancienne;ES\nAPP-03;Nouvelle;XX\nAPP-04;Ok;DE\n"


async def test_import_tague_puis_la_personne_refuse(space: ApiSpace) -> None:
    await _declare(space, deletion="all")
    url = _imports(space, "/fichier/import")
    upload = {"file": ("f.csv", CSV, "text/csv")}
    first = await space.client.post(url, files=upload)
    assert first.status_code == 409, first.text
    assert first.json()["error"] == "import_review_required"
    report = first.json()["error_args"]["report"]
    assert report["tagged"] == 2
    lines = {line["where"]: line for line in report["tagged_lines"]}
    assert set(lines) == {"ligne 3", "ligne 4"}
    assert lines["ligne 4"]["kind"] == "new"
    assert lines["ligne 4"]["tags"][0] == {
        "code": "property_referentiel_unknown",
        "property": "pays",
        "properties": ["pays"],
        "referentiel": "pays",
        "title": "Pays",
        "value": "XX",
    }
    assert (await space.client.get(_carto(space, "/objets/application/APP-04"))).status_code == 404
    # La personne refuse la ligne 3 (APP-02) et garde la ligne 4.
    done = await space.client.post(
        url, data={"reviewed": "true", "refuse": ["ligne 3"]}, files=upload
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["tagged"], body["refused"], body["new"], body["deleted"]) == (2, 1, 2, 0)
    app_02 = (await space.client.get(_carto(space, "/objets/application/APP-02"))).json()
    assert app_02["properties"] == {"pays": "IT"}  # refusée : ni écrite ni supprimée
    app_03 = (await space.client.get(_carto(space, "/objets/application/APP-03"))).json()
    assert app_03["referentiel_links"][0]["status"] == "absent"  # gardée : signalée


async def test_essai_montre_les_lignes_taguees(space: ApiSpace) -> None:
    await _declare(space)
    await space.client.put(
        _imports(space, "/fichier/exemple"), files={"file": ("e.csv", CSV, "text/csv")}
    )
    trial = await space.client.post(_imports(space, "/fichier/essai"), json={"template": TEMPLATE})
    assert trial.status_code == 200, trial.text
    assert trial.json()["tagged"] == 2 and trial.json()["refused"] == 0


# --- Agent ------------------------------------------------------------------------------------


AGENT = "links-agent"


async def _agent(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) "
            "VALUES ('links', $1, true) RETURNING id",
            ["write_documents"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(
        kind="oauth", ref="c", agent_profile_id=profile, user_id=admin_id, client_id="claude"
    )

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)


async def _tool(app: FastAPI, space: ApiSpace, tool: str, **args: Any) -> tuple[bool, Any]:
    result = await call_tool(
        app, AGENT, tool, {"space_id": space.space_id, "branch_id": space.branch_id, **args}
    )
    body = result.structured_content if not result.is_error else json.loads(result.content[0].text)
    return result.is_error, body


async def test_agent_refuse_une_valeur_absente(app: FastAPI, space: ApiSpace) -> None:
    await _agent(app)
    content = {"cle": "APP-08", "type": "application", "nom": "N", "proprietes": {"pays": "ES"}}
    entry = {
        "document_type": "carto_objet",
        "identity": {"type": "application", "key": "APP-08"},
        "content": content,
    }
    error, body = await _tool(app, space, "write_documents", message="m", documents=[entry])
    assert error, body
    [problem] = body["errors"]
    assert problem["code"] == "property_referentiel_unknown"
    assert problem["path"] == "$.documents[0].content.proprietes.pays"
    assert "référentiel pays" in problem["message"]
    content["proprietes"] = {"pays": "FR"}
    error, body = await _tool(app, space, "write_documents", message="m", documents=[entry])
    assert not error, body
    error, schema = await _tool(
        app, space, "read_schema", document_type="carto_objet", type="application", branch="draft"
    )
    assert not error, schema
    pays = schema["schema"]["properties"]["proprietes"]["properties"]["pays"]
    assert pays["x-referentiel"] == {"referentiel": "pays", "propriete": "code"}
