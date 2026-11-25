"""Valeurs posées par règle de classification, reportées sur les champs des
assets de la Curation (ticket docflow 568a0547). Gael, 24/11/2026 : « Champs
raw, suivis à la curation » et « La personne gagne » : déclarée à la main >
par règle > rien ; la valeur par règle est calculée, jamais écrite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.governance.classification_resolve import RuleValue, classify
from ossie_studio.governance.repo import Level, Scheme
from ossie_studio.sources.explorer import clear_caches
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_classification_rules import _files, _gdpr

GDPR = Scheme(
    id="gdpr",
    name="GDPR",
    levels=[
        Level(id="np", scheme_id="gdpr", name="NP", rank=1),
        Level(id="dp", scheme_id="gdpr", name="DP", rank=2),
    ],
)
EMAIL_RULE = RuleValue(("DP",), "emails", "Emails")


def test_champ_par_regle_puis_declaration_qui_gagne() -> None:
    asset: dict[str, Any] = {"properties": {"Email": {"type": "string"}, "Id": {}}}
    c = classify(asset, [GDPR], {"Email": {"gdpr": EMAIL_RULE}})
    assert c.fields == {"Email": {"gdpr": ("DP",)}}
    assert c.by_rule == {"Email": {"gdpr": EMAIL_RULE}}
    # L'asset dérive de la valeur par règle.
    assert (c.object[0].values, c.object[0].declared) == (("DP",), False)

    asset["properties"]["Email"]["x-classification"] = {"gdpr": "NP"}
    c = classify(asset, [GDPR], {"Email": {"gdpr": EMAIL_RULE}})
    assert c.fields == {"Email": {"gdpr": ("NP",)}} and c.by_rule == {}
    # Une déclaration invalide écarte aussi la règle (jamais de repli).
    asset["properties"]["Email"]["x-classification"] = {"gdpr": "Inconnu"}
    c = classify(asset, [GDPR], {"Email": {"gdpr": EMAIL_RULE}})
    assert c.fields == {} and c.by_rule == {} and c.invalid


def test_regle_sur_un_champ_non_retenu_ignoree() -> None:
    c = classify({"properties": {"Id": {}}}, [GDPR], {"Email": {"gdpr": EMAIL_RULE}})
    assert c.fields == {} and c.object[0].values == ()


# --- API ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh() -> None:
    clear_caches()


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    forge_edit(space, _files())
    await _gdpr(space)
    rule = {
        "name": "Emails",
        "when": {"field": ["*email*"], "type": ["string"]},
        "scheme": "gdpr",
        "values": ["Donnée personnelle"],
    }
    r = await space.client.put(f"{space.base}/governance/classification-rules/emails", json=rule)
    assert r.status_code == 200, r.text
    r = await space.client.post(
        f"{space.base}/sources/app/curation/retain",
        json={"path": "customer.schema.yaml", "fields": None},
    )
    assert r.status_code == 200, r.text
    return space


async def _asset(sp: ApiSpace) -> dict[str, Any]:
    r = await sp.client.get(
        f"{sp.base}/curation-assets/asset",
        params={"source": "app", "path": "customer.schema.yaml"},
    )
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def _field(data: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(f for f in data["governance"]["fields"] if f["name"] == name)
    return found


async def test_report_sur_l_asset_declaration_et_regle_desactivee(sp: ApiSpace) -> None:
    data = await _asset(sp)
    email = _field(data, "Email")
    assert email["values"] == {"gdpr": ["Donnée personnelle"]}
    assert email["rules"] == {"gdpr": {"rule": "emails", "rule_name": "Emails"}}
    assert _field(data, "EmailSent") == {"name": "EmailSent", "values": {}, "rules": {}}
    gdpr = data["governance"]["object"][0]
    assert (gdpr["values"], gdpr["declared"]) == (["Donnée personnelle"], False)
    # Badges de la liste des assets et des sources.
    listing = (await sp.client.get(f"{sp.base}/curation-assets")).json()["assets"]
    assert listing[0]["classification"][0]["values"] == ["Donnée personnelle"]
    sources = (await sp.client.get(f"{sp.base}/sources")).json()["sources"]
    assert next(s for s in sources if s["slug"] == "app")["classification"] == [
        {"scheme": "gdpr", "values": ["Donnée personnelle"]}
    ]

    # Déclarer à la main : la personne gagne, la règle ne la remplace plus.
    r = await sp.client.put(
        f"{sp.base}/sources/app/curation/classify",
        json={"path": "customer.schema.yaml", "fields": {"Email": {"gdpr": ["Non personnelle"]}}},
    )
    assert r.status_code == 200, r.text
    email = _field(await _asset(sp), "Email")
    assert (email["values"], email["rules"]) == ({"gdpr": ["Non personnelle"]}, {})
    # customer_email reste classé par la règle : l'asset dérive toujours DP.
    gdpr = (await _asset(sp))["governance"]["object"][0]
    assert gdpr["values"] == ["Donnée personnelle"]

    # Désactiver la règle : la valeur par règle disparaît, la déclarée reste.
    rule = {
        "name": "Emails",
        "enabled": False,
        "when": {"field": ["*email*"], "type": ["string"]},
        "scheme": "gdpr",
        "values": ["Donnée personnelle"],
    }
    r = await sp.client.put(f"{sp.base}/governance/classification-rules/emails", json=rule)
    assert r.status_code == 200, r.text
    data = await _asset(sp)
    assert _field(data, "Email")["values"] == {"gdpr": ["Non personnelle"]}
    assert _field(data, "customer_email")["values"] == {}
    assert data["governance"]["object"][0]["values"] == ["Non personnelle"]
