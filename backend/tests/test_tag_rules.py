"""Règles de tags : format dans le dépôt, correspondance, transmission par
héritage, API (création automatique des tags manquants) et explorateur raw."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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
from tests.conftest import create_plain_user, login
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


@pytest.fixture
def no_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise les règles fournies par Ossie : les tests d'API ci-dessous
    vérifient le mécanisme des règles de l'espace, isolément."""
    import ossie_studio.api.tag_rules as api_rules
    import ossie_studio.governance.builtin_tag_rules as builtin

    monkeypatch.setattr(builtin, "builtin_rules", lambda: ())
    monkeypatch.setattr(api_rules, "builtin_rules", lambda: ())


async def test_api_regle_cree_les_tags_et_l_explorateur_les_montre(
    sp: ApiSpace, no_builtin: None
) -> None:
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


async def test_api_regle_invalide_et_suppression(sp: ApiSpace, no_builtin: None) -> None:
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


async def test_api_decompte_et_apercu_sur_l_index(sp: ApiSpace, no_builtin: None) -> None:
    forge_edit(sp, _files())
    explorer = f"{sp.base}/raw-explorer/tag-rules"
    body = {
        "name": "Contrôleurs",
        "files": ["*.cs"],
        "when": {"inherits": ["ControllerBase"]},
        "tags": ["service"],
        "inherit_tags": True,
    }
    # Aperçu avant enregistrement : rien n'est écrit, la règle s'applique seule.
    r = await sp.client.post(f"{explorer}/preview", json=body)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["objects"] == 3 and preview["inherited"] == 2
    first = {e["title"]: e for e in preview["examples"]}["OrdersController"]
    assert first["folder"] == "src/Api" and first["inherited"] is False
    assert (await sp.client.get(f"{explorer}/usage")).json()["rules"] == {}
    # Aucun objet reconnu : zéro, sans erreur.
    r = await sp.client.post(f"{explorer}/preview", json={**body, "when": {"name": ["Nope*"]}})
    assert r.json() == {"objects": 0, "inherited": 0, "examples": []}
    r = await sp.client.post(f"{explorer}/preview", json={**body, "files": []})
    assert r.status_code == 400 and r.json()["error"] == "tag_rule_invalid"

    await sp.client.put(f"{sp.base}/governance/tag-rules/ctrl", json=body)
    usage = (await sp.client.get(f"{explorer}/usage")).json()["rules"]
    assert usage["ctrl"]["objects"] == 3 and usage["ctrl"]["inherited"] == 2
    # Aperçu d'une modification de la règle enregistrée : les anciens tags ne
    # survivent pas (seuls ceux de la règle modifiée comptent).
    r = await sp.client.post(f"{explorer}/preview", json={**body, "inherit_tags": False})
    assert r.json()["objects"] == 1


# --- Retraits ------------------------------------------------------------------------


def _removal(title: str, tag: str = "service") -> tuple[str, str]:
    return (f"app/{title.lower()}.json", tag)


def _removed(sources: list[SourceEntries]) -> dict[str, set[str]]:
    return {
        o.title: {t.tag for t in o.removed_tags}
        for s in sources
        for o in s.objects
        if o.removed_tags
    }


def test_retrait_sur_la_mere_retire_aux_derivees(tmp_path: Path) -> None:
    rule = _rule(when=CONTROLLERS, inherit_tags=True)
    tagged = apply_tag_rules(_sources(tmp_path), [rule], {_removal("OrdersController")})
    assert not {"OrdersController", "AdminController", "SuperAdminController"} & set(_tags(tagged))
    # Seule la classe d'où le tag a été retiré le montre comme « retiré ».
    assert _removed(tagged) == {"OrdersController": {"service"}}


def test_retrait_sur_la_mere_une_derivee_qui_correspond_elle_meme_garde_le_tag(
    tmp_path: Path,
) -> None:
    rules = [
        _rule("a", when=CONTROLLERS, inherit_tags=True),
        _rule("b", when={"name": ["SuperAdminController"]}),
    ]
    got = _tags(apply_tag_rules(_sources(tmp_path), rules, {_removal("OrdersController")}))
    assert got == {"SuperAdminController": {"service": ("name", "SuperAdminController", "")}}


