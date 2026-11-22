"""Règles de tags : format dans le dépôt, correspondance, transmission par
héritage, API (création automatique des tags manquants) et explorateur raw."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.governance.rules import GovernanceRule
from ossie_studio.governance.tag_rules import (
    TagRule,
    parse_rule,
    path_matches,
    read_tag_rules,
    short_name,
)
from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.explorer import SourceEntries, clear_caches, collect_source_entries
from ossie_studio.sources.explorer_tags import apply_tag_rules
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest, _tech


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


def _schema(title: str, *, bases: list[dict] | None = None, **extra: object) -> str:
    data: dict[str, object] = {
        "$id": f"{title.lower()}.json",
        "title": title,
        "type": "object",
        "x-kind": "class",
        "x-language": "csharp",
        "properties": {"Id": {"type": "string"}},
    }
    if bases:
        data["allOf"] = bases
    data.update(extra)
    return dump_schema(data)


def _files() -> dict[str, str]:
    """Deux sources : `app` (C# et un fichier Java) et `lib` (une base commune)."""
    raw = "sources/app/raw"
    lib = "sources/lib/raw"
    return {
        "sources/app/manifest.yaml": _manifest("app"),
        f"{raw}/orderscontroller.json": _schema(
            "OrdersController",
            bases=[{"x-unresolved-type": "Microsoft.AspNetCore.Mvc.ControllerBase"}],
        ),
        f"{raw}/admincontroller.json": _schema(
            "AdminController", bases=[{"$ref": "orderscontroller.json"}]
        ),
        f"{raw}/superadmincontroller.json": _schema(
            "SuperAdminController", bases=[{"$ref": "admincontroller.json"}]
        ),
        f"{raw}/healthcontroller.json": _schema(
            "HealthController", **{"x-annotations": [{"nom": "ApiController", "arguments": []}]}
        ),
        f"{raw}/order.json": _schema(
            "Order", properties={"Id": {"type": "string"}, "Email": {"type": "string"}}
        ),
        f"{raw}/orderdto.json": _schema("OrderDto"),
        f"{raw}/customer.json": _schema("Customer", bases=[{"$ref": "baseentity.json"}]),
        f"{raw}/javacontroller.json": _schema(
            "JavaController", bases=[{"x-unresolved-type": "ControllerBase"}]
        ),
        f"{raw}/cyclea.json": _schema("CycleA", bases=[{"$ref": "cycleb.json"}]),
        f"{raw}/cycleb.json": _schema("CycleB", bases=[{"$ref": "cyclea.json"}]),
        f"{raw}/manifest-csharp.yaml": _tech(
            {
                "src/Api/OrdersController.cs": (["orderscontroller.json"], "csharp"),
                "src/Api/AdminController.cs": (["admincontroller.json"], "csharp"),
                "src/Api/SuperAdminController.cs": (["superadmincontroller.json"], "csharp"),
                "src/Api/HealthController.cs": (["healthcontroller.json"], "csharp"),
                "src/Domain/Order.cs": (["order.json"], "csharp"),
                "src/Dto/OrderDto.cs": (["orderdto.json"], "csharp"),
                "src/Domain/Customer.cs": (["customer.json"], "csharp"),
                "src/Cycle.cs": (["cyclea.json", "cycleb.json"], "csharp"),
                "java/JavaController.java": (["javacontroller.json"], "java"),
            }
        ),
        "sources/lib/manifest.yaml": _manifest("lib"),
        f"{lib}/baseentity.json": _schema("BaseEntity"),
        f"{lib}/manifest-csharp.yaml": _tech(
            {"Common/BaseEntity.cs": (["baseentity.json"], "csharp")}
        ),
    }


def _sources(tmp_path: Path) -> list[SourceEntries]:
    for rel, text in _files().items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [collect_source_entries(tmp_path, slug) for slug in ("app", "lib")]


def _rule(rule_id: str = "r", **data: object) -> TagRule:
    rule, errors = parse_rule(rule_id, {"files": ["*.cs"], "tags": ["service"], **data})
    assert rule is not None, errors
    return rule


def _tags(sources: list[SourceEntries]) -> dict[str, dict[str, tuple[str, str, str]]]:
    """Titre → tag → (condition, valeur, titre de la classe d'origine si hérité)."""
    return {
        o.title: {t.tag: (t.reason, t.value, t.via_title) for t in o.tags}
        for s in sources
        for o in s.objects
        if o.tags
    }


CONTROLLERS = {"inherits": ["ControllerBase"]}


def test_herite_de_controllerbase_et_transmis_aux_derivees(tmp_path: Path) -> None:
    got = _tags(apply_tag_rules(_sources(tmp_path), [_rule(when=CONTROLLERS, inherit_tags=True)]))
    assert got["OrdersController"] == {
        "service": ("inherits", "Microsoft.AspNetCore.Mvc.ControllerBase", "")
    }
    assert got["AdminController"] == {"service": ("", "", "OrdersController")}
    # La provenance retenue est la classe la plus proche.
    assert got["SuperAdminController"] == {"service": ("", "", "AdminController")}
    assert "Order" not in got
    # Une règle sur `*.cs` ne regarde pas un objet issu d'un fichier `.java`.
    assert "JavaController" not in got


def test_sans_transmission_seule_la_base_directe_compte(tmp_path: Path) -> None:
    got = _tags(apply_tag_rules(_sources(tmp_path), [_rule(when=CONTROLLERS)]))
    assert set(got) == {"OrdersController"}


def test_attribut_nom_champ_et_chemin(tmp_path: Path) -> None:
    rules = [
        _rule("a", when={"annotation": ["ApiController"]}),
        _rule("b", when={"name": ["*Dto"]}, tags=["technique/dto"]),
        _rule("c", when={"field": ["Email", "Phone*"]}, tags=["donnees/personnelles"]),
        _rule("d", when={"path": ["src/Domain/*"]}, tags=["technique/domaine"]),
    ]
    got = _tags(apply_tag_rules(_sources(tmp_path), rules))
    assert got["HealthController"] == {"service": ("annotation", "ApiController", "")}
    assert got["OrderDto"] == {"technique/dto": ("name", "OrderDto", "")}
    assert got["Order"] == {
        "donnees/personnelles": ("field", "Email", ""),
        "technique/domaine": ("path", "src/Domain/Order.cs", ""),
    }
    assert got["Customer"] == {"technique/domaine": ("path", "src/Domain/Customer.cs", "")}


def test_match_all_exige_toutes_les_conditions(tmp_path: Path) -> None:
    rule = _rule(match="all", when={"name": ["*Controller"], "annotation": ["ApiController"]})
    assert set(_tags(apply_tag_rules(_sources(tmp_path), [rule]))) == {"HealthController"}
    rule = _rule(when={"name": ["*Controller"], "annotation": ["ApiController"]})
    assert "OrdersController" in _tags(apply_tag_rules(_sources(tmp_path), [rule]))


def test_regle_desactivee_ne_pose_rien(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert _tags(apply_tag_rules(sources, [_rule(when=CONTROLLERS, enabled=False)])) == {}
    assert _tags(apply_tag_rules(sources, [])) == {}


def test_chaine_qui_traverse_deux_sources(tmp_path: Path) -> None:
    rule = _rule(when={"name": ["BaseEntity"]}, tags=["technique/entite"], inherit_tags=True)
    got = _tags(apply_tag_rules(_sources(tmp_path), [rule]))
    assert got["BaseEntity"] == {"technique/entite": ("name", "BaseEntity", "")}
    assert got["Customer"] == {"technique/entite": ("", "", "BaseEntity")}


def test_correspondance_directe_prime_sur_l_heritage(tmp_path: Path) -> None:
    rules = [
        _rule("a", when=CONTROLLERS, inherit_tags=True),
        _rule("b", when={"name": ["AdminController"]}),
    ]
    got = _tags(apply_tag_rules(_sources(tmp_path), rules))
    assert got["AdminController"] == {"service": ("name", "AdminController", "")}


def test_cycle_d_heritage_ne_boucle_pas(tmp_path: Path) -> None:
    rule = _rule(when={"name": ["CycleA"]}, inherit_tags=True)
    got = _tags(apply_tag_rules(_sources(tmp_path), [rule]))
    assert got["CycleA"] == {"service": ("name", "CycleA", "")}
    assert got["CycleB"] == {"service": ("", "", "CycleA")}


def test_noms_courts_et_motifs_de_chemin() -> None:
    assert short_name("Microsoft.AspNetCore.Mvc.ControllerBase") == "ControllerBase"
    assert short_name("Repository<Order>") == "Repository"
    assert path_matches("src/Api/X.cs", ("*.cs",)) == "*.cs"
    assert path_matches("src/Api/X.cs", ("src/Api/*",)) == "src/Api/*"
    assert path_matches("src/Api/X.cs", ("*.java",)) is None
    assert path_matches("", ("*",)) is None


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ([], "tag_rule_not_mapping"),
        ({"tags": ["s"], "when": {"name": ["X"]}}, "tag_rule_files_invalid"),
        ({"files": ["*.cs"], "tags": ["s"]}, "tag_rule_conditions_missing"),
        (
            {"files": ["*.cs"], "tags": ["s"], "when": {"color": ["X"]}},
            "tag_rule_condition_unknown",
        ),
        ({"files": ["*.cs"], "tags": ["s"], "when": {"name": []}}, "tag_rule_condition_invalid"),
        ({"files": ["*.cs"], "tags": ["../x"], "when": {"name": ["X"]}}, "tag_rule_tags_invalid"),
        (
            {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}, "match": "x"},
            "tag_rule_match_invalid",
        ),
        (
            {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}, "enabled": "oui"},
            "tag_rule_flag_invalid",
        ),
        (
            {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}, "colour": 1},
            "tag_rule_unknown_key",
        ),
    ],
)
def test_regle_invalide(data: object, code: str) -> None:
    rule, errors = parse_rule("r", data)
    assert rule is None and code in errors


