"""Règle de publication des domaines (ticket 661efd1c) : assets référencés absents
signalés en avertissement (choix « seulement le dire », bug 206d434c), domaine
illisible en erreur ; fichiers illisibles hors sources lus par `parseable`."""

from __future__ import annotations

from pathlib import Path

from ossie_studio.domains.documents import AssetRef, Dataset, Domain, DomainField, write_domain
from ossie_studio.domains.rules import DomainRule
from ossie_studio.drafts.validation import LoggedFinding, blocking, run_rules


def _dataset(name: str) -> Dataset:
    return Dataset(
        name.title(),
        f"raw:colis/{name}",
        False,
        True,
        asset=AssetRef("colis", f"{name}.schema.yaml"),
        fields=[DomainField("id", "id")],
    )


def _domain(root: Path) -> None:
    write_domain(
        root,
        Domain(name="pudo", label="Pudo au colis", datasets=[_dataset("boxes"), _dataset("site")]),
    )


def test_assets_absents_un_avertissement_qui_les_nomme(tmp_path: Path) -> None:
    _domain(tmp_path)
    (tmp_path / "sources/colis/assets").mkdir(parents=True)
    (tmp_path / "sources/colis/assets/site.schema.yaml").write_text("title: Site\n")
    [finding] = DomainRule().check(tmp_path)
    assert (finding.level, finding.path, finding.message_code) == (
        "warning",
        "domaines/pudo.extend.yaml",
        "domain_assets_missing",
    )
    assert finding.args == {
        "domain": "Pudo au colis",
        "count": "1",
        "assets": "colis/boxes.schema.yaml",
    }
    # Avertissement : informe, ne bloque jamais la publication (ticket d7139b17).
    assert blocking([LoggedFinding(finding)]) == []


def test_acceptation_retombe_si_un_autre_asset_disparait(tmp_path: Path) -> None:
    _domain(tmp_path)
    (tmp_path / "sources/colis/assets").mkdir(parents=True)
    site = tmp_path / "sources/colis/assets/site.schema.yaml"
    site.write_text("title: Site\n")
    [before] = DomainRule().check(tmp_path)
    site.unlink()
    [after] = DomainRule().check(tmp_path)
    assert before.key == after.key and before.fingerprint != after.fingerprint
    assert after.args["count"] == "2"


def test_tous_les_assets_presents_rien(tmp_path: Path) -> None:
    _domain(tmp_path)
    assets = tmp_path / "sources/colis/assets"
    assets.mkdir(parents=True)
    for name in ("boxes", "site"):
        (assets / f"{name}.schema.yaml").write_text(f"title: {name}\n")
    assert DomainRule().check(tmp_path) == []


def test_domaine_illisible_erreur_bloquante(tmp_path: Path) -> None:
    _domain(tmp_path)
    (tmp_path / "domaines/pudo.extend.yaml").write_text("datasets: [pas un objet\n")
    findings = run_rules(tmp_path, [DomainRule()])
    assert [(f.level, f.message_code) for f in findings] == [("error", "domain_unreadable")]
    assert len(blocking([LoggedFinding(f) for f in findings])) == 1


def test_parseable_lit_cartographie_referentiels_domaines(tmp_path: Path) -> None:
    for rel in (
        "cartographie/objets/t/a.yaml",
        "referentiels/r/valeurs.json",
        "domaines/x.extend.yaml",
    ):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{cassé: [")
    parseable = [f.path for f in run_rules(tmp_path) if f.rule == "parseable"]
    assert sorted(parseable) == [
        "cartographie/objets/t/a.yaml",
        "domaines/x.extend.yaml",
        "referentiels/r/valeurs.json",
    ]