def test_retrait_sur_une_fille_vaut_pour_elle_et_ses_descendantes(tmp_path: Path) -> None:
    rule = _rule(when=CONTROLLERS, inherit_tags=True)
    tagged = apply_tag_rules(_sources(tmp_path), [rule], {_removal("AdminController")})
    assert set(_tags(tagged)) == {"OrdersController"}
    assert _removed(tagged) == {"AdminController": {"service"}}


def test_decompte_des_derivees_qui_perdront_le_tag(tmp_path: Path) -> None:
    from ossie_studio.sources.explorer_tags import dependents_losing, index_removals

    tagged = apply_tag_rules(_sources(tmp_path), [_rule(when=CONTROLLERS, inherit_tags=True)])
    assert dependents_losing(tagged, "app/orderscontroller.json", "service") == 2
    assert dependents_losing(tagged, "app/admincontroller.json", "service") == 1
    assert dependents_losing(tagged, "app/superadmincontroller.json", "service") == 0
    removed = apply_tag_rules(tagged, [_rule(when=CONTROLLERS)], {_removal("OrdersController")})
    assert index_removals(removed) == {_removal("OrdersController")}


def test_fichier_des_retraits(tmp_path: Path) -> None:
    from ossie_studio.governance.tag_rules import (
        TagRemoval,
        read_tag_removals,
        removal_errors,
        write_tag_removals,
    )

    write_tag_removals(tmp_path, [TagRemoval("app/a.json", "service", "A")] * 2)
    assert read_tag_removals(tmp_path) == [TagRemoval("app/a.json", "service", "A")]
    assert removal_errors(tmp_path) == []
    write_tag_removals(tmp_path, [])
    assert not (tmp_path / "governance/tag-removals.yaml").exists()
    (tmp_path / "governance").mkdir(exist_ok=True)
    (tmp_path / "governance/tag-removals.yaml").write_text("removals: [{tag: x}]\n")
    assert read_tag_removals(tmp_path) == []
    assert removal_errors(tmp_path) == ["tag_removals_invalid"]


async def test_api_retrait_memorise_transmis_et_annulable(sp: ApiSpace, no_builtin: None) -> None:
    forge_edit(sp, _files())
    rule = {
        "name": "Contrôleurs",
        "files": ["*.cs"],
        "when": {"inherits": ["ControllerBase"]},
        "tags": ["service"],
        "inherit_tags": True,
    }
    await sp.client.put(f"{sp.base}/governance/tag-rules/ctrl", json=rule)
    detail_url = f"{sp.base}/raw-explorer/app/object"
    detail = (await sp.client.get(detail_url, params={"file": "orderscontroller.json"})).json()
    assert detail["tag_dependents"] == {"service": 2}

    removal = {"object": "app/orderscontroller.json", "tag": "service", "title": "OrdersController"}
    url = f"{sp.base}/governance/tag-removals"
    assert (await sp.client.post(url, json=removal)).status_code == 200
    assert (await sp.client.post(url, json=removal)).status_code == 200  # idempotent
    assert len((await sp.client.get(url)).json()["removals"]) == 1

    folder = await sp.client.get(f"{sp.base}/raw-explorer/app/folder", params={"path": "src/Api"})
    assert all(o["tags"] == [] for o in folder.json()["objects"])
    detail = (await sp.client.get(detail_url, params={"file": "orderscontroller.json"})).json()
    assert [t["tag"] for t in detail["removed_tags"]] == ["service"]
    # Une nouvelle collecte (nouveau commit de la source) ne remet pas le tag.
    files = _files()
    files["sources/app/raw/order.json"] = _schema("Order", description="modifié")
    forge_edit(sp, files)
    folder = await sp.client.get(f"{sp.base}/raw-explorer/app/folder", params={"path": "src/Api"})
    assert all(o["tags"] == [] for o in folder.json()["objects"])
    usage = (await sp.client.get(f"{sp.base}/raw-explorer/tag-rules/usage")).json()["rules"]
    assert "ctrl" not in usage

    assert (await sp.client.post(f"{url}/restore", json=removal)).status_code == 200
    assert (await sp.client.post(f"{url}/restore", json=removal)).status_code == 200
    folder = await sp.client.get(f"{sp.base}/raw-explorer/app/folder", params={"path": "src/Api"})
    tagged = {o["title"] for o in folder.json()["objects"] if o["tags"]}
    assert tagged == {"OrdersController", "AdminController", "SuperAdminController"}
    bad = {"object": "pas-de-source", "tag": "service"}
    assert (await sp.client.post(url, json=bad)).status_code == 400


