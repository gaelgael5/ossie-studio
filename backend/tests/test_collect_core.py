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
    assert set(plan.writes) == {"a.schema.yaml", "b.schema.yaml"}
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
    assert set(second.writes) == {"b.schema.yaml"}
    assert second.modified == 1
    assert second.unchanged == 1


def test_fichier_supprime() -> None:
    files = {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    del files["b.schema.json"]
    second = run_collection(files, PARSERS, first.manifest, disk)
    assert "b.schema.yaml" in second.deletes
    assert second.deleted == 1
    assert "b.schema.json" not in second.manifest.entries


def test_version_parseur_change_retransforme_tout() -> None:
    files = {"a.schema.json": _schema("A")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    stale = first.manifest
    stale.parsers = {"jsonschema-demo": "0.9"}  # ancienne version
    second = run_collection(files, PARSERS, stale, disk)
    assert set(second.writes) == {"a.schema.yaml"}  # retransformé malgré checksum identique


def test_collecte_forcee() -> None:
    files = {"a.schema.json": _schema("A")}
    first = run_collection(files, PARSERS, TechManifest(), {})
    disk = _apply(first, {})
    forced = run_collection(files, PARSERS, first.manifest, disk, force=True)
    assert set(forced.writes) == {"a.schema.yaml"}


def test_fichier_illisible_signale_et_continue() -> None:
    files = {"bon.schema.json": _schema("Bon"), "casse.schema.json": b"{ pas du json"}
    plan = run_collection(files, PARSERS, TechManifest(), {})
    assert "bon.schema.yaml" in plan.writes
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
    plan = run_collection(files, PARSERS, TechManifest(), {}, branch="work/ventes")
    from ossie_studio.sources.collect_core import dump_tech_manifest

    reloaded = parse_tech_manifest(dump_tech_manifest(plan.manifest))
    assert reloaded.parsers == {"jsonschema-demo": "1.0"}
    assert reloaded.entries["a.schema.json"].branch == "work/ventes"
    assert reloaded.entries["a.schema.json"].destinations == ["a.schema.yaml"]


def test_manifeste_technique_ecrit_en_anglais() -> None:
    files = {"a.schema.json": _schema("A")}
    plan = run_collection(files, PARSERS, TechManifest(), {}, branch="main")
    from ossie_studio.sources.collect_core import dump_tech_manifest

    text = dump_tech_manifest(plan.manifest)
    assert text.startswith("version: 2\n")
    for key in ("parsers:", "entries:", "branch: main", "parser: jsonschema-demo"):
        assert key in text
    for french in ("parseurs:", "entrees:", "branche:", "parseur:"):
        assert french not in text


def test_manifeste_technique_version_1_relu() -> None:
    # Un `raw/` collecté avant le passage aux clés anglaises reste lisible.
    legacy = (
        "version: 1\nparseurs:\n  jsonschema-demo: '1.0'\nentrees:\n"
        "- source: a.schema.json\n  checksum: abc\n  destination: [a.schema.yaml]\n"
        "  branche: work/ventes\n  parseur: jsonschema-demo\n"
    )
    manifest = parse_tech_manifest(legacy)
    assert manifest.parsers == {"jsonschema-demo": "1.0"}
    entry = manifest.entries["a.schema.json"]
    assert (entry.checksum, entry.destinations) == ("abc", ["a.schema.yaml"])
    assert (entry.branch, entry.parser) == ("work/ventes", "jsonschema-demo")



def test_regle_d_exclusion() -> None:
    from ossie_studio.sources.collect import is_excluded

    # Nom de dossier : exclu où qu'il soit ; jamais un nom de fichier.
    assert is_excluded("src/tests/a.cs", ["tests"])
    assert not is_excluded("src/tests.cs", ["tests"])
    # Chemin depuis la racine : ce répertoire seulement, avec son contenu.
    assert is_excluded("src/App.Tests/a.cs", ["/src/App.Tests/"])
    assert not is_excluded("other/src/App.Tests/a.cs", ["src/App.Tests"])
    assert not is_excluded("src/App.TestsHelpers/a.cs", ["src/App.Tests"])
    # Motif.
    assert is_excluded("a/test/b.cs", ["**/test/**"])
    # Défauts toujours appliqués, même avec une exclusion saisie.
    assert is_excluded("node_modules/x/a.md", ["tests"])
    assert is_excluded(".git/config", None)
    assert not is_excluded("src/a.cs", ["", "  "])
