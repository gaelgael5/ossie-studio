"""Règles de classification (ticket docflow cfc33d51) : format, correspondance
sur les champs raw, arbitrage entre règles, API et explorateur raw."""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_studio.governance.classification_rules import (
    ClassificationRule,
    effect_errors,
    parse_rule,
    read_classification_rules,
    write_classification_rule,
)
from ossie_studio.governance.repo import Level, Scheme
from ossie_studio.governance.tag_rules import parse_rule as parse_tag_rule
from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.explorer import SourceEntries, clear_caches, collect_source_entries
from ossie_studio.sources.explorer_classes import apply_classification_rules
from ossie_studio.sources.explorer_tags import apply_tag_rules
from tests.space_fixture import ApiSpace, forge_edit, make_api_space
from tests.test_raw_explorer import _manifest, _tech


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


def _schema(title: str, properties: dict[str, object], **extra: object) -> str:
    return dump_schema(
        {
            "$id": f"{title.lower()}.schema.yaml",
            "title": title,
            "type": "object",
            "x-kind": "class",
            "properties": properties,
            **extra,
        }
    )


def _files() -> dict[str, str]:
    raw = "sources/app/raw"
    return {
        "sources/app/manifest.yaml": _manifest("app"),
        f"{raw}/customer.schema.yaml": _schema(
            "Customer",
            {
                "Id": {"type": "string"},
                "Email": {"type": "string"},
                "customer_email": {"type": ["string", "null"]},
                "EmailSent": {"type": "boolean"},
                "BirthDate": {"type": "string", "format": "date-time"},
                "Phones": {"type": "array", "items": {"type": "string"}},
            },
        ),
        f"{raw}/customerdto.schema.yaml": _schema(
            "CustomerDto",
            {"Mail": {"type": "string"}, "Name": {"type": "string"}},
            allOf=[{"x-unresolved-type": "DtoBase"}],
        ),
        f"{raw}/manifest-csharp.yaml": _tech(
            {
                "src/Domain/Customer.cs": (["customer.schema.yaml"], "csharp"),
                "src/Dto/CustomerDto.cs": (["customerdto.schema.yaml"], "csharp"),
            }
        ),
    }


def _sources(tmp_path: Path) -> list[SourceEntries]:
    for rel, text in _files().items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [collect_source_entries(tmp_path, "app")]


GDPR = Scheme(
    "rgpd",
    "GDPR",
    levels=[
        Level("1", "rgpd", "Non personnelle", rank=1),
        Level("2", "rgpd", "Donnée personnelle", rank=2),
        Level("3", "rgpd", "Catégorie particulière", rank=3),
    ],
)
LABELS = Scheme(
    "lab", "Labels", multi_value=True, levels=[Level("a", "lab", "A"), Level("b", "lab", "B")]
)


def _rule(rule_id: str = "r", **data: object) -> ClassificationRule:
    rule, errors = parse_rule(rule_id, {"scheme": "rgpd", "values": ["Donnée personnelle"], **data})
    assert rule is not None, errors
    return rule


