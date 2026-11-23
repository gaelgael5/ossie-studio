"""Tests de validation et journal : erreurs liées au brouillon (bloquantes),
erreurs préexistantes (non bloquantes), avertissements acceptables."""

from __future__ import annotations

import json
from pathlib import Path

from ossie_studio.drafts.validation import blocking, run_rules, validation_log


def _put(view: Path, rel: str, text: str) -> None:
    target = view / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


MANIFEST = "version: 2\nslug: s\nlabel: S\ntype: openapi\nconnection: ''\nparameters: {}\n"


def test_yaml_illisible_erreur(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/manifest.yaml", "slug: [cassé")
    findings = run_rules(tmp_path)
    assert [(f.level, f.rule, f.path) for f in findings] == [
        ("error", "parseable", "sources/s/manifest.yaml")
    ]


def test_manifeste_invalide_erreur(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/manifest.yaml", "version: 1\nslug: s\ntype: openapi\n")
    findings = run_rules(tmp_path)
    assert [(f.rule, f.message_code) for f in findings] == [("source_manifest", "manifest_invalid")]
    assert "label_required" in findings[0].args["fields"]


def test_ref_casse_erreur(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/manifest.yaml", MANIFEST)
    _put(
        tmp_path,
        "sources/s/raw/Order.schema.yaml",
        json.dumps({"properties": {"u": {"$ref": "User.schema.yaml"}}}),
    )
    findings = run_rules(tmp_path)
    assert [(f.rule, f.args) for f in findings] == [
        ("broken_ref", {"target": "sources/s/raw/User.schema.yaml"})
    ]


def test_asset_orphelin_avertissement(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/manifest.yaml", MANIFEST)
    _put(tmp_path, "sources/s/assets/Seul.schema.yaml", "{}")
    findings = run_rules(tmp_path)
    assert [(f.level, f.rule) for f in findings] == [("warning", "orphan_asset")]


def test_contenu_sain_aucune_entree(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/manifest.yaml", MANIFEST)
    _put(tmp_path, "sources/s/raw/User.schema.yaml", json.dumps({"title": "User"}))
    _put(
        tmp_path,
        "sources/s/raw/Order.schema.yaml",
        json.dumps({"properties": {"u": {"$ref": "User.schema.yaml"}}}),
    )
    assert run_rules(tmp_path) == []


def test_erreur_preexistante_non_bloquante(tmp_path: Path) -> None:
    published = tmp_path / "publie"
    merged = tmp_path / "fusion"
    for view in (published, merged):
        _put(view, "sources/vieux/manifest.yaml", "slug: [cassé")
    _put(
        merged,
        "sources/s/raw/Order.schema.yaml",
        json.dumps({"properties": {"u": {"$ref": "X.schema.yaml"}}}),
    )
    log = validation_log(run_rules(merged), run_rules(published), {})
    states = {(e.finding.rule, e.preexisting, e.blocking) for e in log}
    assert states == {("parseable", True, False), ("broken_ref", False, True)}
    assert [e.finding.rule for e in blocking(log)] == ["broken_ref"]


def test_avertissement_accepte_puis_caduc_si_l_objet_change(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/assets/Seul.schema.yaml", "{}")
    first = run_rules(tmp_path)
    accepted = {first[0].key: first[0].fingerprint}
    assert validation_log(first, [], accepted)[0].accepted is True
    _put(tmp_path, "sources/s/assets/Seul.schema.yaml", '{"x": 1}')
    assert validation_log(run_rules(tmp_path), [], accepted)[0].accepted is False