async def test_sans_droit_de_gouvernance_refus_explicite(sp: ApiSpace, app: FastAPI) -> None:
    await create_plain_user(app, "simple@example.org", "mot-de-passe-simple")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        await login(other, "simple@example.org", "mot-de-passe-simple")
        removal = {"object": "app/a.json", "tag": "service"}
        r = await other.post(f"{sp.base}/governance/tag-removals", json=removal)
        assert r.status_code == 403
        rule = {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}}
        r = await other.put(f"{sp.base}/governance/tag-rules/x", json=rule)
        assert r.status_code == 403


# --- Règles fournies par Ossie --------------------------------------------------------


def test_regles_fournies_toutes_valides_et_prefixees() -> None:
    from ossie_studio.governance.builtin_tag_rules import builtin_rules

    rules = builtin_rules()
    assert len(rules) > 20
    assert all(r.id.startswith("ossie-") and r.enabled for r in rules)
    assert len({r.id for r in rules}) == len(rules)


def test_regles_fournies_sur_le_jeu_de_test(tmp_path: Path) -> None:
    from ossie_studio.governance.builtin_tag_rules import builtin_rules

    got = _tags(apply_tag_rules(_sources(tmp_path), builtin_rules()))
    assert "technique/service" in got["OrdersController"]
    assert got["AdminController"]["technique/service"][2] == "OrdersController"
    assert "technique/service" in got["HealthController"]  # [ApiController]
    assert "technique/dto" in got["OrderDto"]
    assert got["Order"]["donnees/personnelles/contact"] == ("field", "Email", "")


def test_champs_sans_casse_et_condition_de_nature(tmp_path: Path) -> None:
    rules = [
        _rule("a", when={"field": ["EMAIL"]}, tags=["contact"]),
        _rule("b", files=["*"], when={"kind": ["class"]}, tags=["classe"]),
    ]
    got = _tags(apply_tag_rules(_sources(tmp_path), rules))
    assert got["Order"]["contact"] == ("field", "Email", "")
    assert got["BaseEntity"]["classe"] == ("kind", "class", "")


def test_id_reserve_aux_regles_fournies() -> None:
    rule, errors = parse_rule(
        "ossie-maison", {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}}
    )
    assert rule is None and "tag_rule_id_reserved" in errors


async def test_api_regles_fournies_listees_et_desactivables(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    rules = (await sp.client.get(f"{sp.base}/governance/tag-rules")).json()["rules"]
    provided = {r["id"]: r for r in rules if r["builtin"]}
    ctrl = provided["ossie-csharp-aspnet-controllers"]
    assert ctrl["rule"]["enabled"] is True and ctrl["path"] == ""

    folder_url = f"{sp.base}/raw-explorer/app/folder"
    folder = await sp.client.get(folder_url, params={"path": "src/Api"})
    tags = {o["title"]: [t["rule"] for t in o["tags"]] for o in folder.json()["objects"]}
    assert "ossie-csharp-aspnet-controllers" in tags["OrdersController"]

    state = f"{sp.base}/governance/tag-rules/ossie-csharp-aspnet-controllers/state"
    r = await sp.client.put(state, json={"enabled": False})
    assert r.status_code == 200, r.text
    assert "technique/service" in r.json()["created_tags"]  # tags des règles actives
    folder = await sp.client.get(folder_url, params={"path": "src/Api"})
    tags = {o["title"]: [t["rule"] for t in o["tags"]] for o in folder.json()["objects"]}
    assert "ossie-csharp-aspnet-controllers" not in tags["OrdersController"]
    rules = (await sp.client.get(f"{sp.base}/governance/tag-rules")).json()["rules"]
    ctrl = next(r for r in rules if r["id"] == "ossie-csharp-aspnet-controllers")
    assert ctrl["rule"]["enabled"] is False

    assert (await sp.client.put(state, json={"enabled": True})).status_code == 200
    absent = f"{sp.base}/governance/tag-rules/ossie-inconnue/state"
    assert (await sp.client.put(absent, json={"enabled": False})).status_code == 404
    rule = {"files": ["*.cs"], "tags": ["s"], "when": {"name": ["X"]}}
    r = await sp.client.put(f"{sp.base}/governance/tag-rules/ossie-perso", json=rule)
    assert r.status_code == 400
