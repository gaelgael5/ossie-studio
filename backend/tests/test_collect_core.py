from __future__ import annotations

from ossie_studio.parsers.demo import DemoJsonSchemaParser
from ossie_studio.sources.collect_core import (
    TechManifest,
    parse_tech_manifest,
    run_collection,
)

PARSERS = (DemoJsonSchemaParser(),)


def _schema(name: str) -> bytes:
    return f'{{"title": "{name}", "type": "object"}}'.encode()


def _apply(plan, disk: dict[str, str]) -> dict[str, str]:
    """Simule l'écriture d'un plan sur le disque (raw/)."""
    disk = dict(disk)
    for dest, content in plan.writes.items():
        disk[dest] = content
    for dest in plan.deletes:
        disk.pop(dest, None)
    return disk


def test_premiere_collecte() -> None:
    files = {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    plan = run_collection(files, PARSERS, TechManifest(), {})
    assert plan.added == 2
    assert plan.unchanged == 0
    assert set(plan.writes) == {"a.json", "b.json"}
    assert len(plan.manifest.entries) == 2


def test_deuxieme_collecte_sans_changement() -> None:
    files = {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    second = run_collection(files, PARSERS, first.manifest, disk)
    assert second.writes == {}
    assert second.deletes == []
    assert second.unchanged == 2
    assert second.added == 0 and second.modified == 0


def test_fichier_modifie_seul_retransforme() -> None:
    files = {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    files["b.schema.json"] = b'{"title": "B2", "type": "object"}'
    second = run_collection(files, PARSERS, first.manifest, disk)
    assert set(second.writes) == {"b.json"}
    assert second.modified == 1
    assert second.unchanged == 1


def test_fichier_supprime() -> None:
    files = {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    del files["b.schema.json"]
    second = run_collection(files, PARSERS, first.manifest, disk)
    assert "b.json" in second.deletes
    assert second.deleted == 1
    assert "b.schema.json" not in second.manifest.entries


def test_version_parseur_change_retransforme_tout() -> None:
    files = {"a.schema.json": _schema("A")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    stale = first.manifest
    stale.parseurs = {"jsonschema-demo": "0.9"}  # ancienne version
    second = run_collection(files, PARSERS, stale, disk)
    assert set(second.writes) == {"a.json"}  # retransformé malgré checksum identique


def test_collecte_forcee() -> None:
    files = {"a.schema.json": _schema("A")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    forced = run_collection(files, PARSERS, first.manifest, disk, force=True)
    assert set(forced.writes) == {"a.json"}


def test_fichier_illisible_signale_et_continue() -> None:
    files = {"bon.schema.json": _schema("Bon"), "casse.schema.json": b"{ pas du json"}
    plan = run_collection(files, PARSERS, TechManifest(), {})
    assert "bon.json" in plan.writes
    assert [e.source for e in plan.errors] == ["casse.schema.json"]
    # Le fichier en erreur n'est pas enregistré (pas de checksum) → retenté ensuite.
    assert "casse.schema.json" not in plan.manifest.entries


def test_determinisme_octet_pour_octet() -> None:
    files = {"a.schema.json": _schema("A")}
    one = run_collection(files, PARSERS, TechManifest(), {})
    two = run_collection(files, PARSERS, TechManifest(), {})
    assert one.writes == two.writes


def test_manifeste_technique_roundtrip() -> None:
    files = {"a.schema.json": _schema("A")}
    plan = run_collection(files, PARSERS, TechManifest(), {}, branche="work/ventes")
    from ossie_studio.sources.collect_core import dump_tech_manifest

    reloaded = parse_tech_manifest(dump_tech_manifest(plan.manifest))
    assert reloaded.parseurs == {"jsonschema-demo": "1.0"}
    assert reloaded.entries["a.schema.json"].branche == "work/ventes"
    assert reloaded.entries["a.schema.json"].destinations == ["a.json"]