def test_regle_invalide_signalee_par_la_validation_sans_casser_l_index(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    rules_dir = tmp_path / "governance/tag-rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "ok.yaml").write_text(
        "files: ['*.cs']\nwhen: {inherits: [ControllerBase]}\ntags: [service]\n", encoding="utf-8"
    )
    (rules_dir / "bad.yaml").write_text("files: []\ntags: [service]\n", encoding="utf-8")
    (rules_dir / "broken.yaml").write_text("files: [\n", encoding="utf-8")
    files = {f.rel: f for f in read_tag_rules(tmp_path)}
    assert files["governance/tag-rules/ok.yaml"].rule is not None
    assert files["governance/tag-rules/broken.yaml"].errors == ("tag_rule_unreadable",)
    findings = {(f.path, f.message_code) for f in GovernanceRule().check(tmp_path)}
    assert ("governance/tag-rules/bad.yaml", "tag_rule_files_invalid") in findings
    assert ("governance/tag-rules/broken.yaml", "tag_rule_unreadable") in findings
    active = [f.rule for f in files.values() if f.rule]
    assert set(_tags(apply_tag_rules(sources, active))) == {"OrdersController"}


# --- API ---------------------------------------------------------------------------


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_regle_cree_les_tags_et_l_explorateur_les_montre(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    explorer = f"{sp.base}/raw-explorer/app/folder"
    r = await sp.client.get(explorer, params={"path": "src/Api"})
    assert r.status_code == 200, r.text
    assert all(o["tags"] == [] for o in r.json()["objects"])

    body = {
        "name": "Contrôleurs",
        "files": ["*.cs"],
        "when": {"inherits": ["ControllerBase"]},
        "tags": ["Technique/Service"],
        "inherit_tags": True,
    }
    r = await sp.client.put(f"{sp.base}/governance/tag-rules/controllers", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["created_tags"] == ["technique/service"]
    tags = await sp.client.get(f"{sp.base}/governance/tags")
    assert "technique/service" in {t["id"] for t in tags.json()["tags"]}

    # Sans recollecter : la règle est dans le brouillon, l'index se reconstruit.
    r = await sp.client.get(explorer, params={"path": "src/Api"})
    by_title = {o["title"]: o["tags"] for o in r.json()["objects"]}
    assert [t["tag"] for t in by_title["OrdersController"]] == ["technique/service"]
    assert by_title["AdminController"][0]["via_title"] == "OrdersController"
    assert by_title["AdminController"][0]["rule_name"] == "Contrôleurs"
    assert by_title["HealthController"] == []

    detail = await sp.client.get(
        f"{sp.base}/raw-explorer/app/object", params={"file": "orderscontroller.json"}
    )
    assert detail.json()["tags"][0]["value"] == "Microsoft.AspNetCore.Mvc.ControllerBase"

    # Réenregistrer ne recrée pas le tag ; désactiver retire les tags affichés.
    r = await sp.client.put(
        f"{sp.base}/governance/tag-rules/controllers", json={**body, "enabled": False}
    )
    assert r.json()["created_tags"] == []
    r = await sp.client.get(explorer, params={"path": "src/Api"})
    assert all(o["tags"] == [] for o in r.json()["objects"])

    listing = await sp.client.get(f"{sp.base}/governance/tag-rules")
    rule = listing.json()["rules"][0]
    assert rule["id"] == "controllers" and rule["rule"]["enabled"] is False
    assert yaml.safe_dump(rule["rule"])  # sérialisable tel quel


async def test_api_regle_invalide_et_suppression(sp: ApiSpace) -> None:
    url = f"{sp.base}/governance/tag-rules"
    r = await sp.client.put(f"{url}/x", json={"files": [], "tags": ["s"], "when": {"name": ["X"]}})
    assert r.status_code == 400
    assert r.json()["error"] == "tag_rule_invalid"
    assert "tag_rule_files_invalid" in r.json()["error_args"]["fields"]
    r = await sp.client.put(
        f"{url}/Bad..Id", json={"files": ["*"], "tags": ["s"], "when": {"name": ["X"]}}
    )
    assert r.status_code == 400
    assert (await sp.client.delete(f"{url}/absent")).status_code == 404
    ok = {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}}
    assert (await sp.client.put(f"{url}/x", json=ok)).status_code == 200
    assert (await sp.client.delete(f"{url}/x")).status_code == 204
    assert (await sp.client.get(url)).json()["rules"] == []
