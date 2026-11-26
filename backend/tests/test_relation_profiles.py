"""Relations virtuelles des sources SQL par profils de nommage (ticket b5f333b4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.governance.relation_profiles import (
    builtin_profiles,
    parse_profile,
    source_relation_profiles,
)
from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.explorer import SourceEntries, clear_caches, collect_source_entries
from ossie_studio.sources.explorer_detail import SpaceObjects
from ossie_studio.sources.explorer_relations import apply_relation_profiles, resolve_source
from ossie_studio.sources.sql_extract import _slug
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest, _tech


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


TABLES = {
    "BV.ACCOUNT_HUB": ["ACCOUNT_HK", "ACCOUNT_BK"],
    "BV.CARRIER_HUB": ["CARRIER_HK"],
    "BV.SAT_ACCOUNT": ["ACCOUNT_HK", "NAME"],
    "BV.LINK_ACCOUNT_CARRIER": ["LINK_HK", "ACCOUNT_HK", "CARRIER_HK"],
    "BV.SAT_FOO": ["FOO_HK"],
    "BV.DECLARED": ["ACCOUNT_HK"],
}


def _files(slug: str = "dwh", profiles: list[str] | None = None) -> dict[str, str]:
    raw = f"sources/{slug}/raw"
    params: dict[str, object] = {"database": "DB_DVT_PRD"}
    if profiles is not None:
        params["relation_profiles"] = profiles
    files = {f"sources/{slug}/manifest.yaml": _manifest(slug, "snowflake", **params)}
    for key, cols in TABLES.items():
        props: dict[str, object] = {c: {"type": "string"} for c in cols}
        if key == "BV.DECLARED":  # clé étrangère déclarée : elle l'emporte
            props["ACCOUNT_HK"] = {
                "type": "string",
                "x-references": {"table": "BV.CARRIER_HUB", "column": "CARRIER_HK"},
            }
        files[f"{raw}/{_slug(key)}.schema.yaml"] = dump_schema(
            {
                "$id": f"{_slug(key)}.schema.yaml",
                "title": key.split(".")[1],
                "type": "object",
                "x-kind": "table",
                "properties": props,
            }
        )
    files[f"{raw}/manifest-snowflake.yaml"] = _tech(
        {key: ([f"{_slug(key)}.schema.yaml"], "snowflake") for key in TABLES}
    )
    return files


def _entries(tmp_path: Path, **kw: object) -> SourceEntries:
    for rel, text in _files(**kw).items():  # type: ignore[arg-type]
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return collect_source_entries(tmp_path, "dwh")


def _virtual(entries: SourceEntries) -> set[tuple[str, str, str]]:
    return {(o.title, r.field, r.target) for o in entries.objects for r in o.refs if r.virtual}


def test_data_vault_satellites_et_liens_vers_leurs_hubs(tmp_path: Path) -> None:
    (out,) = apply_relation_profiles([_entries(tmp_path)], {"dwh": builtin_profiles()})
    assert _virtual(out) == {
        ("SAT_ACCOUNT", "ACCOUNT_HK", "BV.ACCOUNT_HUB"),
        ("LINK_ACCOUNT_CARRIER", "ACCOUNT_HK", "BV.ACCOUNT_HUB"),
        ("LINK_ACCOUNT_CARRIER", "CARRIER_HK", "BV.CARRIER_HUB"),
    }
    ref = next(r for o in out.objects for r in o.refs if r.virtual)
    assert (
        ref.virtual == "Data Vault 2.0" and ref.column == "ACCOUNT_HK" and ref.kind == "foreign_key"
    )
    # Les flèches se résolvent vers la table visée (fichier SQL normalisé).
    space = SpaceObjects([out], "origin")
    target = space.by_table("BV.ACCOUNT_HUB")
    assert target is not None and target.title == "ACCOUNT_HUB"


def test_hub_ne_se_reference_pas_et_cible_absente_signalee(tmp_path: Path) -> None:
    hits, unresolved = resolve_source(_entries(tmp_path), builtin_profiles())
    assert not any(h.title == "ACCOUNT_HUB" for h in hits)
    assert {(u.title, u.field, u.reason) for u in unresolved} >= {
        ("SAT_FOO", "FOO_HK", "not_found")
    }
    # LINK_HK : la table LINK_HUB n'existe pas → signalée, sans relation.
    assert not any(h.field == "LINK_HK" for h in hits)


def test_cle_etrangere_declaree_l_emporte(tmp_path: Path) -> None:
    (out,) = apply_relation_profiles([_entries(tmp_path)], {"dwh": builtin_profiles()})
    declared = next(o for o in out.objects if o.title == "DECLARED")
    assert [(r.target, r.virtual) for r in declared.refs] == [("BV.CARRIER_HUB", "")]


def test_sans_profil_rien_ne_change_et_retrait_efface(tmp_path: Path) -> None:
    entries = _entries(tmp_path)
    (with_profile,) = apply_relation_profiles([entries], {"dwh": builtin_profiles()})
    (without,) = apply_relation_profiles([with_profile], {})
    assert _virtual(without) == set()
    assert [o.refs for o in without.objects] == [o.refs for o in entries.objects]


def test_profil_etoile_et_validation() -> None:
    profile, errors = parse_profile(
        "etoile",
        {
            "name": "Étoile",
            "rules": [{"column": "(?<nom>[A-Z]+)_ID", "table": "DIM_{nom}", "target_column": "ID"}],
        },
    )
    assert profile is not None, errors
    assert profile.rules[0].regex() is not None
    _p, errs = parse_profile(
        "x", {"rules": [{"column": "(a+)+", "table": "T", "target_column": "C"}]}
    )
    assert "relation_rule_pattern_invalid" in errs
    _p, errs = parse_profile(
        "x", {"rules": [{"column": "(?P<n>A)", "table": "{autre}", "target_column": "C"}]}
    )
    assert "relation_rule_template_invalid" in errs
    _p, errs = parse_profile(
        "ossie-x", {"rules": [{"column": "A", "table": "T", "target_column": "C"}]}
    )
    assert "relation_profile_id_reserved" in errs


def test_profils_choisis_dans_le_manifeste(tmp_path: Path) -> None:
    _entries(tmp_path, profiles=["ossie-datavault"])
    assert source_relation_profiles(tmp_path, "dwh") == ["ossie-datavault"]
    assert source_relation_profiles(tmp_path, "absente") == []


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_profil_apercu_et_index(sp: ApiSpace) -> None:
    forge_edit(sp, _files(profiles=["ossie-datavault", "etoile"]))
    gov = f"{sp.base}/governance/relation-profiles"
    listing = (await sp.client.get(gov)).json()["profiles"]
    assert [p["id"] for p in listing] == ["ossie-datavault"] and listing[0]["builtin"] is True
    body = {
        "name": "Étoile",
        "rules": [
            {"column": "(?<nom>[A-Z]+)_HK", "table": "{nom}_HUB", "target_column": "{nom}_HK"}
        ],
    }
    r = await sp.client.post(
        f"{sp.base}/raw-explorer/relation-profiles/preview", params={"source": "dwh"}, json=body
    )
    assert r.status_code == 200, r.text
    assert r.json()["relations"] == 3 and r.json()["unresolved"] >= 1
    r = await sp.client.put(f"{gov}/etoile", json=body)
    assert r.status_code == 200, r.text
    bad = await sp.client.put(
        f"{gov}/x",
        json={"name": "X", "rules": [{"column": "(", "table": "T", "target_column": "C"}]},
    )
    assert bad.status_code == 400 and bad.json()["error"] == "relation_profile_invalid"
    assert (await sp.client.delete(f"{gov}/ossie-datavault")).status_code == 404
    # L'index de l'explorateur porte les références virtuelles (détail d'un objet).
    detail = await sp.client.get(
        f"{sp.base}/raw-explorer/dwh/object",
        params={"file": f"{_slug('BV.SAT_ACCOUNT')}.schema.yaml"},
    )
    assert detail.status_code == 200, detail.text
    virtual = [x for x in detail.json()["references"] if x.get("virtual")]
    assert virtual and virtual[0]["label"] == "BV.ACCOUNT_HUB.ACCOUNT_HK"
    assert virtual[0]["target"]["title"] == "ACCOUNT_HUB"
    assert (await sp.client.delete(f"{gov}/etoile")).status_code == 204


async def test_api_profil_fourni_modifie_puis_retabli(sp: ApiSpace) -> None:
    """Ticket 1bce7eb9 : le profil fourni se modifie sous son identifiant, les
    sources qui l'ont coché suivent ; le supprimer rétablit l'original."""
    forge_edit(sp, _files(profiles=["ossie-datavault"]))
    gov = f"{sp.base}/governance/relation-profiles"
    sat = {"file": f"{_slug('BV.SAT_ACCOUNT')}.schema.yaml"}

    async def virtual() -> list[str]:
        r = await sp.client.get(f"{sp.base}/raw-explorer/dwh/object", params=sat)
        return [x["label"] for x in r.json()["references"] if x.get("virtual")]

    assert await virtual() == ["BV.ACCOUNT_HUB.ACCOUNT_HK"]
    # Une règle qui ne trouve plus rien : l'index suit la version de l'espace.
    body = {
        "name": "Data Vault maison",
        "rules": [{"column": "(?<nom>[A-Z]+)_HK", "table": "H_{nom}", "target_column": "{nom}_HK"}],
    }
    r = await sp.client.put(f"{gov}/ossie-datavault", json=body)
    assert r.status_code == 200, r.text
    listing = (await sp.client.get(gov)).json()["profiles"]
    assert [p["id"] for p in listing] == ["ossie-datavault"]
    assert listing[0]["builtin"] and listing[0]["customized"]
    assert listing[0]["profile"]["name"] == "Data Vault maison"
    assert await virtual() == []
    assert (await sp.client.delete(f"{gov}/ossie-datavault")).status_code == 204
    listing = (await sp.client.get(gov)).json()["profiles"]
    assert listing[0]["profile"]["name"] == "Data Vault 2.0" and not listing[0]["customized"]
    assert await virtual() == ["BV.ACCOUNT_HUB.ACCOUNT_HK"]
    # Rien à rétablir : 404 ; préfixe réservé pour un profil qui n'est pas fourni.
    assert (await sp.client.delete(f"{gov}/ossie-datavault")).status_code == 404
    bad = await sp.client.put(f"{gov}/ossie-autre", json=body)
    assert bad.status_code == 400