def _classes(sources: list[SourceEntries]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Titre → champ → valeurs (schéma rgpd ou lab)."""
    return {
        o.title: {f"{c.field}:{c.scheme}": c.values for c in o.field_classes}
        for s in sources
        for o in s.objects
        if o.field_classes
    }


def test_nom_sans_casse_et_type(tmp_path: Path) -> None:
    rule = _rule(when={"field": ["*email*"], "type": ["string"]})
    out = _classes(apply_classification_rules(_sources(tmp_path), [rule], [GDPR]))
    # EmailSent est un booléen : la condition de type l'écarte (match: all par défaut).
    assert set(out["Customer"]) == {"Email:rgpd", "customer_email:rgpd"}
    assert out["Customer"]["Email:rgpd"] == ("Donnée personnelle",)


def test_format_et_liste_reconnus_par_le_type(tmp_path: Path) -> None:
    rule = _rule(when={"type": ["date-time", "string[]"]})
    out = _classes(apply_classification_rules(_sources(tmp_path), [rule], [GDPR]))
    assert set(out["Customer"]) == {"BirthDate:rgpd", "Phones:rgpd"}


def test_match_any(tmp_path: Path) -> None:
    rule = _rule(match="any", when={"field": ["mail"], "type": ["boolean"]})
    out = _classes(apply_classification_rules(_sources(tmp_path), [rule], [GDPR]))
    assert set(out["Customer"]) == {"EmailSent:rgpd"}
    assert set(out["CustomerDto"]) == {"Mail:rgpd"}


def test_condition_de_classe_et_de_fichier(tmp_path: Path) -> None:
    by_class = _rule(when={"class": ["*dto"], "field": ["*"]})
    out = _classes(apply_classification_rules(_sources(tmp_path), [by_class], [GDPR]))
    assert list(out) == ["CustomerDto"]
    by_file = _rule(files=["src/Domain/*"], when={"field": ["*mail*"]})
    out = _classes(apply_classification_rules(_sources(tmp_path), [by_file], [GDPR]))
    assert list(out) == ["Customer"]


def test_condition_de_tag_y_compris_par_prefixe(tmp_path: Path) -> None:
    tag_rule, errors = parse_tag_rule(
        "dto", {"files": ["*.cs"], "when": {"inherits": ["DtoBase"]}, "tags": ["technique/dto"]}
    )
    assert tag_rule is not None, errors
    tagged = apply_tag_rules(_sources(tmp_path), [tag_rule])
    rule = _rule(when={"tag": ["technique"], "field": ["*mail*"]})
    out = _classes(apply_classification_rules(tagged, [rule], [GDPR]))
    assert out == {"CustomerDto": {"Mail:rgpd": ("Donnée personnelle",)}}


def test_plus_haut_rang_l_emporte_et_union_multi_valeurs(tmp_path: Path) -> None:
    low = _rule("low", when={"field": ["Email"]})
    high = _rule("high", when={"field": ["Email"]}, values=["Catégorie particulière"])
    a = _rule("a", when={"field": ["Email"]}, scheme="lab", values=["A"])
    b = _rule("b", when={"field": ["Email"]}, scheme="lab", values=["B"])
    sources = apply_classification_rules(_sources(tmp_path), [low, high, a, b], [GDPR, LABELS])
    out = _classes(sources)["Customer"]
    assert out["Email:rgpd"] == ("Catégorie particulière",)
    assert out["Email:lab"] == ("A", "B")
    lab = next(c for o in sources[0].objects for c in o.field_classes if c.scheme == "lab")
    assert lab.rule == "a,b"


def test_regle_desactivee_ou_invalide_ne_classe_rien(tmp_path: Path) -> None:
    off = _rule(enabled=False, when={"field": ["Email"]})
    gone = _rule(when={"field": ["Email"]}, values=["Inconnue"])
    assert effect_errors(gone, [GDPR]) == ["classification_rule_value_unknown"]
    assert effect_errors(_rule(scheme="absent", when={"field": ["x"]}), [GDPR]) == [
        "classification_rule_scheme_unknown"
    ]
    two = _rule(when={"field": ["x"]}, values=["Non personnelle", "Donnée personnelle"])
    assert effect_errors(two, [GDPR]) == ["classification_rule_single_value"]
    assert _classes(apply_classification_rules(_sources(tmp_path), [off, gone], [GDPR])) == {}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"scheme": "rgpd", "values": ["x"]}, "classification_rule_conditions_missing"),
        ({"when": {"field": ["x"]}}, "classification_rule_effect_missing"),
        (
            {"when": {"inherits": ["x"]}, "scheme": "s", "values": ["v"]},
            "classification_rule_condition_unknown",
        ),
        (
            {"when": {"field": ["x"]}, "scheme": "s", "values": ["v"], "match": "x"},
            "classification_rule_match_invalid",
        ),
    ],
)
def test_regle_invalide(data: dict[str, object], code: str) -> None:
    rule, errors = parse_rule("r", data)
    assert rule is None and code in errors


def test_aller_retour_fichier(tmp_path: Path) -> None:
    rule = _rule("emails", name="Emails", when={"field": ["*email*"]}, files=["*.cs"])
    write_classification_rule(tmp_path, rule)
    files = read_classification_rules(tmp_path)
    assert [f.rel for f in files] == ["governance/classification-rules/emails.yaml"]
    assert files[0].rule == rule


# --- API ---------------------------------------------------------------------------


@pytest.fixture
async def sp(client, app, tmp_path: Path) -> ApiSpace:  # type: ignore[no-untyped-def]
    return await make_api_space(client, app, tmp_path)


async def _gdpr(sp: ApiSpace) -> None:
    gov = f"{sp.base}/governance/classifications"
    r = await sp.client.post(gov, json={"name": "GDPR"})
    assert r.status_code == 201, r.text
    for rank, name in ((1, "Non personnelle"), (2, "Donnée personnelle")):
        r = await sp.client.post(
            f"{gov}/gdpr/levels", json={"name": name, "short_name": name[:2].upper(), "rank": rank}
        )
        assert r.status_code == 201, r.text


async def test_api_regle_classe_les_champs_visibles_dans_l_explorateur(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    await _gdpr(sp)
    rules = f"{sp.base}/governance/classification-rules"
    body = {
        "name": "Emails",
        "when": {"field": ["*email*"], "type": ["string"]},
        "scheme": "gdpr",
        "values": ["Donnée personnelle"],
    }
    # Aperçu sur l'index, avant enregistrement.
    preview = await sp.client.post(
        f"{sp.base}/raw-explorer/classification-rules/preview", json=body
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["fields"] == 2

    r = await sp.client.put(f"{rules}/emails", json=body)
    assert r.status_code == 200, r.text
    listing = (await sp.client.get(rules)).json()["rules"]
    assert [(x["id"], x["errors"]) for x in listing] == [("emails", [])]

    detail = await sp.client.get(
        f"{sp.base}/raw-explorer/app/object", params={"file": "customer.schema.yaml"}
    )
    assert detail.status_code == 200, detail.text
    by_field = {f["name"]: f["classification"] for f in detail.json()["fields"]}
    assert by_field["Email"] == [
        {
            "scheme": "gdpr",
            "values": ["Donnée personnelle"],
            "rule": "emails",
            "rule_name": "Emails",
        }
    ]
    assert by_field["EmailSent"] == []
    usage = await sp.client.get(f"{sp.base}/raw-explorer/classification-rules/usage")
    assert usage.json()["rules"] == {"emails": 2}
    fields = await sp.client.get(f"{sp.base}/raw-explorer/classification-rules/emails/fields")
    assert fields.json()["objects"][0]["fields"] == ["Email", "customer_email"]

    # Désactivée : plus rien.
    r = await sp.client.put(f"{rules}/emails", json={**body, "enabled": False})
    assert r.status_code == 200
    usage = await sp.client.get(f"{sp.base}/raw-explorer/classification-rules/usage")
    assert usage.json()["rules"] == {}


async def test_api_valeur_inconnue_refusee_et_schema_supprime_signale(sp: ApiSpace) -> None:
    forge_edit(sp, _files())
    await _gdpr(sp)
    rules = f"{sp.base}/governance/classification-rules"
    bad = {"when": {"field": ["x"]}, "scheme": "gdpr", "values": ["Secret"]}
    r = await sp.client.put(f"{rules}/bad", json=bad)
    assert r.status_code == 400
    assert r.json()["error_args"]["fields"] == ["classification_rule_value_unknown"]
    ok = {**bad, "values": ["Non personnelle"]}
    assert (await sp.client.put(f"{rules}/ok", json=ok)).status_code == 200
    # Le schéma disparaît après coup : la règle est signalée, pas d'erreur 500.
    assert (await sp.client.delete(f"{sp.base}/governance/classifications/gdpr")).status_code == 204
    listing = (await sp.client.get(rules)).json()["rules"]
    assert listing[0]["errors"] == ["classification_rule_scheme_unknown"]
    usage = await sp.client.get(f"{sp.base}/raw-explorer/classification-rules/usage")
    assert usage.status_code == 200 and usage.json()["rules"] == {}
    assert (await sp.client.delete(f"{rules}/ok")).status_code == 204
    assert (await sp.client.delete(f"{rules}/ok")).status_code == 404
