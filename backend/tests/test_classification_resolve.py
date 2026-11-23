"""Classification des assets et de leurs champs (ticket docflow 5e8680d7).

Valeur effective : déclarée l'emporte, sinon dérivée des champs (rang le plus
élevé, ou union en multi-valeurs) ; une valeur invalide n'affiche aucun badge.
Stockage : `classification` dans `<clé>.extend.yaml` (jamais dans le schéma).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_studio.governance.classification_resolve import (
    NOT_SINGLE,
    UNKNOWN_LEVEL,
    UNKNOWN_SCHEME,
    Gap,
    check_declaration,
    classify,
    source_levels,
)
from ossie_studio.governance.repo import Level, Scheme, write_scheme
from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.curation import classify_asset_fn, retain_asset_fn
from ossie_studio.sources.curation_assets import (
    invalid_classifications,
    read_assets,
    source_classifications,
    summarize,
)
from ossie_studio.sources.explorer_graph import ReferenceGraph


def _levels(scheme_id: str, *names_ranks: tuple[str, int]) -> list[Level]:
    return [
        Level(id=f"{scheme_id}-{i}", scheme_id=scheme_id, name=n, rank=r, position=i)
        for i, (n, r) in enumerate(names_ranks)
    ]


CLASSIF = Scheme(
    id="classification",
    name="Classification",
    is_primary=True,
    levels=_levels(
        "classification", ("Public", 1), ("Interne", 2), ("Confidentiel", 3), ("Restreint", 4)
    ),
)
RGPD9 = Scheme(
    id="rgpd-art9",
    name="RGPD — cat. particulières",
    multi_value=True,
    display_order=1,
    levels=_levels("rgpd-art9", ("Santé", 1), ("Opinions politiques", 2)),
)
SCHEMES = [CLASSIF, RGPD9]


def _asset(obj: dict | None = None, **fields: dict) -> dict:
    asset: dict = {"type": "object", "properties": {}}
    if obj is not None:
        asset["x-classification"] = obj
    for name, declared in fields.items():
        asset["properties"][name] = {"type": "string", "x-classification": declared}
    asset["properties"].setdefault("total", {"type": "number"})
    return asset


def _value(asset: dict, scheme: str = "classification"):  # type: ignore[no-untyped-def]
    return next(v for v in classify(asset, SCHEMES).object if v.scheme == scheme)


# --- résolution -----------------------------------------------------------------


def test_champ_confidentiel_donne_l_asset_confidentiel_derive() -> None:
    v = _value(_asset(client={"classification": "Confidentiel"}))
    assert (v.values, v.declared, v.extra) == (("Confidentiel",), False, 0)


def test_derive_prend_le_plus_sensible_avec_plus_n() -> None:
    v = _value(
        _asset(
            client={"classification": "Restreint"},
            id={"classification": "Interne"},
            nom={"classification": "Interne"},
        )
    )
    assert v.values == ("Restreint",) and v.extra == 1
    assert v.derived_from == ("Restreint", "Interne")


def test_declaration_l_emporte_et_signale_l_ecart() -> None:
    v = _value(_asset({"classification": "Interne"}, client={"classification": "Restreint"}))
    assert (v.values, v.declared) == (("Interne",), True)
    assert v.gap == Gap(field="client", level="Restreint")


def test_declaration_plus_sensible_sans_ecart() -> None:
    v = _value(_asset({"classification": "Restreint"}, client={"classification": "Interne"}))
    assert v.gap is None


def test_multi_valeurs_union_puis_declaration_remplace() -> None:
    a = _asset(
        x={"rgpd-art9": ["Santé"]},
        y={"rgpd-art9": ["Santé", "Opinions politiques"]},
    )
    assert _value(a, "rgpd-art9").values == ("Opinions politiques", "Santé")
    a["x-classification"] = {"rgpd-art9": ["Santé"]}
    v = _value(a, "rgpd-art9")
    assert (v.values, v.declared) == (("Santé",), True)


def test_valeur_invalide_aucun_badge_et_listee() -> None:
    a = _asset({"classification": "Secret"}, client={"classification": "Restreint"})
    c = classify(a, SCHEMES)
    v = c.object[0]
    # Pas de repli sur la valeur dérivée : aucun badge.
    assert v.values == () and v.invalid and v.declared_value == ("Secret",)
    assert [(i.field, i.value, i.reason) for i in c.invalid] == [("", "Secret", UNKNOWN_LEVEL)]


def test_schema_supprime_et_plusieurs_valeurs_pour_un_schema_unique() -> None:
    a = _asset(client={"tlp": "AMBER", "classification": ["Interne", "Public"]})
    c = classify(a, SCHEMES)
    assert sorted((i.scheme, i.reason) for i in c.invalid) == [
        ("classification", NOT_SINGLE),
        ("classification", NOT_SINGLE),
        ("tlp", UNKNOWN_SCHEME),
    ]
    assert c.fields == {}


def test_filtre_porte_sur_tous_les_niveaux_effectifs() -> None:
    c = classify(
        _asset(client={"classification": "Restreint"}, id={"classification": "Confidentiel"}),
        SCHEMES,
    )
    # L'asset affiche « Restreint », mais « Confidentiel » (d'un champ) compte aussi.
    assert ("classification", "Confidentiel") in c.levels


def test_niveau_de_la_source() -> None:
    a = classify(_asset(client={"classification": "Interne"}), SCHEMES)
    b = classify(_asset({"classification": "Restreint"}), SCHEMES)
    assert source_levels([a, b], SCHEMES) == {"classification": ("Restreint",)}


def test_check_declaration() -> None:
    assert check_declaration(CLASSIF, []) is None
    assert check_declaration(CLASSIF, ["Interne"]) is None
    assert check_declaration(CLASSIF, ["Inconnu"]) == UNKNOWN_LEVEL
    assert check_declaration(CLASSIF, ["Interne", "Public"]) == NOT_SINGLE
    assert check_declaration(None, ["x"]) == UNKNOWN_SCHEME
    assert check_declaration(RGPD9, ["Santé", "Opinions politiques"]) is None


# --- stockage et lectures -------------------------------------------------------


@pytest.fixture
def view(tmp_path: Path) -> Path:
    raw = tmp_path / "sources" / "api" / "raw"
    raw.mkdir(parents=True)
    schema = {
        "$id": "commande.schema.yaml",
        "title": "Commande",
        "type": "object",
        "properties": {"client": {"type": "string"}, "total": {"type": "number"}},
    }
    (raw / "commande.schema.yaml").write_text(dump_schema(schema), encoding="utf-8")
    (tmp_path / "sources" / "api" / "manifest.yaml").write_text("slug: api\n", encoding="utf-8")
    retain_asset_fn("api", "commande.schema.yaml", None)(tmp_path)
    for scheme in SCHEMES:
        write_scheme(tmp_path, scheme)
    return tmp_path


def test_classer_ecrit_dans_extend_jamais_dans_le_schema(view: Path) -> None:
    classify_asset_fn(
        "api",
        "commande.schema.yaml",
        {"classification": "Interne"},
        {"client": {"classification": "Restreint", "rgpd-art9": ["Santé"]}},
    )(view)
    assets = view / "sources" / "api" / "assets"
    schema_text = (assets / "commande.schema.yaml").read_text(encoding="utf-8")
    assert "classification" not in schema_text
    extend = load_schema((assets / "commande.extend.yaml").read_text(encoding="utf-8"))
    assert extend == {
        "object": {"classification": {"classification": "Interne"}},
        "fields": {
            "client": {"classification": {"classification": "Restreint", "rgpd-art9": ["Santé"]}}
        },
    }
    # Retour à « — dérivée — » : la déclaration disparaît.
    classify_asset_fn("api", "commande.schema.yaml", {"classification": []}, {})(view)
    extend = load_schema((assets / "commande.extend.yaml").read_text(encoding="utf-8"))
    assert "object" not in extend


def test_resume_de_la_liste_badges_et_niveaux(view: Path) -> None:
    classify_asset_fn(
        "api", "commande.schema.yaml", None, {"client": {"classification": "Restreint"}}
    )(view)
    [summary] = summarize(read_assets(view), ReferenceGraph([], "origin"), SCHEMES)
    [badge] = summary.classification
    assert (badge.scheme, badge.values, badge.declared) == ("classification", ("Restreint",), False)
    assert summary.class_levels == ("classification/Restreint",)


def test_valeurs_invalides_et_niveau_des_sources(view: Path) -> None:
    classify_asset_fn(
        "api",
        "commande.schema.yaml",
        {"classification": "Secret"},
        {"client": {"classification": "Interne"}},
    )(view)
    [bad] = invalid_classifications(view)
    assert (bad.source, bad.path, bad.field, bad.value, bad.reason) == (
        "api",
        "commande.schema.yaml",
        "",
        "Secret",
        UNKNOWN_LEVEL,
    )
    # La déclaration invalide de l'asset ne donne pas de badge à la source non plus.
    assert source_classifications(view) == {"api": []}
