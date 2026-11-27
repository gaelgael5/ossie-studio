"""Imports d'un type de cartographie (ticket 49952be0) : une ligne = un objet du
type, comparaison par la clé, fusion seulement, validation par le service de la
cartographie, tout ou rien, journal ; même module que les référentiels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, make_api_space

TYPES = {
    "application": (
        "Application",
        "ApplicationComponent",
        "properties:\n  criticite:\n    type: string\n    enum: [haute, basse]\n",
    ),
    "processus": ("Processus", "BusinessProcess", ""),
    "document": ("Document", "BusinessObject", ""),
    "acces": ("Accès", "Access", ""),
    "composition": ("Composition", "Composition", ""),
}
PASSWORD = "motdepasse-solide"
APP_TEMPLATE = '{"cle": $.cle, "nom": $.nom, "proprietes": {"criticite": $.criticite}}'
REL_TEMPLATE = '{"cle": $.cle, "nom": $.nom, "source": $.source, "cible": $.cible}'


def _apps(count: int = 10, rename: dict[int, str] | None = None) -> bytes:
    lines = ["cle;nom;criticite"]
    for i in range(1, count + 1):
        name = (rename or {}).get(i, f"Application {i}")
        lines.append(f"APP-{i:02d};{name};haute")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n{extra}"
        )
        for slug, (title, arch, extra) in TYPES.items()
    }
    for key, ctype in (("APP-OLD", "application"), ("PRC-X", "processus"), ("DOC-Y", "document")):
        files[f"cartographie/objets/{ctype}/{key.lower()}.yaml"] = (
            f"cle: {key}\ntype: {ctype}\nnom: {key}\n"
        )
    forge_edit(space, files)
    return space


def _url(space: ApiSpace, type_slug: str, rest: str = "") -> str:
    return f"{space.base}/cartographie/types/{type_slug}/imports{rest}"


async def _declare(space: ApiSpace, type_slug: str, template: str) -> None:
    created = await space.client.post(
        _url(space, type_slug), json={"label": "Fichier", "slug": "fichier", "format": "csv"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["paths"][0] == f"cartographie/imports/{type_slug}/fichier.yaml"
    await space.client.put(
        _url(space, type_slug, "/fichier/lecture"),
        json={"separator": ";", "encoding": "utf-8", "header": True},
    )
    saved = await space.client.put(
        _url(space, type_slug, "/fichier/template"), json={"template": template}
    )
    assert saved.json()["template_problems"] == []


async def _import(space: ApiSpace, type_slug: str, content: bytes, mode: str = "merge") -> Any:
    return await space.client.post(
        _url(space, type_slug, "/fichier/import"),
        data={"mode": mode},
        files={"file": ("f.csv", content, "text/csv")},
    )


async def _object(space: ApiSpace, key: str) -> dict[str, Any] | None:
    answer = await space.client.get(f"{space.base}/cartographie/objets/{key}")
    return answer.json() if answer.status_code == 200 else None


async def test_importer_dix_applications_puis_reimporter(space: ApiSpace) -> None:
    """Critères 1, 2, 3 et 6."""
    await _declare(space, "application", APP_TEMPLATE)
    first = await _import(space, "application", _apps())
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["new"], body["modified"], body["unchanged"]) == (10, 0, 0)
    assert body["revision"].startswith("draft:") and body["last_import"]
    app_03 = await _object(space, "APP-03")
    assert app_03 is not None and app_03["type"] == "application"
    again = (await _import(space, "application", _apps())).json()
    assert (again["new"], again["unchanged"], again["revision"]) == (0, 10, None)
    renamed = (await _import(space, "application", _apps(rename={4: "Quatre"}))).json()
    assert (renamed["modified"], renamed["new"]) == (1, 0)
    app_04 = await _object(space, "APP-04") or {}
    assert (app_04["name"], app_04["key"], app_04["type"]) == ("Quatre", "APP-04", "application")
    assert await _object(space, "APP-OLD") is not None  # absent du fichier : inchangé


async def test_une_propriete_invalide_refuse_tout(space: ApiSpace) -> None:
    """Critère 4 : refus entier, la ligne et la propriété nommées."""
    await _declare(space, "application", APP_TEMPLATE)
    content = _apps(3).replace(b"APP-02;Application 2;haute", b"APP-02;Application 2;moyenne")
    refused = await _import(space, "application", content)
    assert refused.status_code == 422, refused.text
    report = refused.json()["error_args"]["report"]
    [error] = report["error_details"]
    assert (error["where"], error["key"]) == ("ligne 3", "APP-02")
    # Même code, même champ et même message que par write_documents (ticket 1612cce4).
    assert (error["code"], error["field"]) == ("carto_object_invalid", "$.proprietes.criticite")
    assert error["message"].startswith("criticite doit être")
    assert await _object(space, "APP-01") is None


async def test_importer_des_relations(space: ApiSpace) -> None:
    """Critère 5 : une relation par ligne ; source inexistante ou relation non
    permise : refus qui nomme la ligne."""
    await _declare(space, "acces", REL_TEMPLATE)
    ok = await _import(space, "acces", b"cle;nom;source;cible\nREL-1;Lit;PRC-X;DOC-Y\n")
    assert ok.status_code == 200, ok.text
    relation = await _object(space, "REL-1") or {}
    assert relation["source"] == "PRC-X" and relation["target"] == "DOC-Y"
    missing = await _import(space, "acces", b"cle;nom;source;cible\nREL-2;Lit;PRC-ABSENT;DOC-Y\n")
    assert missing.status_code == 422
    [error] = missing.json()["error_args"]["report"]["error_details"]
    assert (error["where"], error["code"], error["field"]) == (
        "ligne 2",
        "carto_object_invalid",
        "$.source",
    )
    assert error["message"] == (
        "La source PRC-ABSENT n'existe ni dans l'espace ni dans la liste envoyée."
    )
    await space.client.delete(_url(space, "acces", "/fichier"))
    await _declare(space, "composition", REL_TEMPLATE)
    refused = await _import(space, "composition", b"cle;nom;source;cible\nREL-3;C;PRC-X;DOC-Y\n")
    assert refused.status_code == 422
    [error] = refused.json()["error_args"]["report"]["error_details"]
    assert (error["where"], error["code"]) == ("ligne 2", "relation_not_allowed")


async def test_pas_de_remplacement_et_type_absent(space: ApiSpace) -> None:
    """Aucune suppression par import : le mode « remplacement » est refusé."""
    await _declare(space, "application", APP_TEMPLATE)
    replace = await _import(space, "application", _apps(1), mode="replace")
    assert replace.status_code == 400
    trial = await space.client.post(
        _url(space, "application", "/fichier/essai"),
        json={"template": APP_TEMPLATE, "mode": "replace"},
    )
    assert trial.status_code == 400
    absent = await space.client.get(_url(space, "inconnu"))
    assert absent.status_code == 404 and absent.json()["error"] == "carto_type_not_found"


async def test_sans_le_droit_de_modifier(app: FastAPI, space: ApiSpace) -> None:
    """Critère 7 : la liste se lit, aucun import n'est possible."""
    await _declare(space, "application", APP_TEMPLATE)
    await create_plain_user(app, "lea@example.org", PASSWORD)
    async with app.state.pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
        reader = await conn.fetchval(
            "INSERT INTO profiles (name, capabilities) "
            "VALUES ('lecteur-imports', '{spaces:read,cartographie:read}') RETURNING id"
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", user_id, reader)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as lea:
        await login(lea, "lea@example.org", PASSWORD)
        assert (await lea.get(_url(space, "application"))).status_code == 200
        denied = await lea.post(
            _url(space, "application", "/fichier/import"),
            data={"mode": "merge"},
            files={"file": ("f.csv", _apps(1), "text/csv")},
        )
        assert denied.status_code == 403


async def test_journal(space: ApiSpace) -> None:
    await _declare(space, "application", APP_TEMPLATE)
    await _import(space, "application", _apps(2))
    answer = await space.client.get("/api/audit", params={"type": "carto_import"})
    assert answer.status_code == 200, answer.text
    [event] = answer.json()["events"]
    assert event["object_id"].endswith("/application/fichier")


async def test_colonnes_et_ajout_au_modele(space: ApiSpace) -> None:
    """Ticket 221d832b, critère 2 : la base et les propriétés du type correspondent,
    une colonne sans propriété s'ajoute au type."""
    await _declare(space, "application", APP_TEMPLATE)
    await space.client.put(
        _url(space, "application", "/fichier/exemple"),
        files={"file": ("e.csv", b"cle;Nom;criticite;Equipe\nAPP-1;Un;haute;Ventes\n", "text/csv")},
    )
    preview = await space.client.post(_url(space, "application", "/fichier/apercu"))
    assert preview.status_code == 200, preview.text
    rows = {r["column"]: r["property"] for r in preview.json()["comparison"]}
    assert rows == {"cle": "cle", "Nom": "nom", "criticite": "criticite", "Equipe": None}
    added = await space.client.post(
        _url(space, "application", "/fichier/proprietes"), json={"column": "Equipe"}
    )
    assert added.status_code == 200, added.text
    assert (added.json()["property"], added.json()["type"]) == ("equipe", "string")
    again = await space.client.post(_url(space, "application", "/fichier/apercu"))
    assert {r["column"]: r["property"] for r in again.json()["comparison"]}["Equipe"] == "equipe"
    twice = await space.client.post(
        _url(space, "application", "/fichier/proprietes"), json={"column": "Equipe"}
    )
    assert twice.status_code == 409


async def test_import_xlsx_d_un_type(space: ApiSpace) -> None:
    """Ticket 6f6066e9 : déclarer un import xlsx, choisir la feuille, importer."""
    from tests.test_imports_common import _xlsx

    created = await space.client.post(
        _url(space, "application"), json={"label": "Excel", "slug": "excel", "format": "xlsx"}
    )
    assert created.status_code == 201, created.text
    await space.client.put(
        _url(space, "application", "/excel/template"), json={"template": APP_TEMPLATE}
    )
    raw = _xlsx(
        {
            "Notes": [["rien"]],
            "Apps": [["cle", "nom", "criticite"], ["APP-X1", "X1", "basse"]],
        }
    )
    await space.client.put(
        _url(space, "application", "/excel/exemple"),
        files={"file": ("a.xlsx", raw, "application/octet-stream")},
    )
    preview = (await space.client.post(_url(space, "application", "/excel/apercu"))).json()
    assert preview["sheet_required"] is True and preview["sheets"] == ["Notes", "Apps"]
    chosen = await space.client.put(
        _url(space, "application", "/excel/lecture"), json={"header": True, "sheet": "Apps"}
    )
    assert chosen.status_code == 200, chosen.text
    preview = (await space.client.post(_url(space, "application", "/excel/apercu"))).json()
    assert preview["columns"] == ["cle", "nom", "criticite"] and preview["count"] == 1
    run = await space.client.post(
        _url(space, "application", "/excel/import"), data={"mode": "merge"}
    )
    assert run.status_code == 200, run.text
    assert run.json()["new"] == 1


async def test_resoudre_le_template_d_un_type(space: ApiSpace) -> None:
    """Ticket 0598c41b, critère 2."""
    await _declare(space, "application", APP_TEMPLATE)
    await space.client.put(
        _url(space, "application", "/fichier/exemple"),
        files={"file": ("e.csv", b"cle;Nom\nAPP-1;Un\n", "text/csv")},
    )
    resolved = await space.client.post(_url(space, "application", "/fichier/resoudre"))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["template"] == (
        "{\n"
        '  "cle": $.cle,\n'
        '  "nom": $.Nom,\n'
        '  "documentation": null,\n'
        '  "proprietes": {\n'
        '    "criticite": null\n'
        "  }\n"
        "}\n"
    )


async def test_renommer_une_propriete_suit_imports_et_objets(space: ApiSpace) -> None:
    """Ticket 859579bb : deux propriétés renommées en un enregistrement ; les
    correspondances (par nom et par choix), le template et les objets suivent,
    une colonne ignorée le reste."""
    await _declare(space, "application", APP_TEMPLATE)
    assert (await _import(space, "application", _apps(2))).status_code == 200
    await space.client.put(
        _url(space, "application", "/fichier/exemple"),
        files={
            "file": ("e.csv", b"cle;Nom;criticite;Equipe;Chef\nAPP-1;Un;haute;V;X\n", "text/csv")
        },
    )
    added = await space.client.post(
        _url(space, "application", "/fichier/proprietes"), json={"column": "Equipe"}
    )
    assert added.status_code == 200, added.text
    chosen = await space.client.put(
        _url(space, "application", "/fichier/correspondances"),
        json={"mapping": {"criticite": "", "Chef": "criticite"}},
    )
    assert chosen.status_code == 200, chosen.text
    url = f"{space.base}/cartographie/types/application"
    schema = (await space.client.get(url)).json()["document"]
    props = schema["properties"]
    schema["properties"] = {"niveau": props["criticite"], "groupe": props["equipe"]}
    saved = await space.client.put(url, json={"schema": schema})
    assert saved.status_code == 200, saved.text
    preview = (await space.client.post(_url(space, "application", "/fichier/apercu"))).json()
    rows = {r["column"]: r["property"] for r in preview["comparison"]}
    assert rows == {
        "cle": "cle",
        "Nom": "nom",
        "criticite": None,
        "Equipe": "groupe",
        "Chef": "niveau",
    }
    detail = (await space.client.get(_url(space, "application", "/fichier"))).json()
    assert '"niveau": $.criticite' in detail["template"]
    assert '"criticite":' not in detail["template"]
    app_01 = await _object(space, "APP-01") or {}
    assert app_01["properties"] == {"niveau": "haute"}


def test_reconnaitre_un_renommage() -> None:
    """Ticket 859579bb : même position et même définition ; ni une suppression
    suivie d'un ajout ailleurs, ni un changement de définition."""
    from ossie_studio.cartographie.model import CartoType, property_renames

    def t(props: dict[str, Any]) -> CartoType:
        return CartoType(slug="a", title="A", archimate="ApplicationComponent", properties=props)

    s, n = {"type": "string"}, {"type": "number"}
    before = t({"a": s, "b": n, "c": s})
    assert property_renames(before, t({"a": s, "bb": n, "c": s})) == {"b": "bb"}
    assert property_renames(before, t({"x": s, "b": n, "y": s})) == {"a": "x", "c": "y"}
    described = {"type": "number", "description": ""}  # ajouté par l'éditeur visuel
    assert property_renames(before, t({"a": s, "bb": described, "c": s})) == {"b": "bb"}
    assert property_renames(before, t({"a": s, "bb": s, "c": s})) == {}  # définition changée
    assert property_renames(before, t({"a": s, "c": s, "d": n})) == {}  # retrait + ajout ailleurs
    assert property_renames(before, t({"a": s, "b": n})) == {}  # retrait seul


async def test_nettoyer_retire_le_fichier_d_exemple(space: ApiSpace) -> None:
    """Ticket 34fa9248 : « Nettoyer » vide le fichier d'exemple ; le template reste."""
    await _declare(space, "application", APP_TEMPLATE)
    await space.client.put(
        _url(space, "application", "/fichier/exemple"),
        files={"file": ("e.csv", b"cle;Nom\nAPP-1;Un\n", "text/csv")},
    )
    cleared = await space.client.delete(_url(space, "application", "/fichier/exemple"))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["example_size"] == 0
    assert cleared.json()["template"] == APP_TEMPLATE
    preview = (await space.client.post(_url(space, "application", "/fichier/apercu"))).json()
    assert preview["rows"] == [] and preview["count"] == 0
    missing = await space.client.delete(_url(space, "application", "/inconnu/exemple"))
    assert missing.status_code == 404


def _carto_root(tmp_path: Path) -> Path:
    """Les types de TYPES et les trois objets du banc, en fichiers."""
    for slug, (title, arch, extra) in TYPES.items():
        file = tmp_path / f"cartographie/types/{slug}.schema.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"title: {title}\nx-archimate: {arch}\ntype: object\n{extra}")
    for key, ctype in (("APP-OLD", "application"), ("PRC-X", "processus"), ("DOC-Y", "document")):
        file = tmp_path / f"cartographie/objets/{ctype}/{key.lower()}.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"cle: {key}\ntype: {ctype}\nnom: {key}\n")
    return tmp_path


def test_ecriture_d_un_import_sans_relire_par_objet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket eed2dd0d : l'écriture lit la cartographie une fois, quel que soit le
    nombre d'objets (elle la relisait trois fois par objet : 44 s pour 204
    applications), et écrit les mêmes fichiers que l'écriture objet par objet."""
    from ossie_studio.cartographie import space as carto_space
    from ossie_studio.cartographie.import_target import target_of
    from ossie_studio.mcp_surface import kind_stores, kinds

    root = _carto_root(tmp_path)
    items = [
        {"cle": f"APP-{i:02d}", "type": "application", "nom": f"App {i}",
         "proprietes": {"criticite": "haute"}}
        for i in range(1, 31)
    ]  # fmt: skip
    items.append({"cle": "APP-OLD", "type": "application", "nom": "Renommée"})
    reads = {"n": 0}
    real = carto_space.read_cartographie

    def counted(*args: Any, **kwargs: Any) -> Any:
        reads["n"] += 1
        return real(*args, **kwargs)

    target = target_of(real(root), "application")
    for module in (carto_space, kind_stores, kinds):
        monkeypatch.setattr(module, "read_cartographie", counted)
    target.write(root, items)
    assert reads["n"] <= 1
    assert (root / "cartographie/objets/application/app-07.yaml").read_text() == (
        "cle: APP-07\ntype: application\nnom: App 7\nproprietes:\n  criticite: haute\n"
    )
    assert "nom: Renommée" in (root / "cartographie/objets/application/app-old.yaml").read_text()


def test_ecriture_d_un_import_tout_ou_rien(tmp_path: Path) -> None:
    """Un objet refusé à l'écriture (validé contre l'état du disque) : rien n'est
    écrit, et le refus nomme l'objet."""
    from ossie_studio.cartographie.errors import CartographieError
    from ossie_studio.cartographie.import_target import target_of
    from ossie_studio.cartographie.space import read_cartographie

    root = _carto_root(tmp_path)
    target = target_of(read_cartographie(root), "acces")
    items = [
        {"cle": "REL-1", "type": "acces", "nom": "Lit", "source": "PRC-X", "cible": "DOC-Y"},
        {"cle": "REL-2", "type": "acces", "nom": "Lit", "source": "PRC-ABSENT", "cible": "DOC-Y"},
    ]
    with pytest.raises(CartographieError) as refused:
        target.write(root, items)
    assert [p.code for p in refused.value.problems] == ["relation_endpoint_unknown"]
    assert not (root / "cartographie/objets/acces").exists()
