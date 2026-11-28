"""Tests de validation et journal : erreurs liées au brouillon (bloquantes),
erreurs préexistantes (non bloquantes), avertissements acceptables."""

from __future__ import annotations

import json
from pathlib import Path

from ossie_studio.drafts.validation import LoggedFinding, blocking, run_rules


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


def test_regle_d_or_une_erreur_preexistante_bloque(tmp_path: Path) -> None:
    """Ticket d7139b17 : une erreur déjà présente côté publié bloque aussi (« sinon
    personne ne corrige les problèmes ») ; un avertissement ne bloque jamais et ne
    s'accepte plus."""
    _put(tmp_path, "sources/vieux/manifest.yaml", "slug: [cassé")
    _put(
        tmp_path,
        "sources/s/raw/Order.schema.yaml",
        json.dumps({"properties": {"u": {"$ref": "X.schema.yaml"}}}),
    )
    _put(tmp_path, "sources/s/assets/Seul.schema.yaml", "{}")
    log = [LoggedFinding(f) for f in run_rules(tmp_path)]
    assert sorted(e.finding.rule for e in blocking(log)) == ["broken_ref", "parseable"]
    assert {e.finding.rule for e in log if not e.blocking} == {"orphan_asset"}
    assert all("accepted" not in e.payload() and "preexisting" not in e.payload() for e in log)
