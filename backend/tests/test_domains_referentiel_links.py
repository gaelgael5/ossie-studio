"""Lien de référentiel sur les champs d'un dataset de domaine (ticket docflow
65235f64) : un dataset dérivé hérite du lien de son asset, en lecture seule, le
suit quand on lie ou délie dans la Curation, et le signale partiel quand il ne
reprend qu'une partie d'une clé composée. Rien dans les documents Ossie."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ossie_studio.domains.documents import AssetRef, Dataset, DomainField
from ossie_studio.domains.links import field_link_views
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.sources.referentiel_links import LinkTarget
from tests.space_fixture import ApiSpace, admin_workdir
from tests.test_curation_referentiel_links import ASSET, _fresh_caches, sp  # noqa: F401

DELAIS = LinkTarget(
    name="delais-pays-canal",
    title="Délais",
    schema="referentiels/delais-pays-canal/modele.schema.yaml",
    key=["pays", "canal"],
    properties={"pays": "string", "canal": "string", "delai": "integer"},
)


def test_cle_composee_reprise_en_partie_signalee_partielle() -> None:
    link = {"schema": DELAIS.schema, "property": "pays", "group": "g1"}
    ds = Dataset(
        "Order", "raw:app/order.schema.yaml", False, True, asset=AssetRef("app", ASSET),
        fields=[DomainField("pays", "pays", property="pays", referentiel=link)],
    )  # fmt: skip
    view = field_link_views(ds, {DELAIS.name: DELAIS})["pays"]
    assert (view.status, view.key_index, view.key_size, view.linked_keys) == ("partial", 1, 2, 1)
    assert view.missing == ["canal"]


async def _read(app: FastAPI, sp: ApiSpace, rel: str) -> str | None:  # noqa: F811
    workdir = await admin_workdir(app, sp)

    def read(root: Path) -> str | None:
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    result: str | None = await workdir.read_branch(
        uuid.UUID(sp.space_id), "work/principale", sp.url, "x", None, read
    )
    return result


def _field(detail: dict[str, Any], name: str) -> dict[str, Any]:
    ds = detail["datasets"][0]
    return next(f for f in ds["fields"] if f["name"] == name)


async def test_dataset_derive_herite_du_lien_et_le_suit(sp: ApiSpace, app: FastAPI) -> None:  # noqa: F811
    links = f"{sp.base}/sources/app/curation/referentiel-link"
    r = await sp.client.put(
        links, json={"path": ASSET, "referentiel": "statuts-commande", "links": {"statut": "code"}}
    )
    assert r.status_code == 200, r.text
    r = await sp.client.put(
        links,
        json={
            "path": ASSET,
            "referentiel": "delais-pays-canal",
            "links": {"pays": "pays", "canal": "canal"},
        },
    )
    assert r.status_code == 200, r.text
    api = f"{sp.base}/domains"
    assert (await sp.client.post(api, json={"name": "ventes"})).status_code == 201
    body = {"assets": [{"source": "app", "path": ASSET}]}
    assert (await sp.client.post(f"{api}/ventes/assets", json=body)).status_code == 200

    detail = (await sp.client.get(f"{api}/ventes")).json()
    statut = _field(detail, "statut")["referentiel"]
    assert (statut["referentiel"], statut["property"], statut["status"], statut["inherited"]) == (
        "statuts-commande",
        "code",
        "ok",
        True,
    )
    pays = _field(detail, "pays")["referentiel"]
    assert (pays["status"], pays["key_index"], pays["key_size"]) == ("ok", 1, 2)
    assert _field(detail, "montant")["referentiel"] is None

    # Le lien est dans le `.extend.yaml` du domaine, jamais dans un document Ossie.
    extend = load_schema(await _read(app, sp, "domaines/ventes.extend.yaml") or "")
    assert extend["datasets"]["Order"]["fields"]["statut"]["referentiel"] == {
        "schema": "referentiels/statuts-commande/modele.schema.yaml",
        "property": "code",
    }
    virtual = await _read(app, sp, "domaines/ventes.virtuel.ossie.yaml") or ""
    assert "referentiel" not in virtual and "statuts-commande" not in virtual

    # Délier sur l'asset : le lien disparaît du dataset dérivé dans le même geste.
    r = await sp.client.delete(links, params={"path": ASSET, "field": "statut"})
    assert r.status_code == 200, r.text
    detail = (await sp.client.get(f"{api}/ventes")).json()
    assert _field(detail, "statut")["referentiel"] is None
    extend = load_schema(await _read(app, sp, "domaines/ventes.extend.yaml") or "")
    assert "referentiel" not in (extend["datasets"]["Order"]["fields"].get("statut") or {})
    # Le dataset dérivé reste en lecture seule : pas de modification manuelle.
    refused = await sp.client.put(
        f"{api}/ventes/datasets/Order", json={"name": "Order", "consultable": False}
    )
    assert (refused.status_code, refused.json()["error"]) == (409, "dataset_derived_readonly")


# --- Ticket 80bb56af : dataset manuel ------------------------------------------


async def test_dataset_manuel_lier_delier_renommer(sp: ApiSpace, app: FastAPI) -> None:  # noqa: F811
    api = f"{sp.base}/domains"
    assert (await sp.client.post(api, json={"name": "ventes"})).status_code == 201
    manual = {
        "name": "Tarif",
        "consultable": False,
        "fields": [{"name": n} for n in ("statut", "pays", "canal", "prix")],
    }
    assert (await sp.client.post(f"{api}/ventes/datasets", json=manual)).status_code == 201
    link = f"{api}/ventes/datasets/Tarif/referentiel-link"

    r = await sp.client.put(
        link, json={"referentiel": "statuts-commande", "links": {"statut": "code"}}
    )
    assert r.status_code == 200, r.text
    # Clé composée : un seul champ associé est refusé, la propriété manquante nommée.
    r = await sp.client.put(
        link, json={"referentiel": "delais-pays-canal", "links": {"pays": "pays"}}
    )
    assert (r.status_code, r.json()["error"]) == (400, "referentiel_link_incomplete")
    assert r.json()["error_args"]["missing"] == ["canal"]
    r = await sp.client.put(
        link, json={"referentiel": "delais-pays-canal", "links": {"pays": "pays", "canal": "canal"}}
    )
    assert r.status_code == 200, r.text
    # Un seul référentiel par champ.
    r = await sp.client.put(
        link, json={"referentiel": "statuts-commande", "links": {"pays": "code"}}
    )
    assert (r.status_code, r.json()["error"]) == (409, "referentiel_link_taken")

    detail = (await sp.client.get(f"{api}/ventes")).json()
    statut = _field(detail, "statut")["referentiel"]
    assert (statut["referentiel"], statut["status"], statut["inherited"]) == (
        "statuts-commande",
        "ok",
        False,
    )
    extend = load_schema(await _read(app, sp, "domaines/ventes.extend.yaml") or "")
    group = extend["datasets"]["Tarif"]["fields"]["pays"]["referentiel"]["group"]
    assert extend["datasets"]["Tarif"]["fields"]["canal"]["referentiel"]["group"] == group
    assert "referentiel" not in (await _read(app, sp, "domaines/ventes.virtuel.ossie.yaml") or "")

    # Renommer un champ lié : son lien suit. Supprimer `canal` : le groupe devient partiel.
    edited = {
        "name": "Tarif",
        "consultable": False,
        "fields": [
            {"name": "code_statut", "previous": "statut"},
            {"name": "pays", "previous": "pays"},
            {"name": "prix", "previous": "prix"},
        ],
        "confirm": True,
    }
    r = await sp.client.put(f"{api}/ventes/datasets/Tarif", json=edited)
    assert r.status_code == 200, r.text
    detail = (await sp.client.get(f"{api}/ventes")).json()
    assert _field(detail, "code_statut")["referentiel"]["property"] == "code"
    pays = _field(detail, "pays")["referentiel"]
    assert (pays["status"], pays["missing"]) == ("partial", ["canal"])

    # Délier : le lien disparaît du `.extend.yaml`.
    r = await sp.client.delete(link, params={"field": "code_statut"})
    assert r.status_code == 200, r.text
    extend = load_schema(await _read(app, sp, "domaines/ventes.extend.yaml") or "")
    assert "code_statut" not in (extend["datasets"]["Tarif"].get("fields") or {})


async def test_lier_un_champ_de_dataset_derive_refuse(sp: ApiSpace) -> None:  # noqa: F811
    api = f"{sp.base}/domains"
    assert (await sp.client.post(api, json={"name": "ventes"})).status_code == 201
    body = {"assets": [{"source": "app", "path": ASSET}]}
    assert (await sp.client.post(f"{api}/ventes/assets", json=body)).status_code == 200
    r = await sp.client.put(
        f"{api}/ventes/datasets/Order/referentiel-link",
        json={"referentiel": "statuts-commande", "links": {"statut": "code"}},
    )
    assert (r.status_code, r.json()["error"]) == (409, "dataset_derived_readonly")
