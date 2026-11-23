from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from ossie_studio.models import GitSpace
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.parsers.demo import _key_from_path
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.collect_core import (
    TechEntry,
    TechManifest,
    dump_tech_manifest,
    parse_tech_manifest,
)
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.exclusion_prune import prune_excluded_raw
from tests.draft_helpers import DraftCollector, draft_workdir, view_dir


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _schema(title: str) -> str:
    return f'{{"title": "{title}", "type": "object"}}'


def _make_bare_with(tmp_path: Path, name: str, files: dict[str, str]) -> tuple[str, Path]:
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / f"{name}-seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for rel, content in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(content, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "origin", "main")
    return f"file://{bare}", work


def _push(work: Path) -> None:
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "change")
    _git(work, "push", "-q", "origin", "main")


@pytest.fixture
async def setup(app: FastAPI, tmp_path: Path) -> tuple[DraftCollector, GitSpace, Path, Path]:
    space_url, _ = _make_bare_with(tmp_path, "space", {"README.md": "x"})
    manager, workdir, space = await draft_workdir(app, tmp_path, space_url)
    collector = DraftCollector(SourceDownloader(tmp_path / "volume"), manager, workdir)
    return collector, space, tmp_path, Path(space_url.removeprefix("file://"))


async def _base_branch(collector: SourceCollector, space: GitSpace) -> None:
    await collector._workdir.create_base_branch(
        space.id, "work/principale", space.repo_url, "x", None
    )


def _raw(tmp_path: Path, space: GitSpace) -> Path:
    # Ce que voit l'utilisateur : la branche publiée avec son brouillon.
    return view_dir(tmp_path, space) / "sources" / "api" / "raw"


async def _collect(collector, space, source_url, *, force=False, exclusions=None):
    return await collector.collect(
        space=space,
        branch_slug="work/principale",
        branch_label="principale",
        space_username="x",
        space_token=None,
        source_slug="api",
        source_clone_url=source_url,
        source_branch="main",
        source_username="x",
        source_token=None,
        author_name="a@a",
        author_email="a@a",
        force=force,
        exclusions=exclusions,
    )


async def test_premiere_collecte_ecrit_les_schemas(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path, "src", {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    )
    report = await _collect(collector, space, src)
    assert report.added == 2
    assert report.local_commit is not None
    raw = _raw(tmp_path, space)
    assert (raw / "a.schema.yaml").is_file() and (raw / "b.schema.yaml").is_file()
    assert (raw / "manifest-jsonschema-demo.yaml").is_file()


async def test_csharp_classes_partielles_une_seule_collecte(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:

    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {
            "foo1.cs": "namespace App; public partial class Foo { public int A { get; set; } }",
            "foo2.cs": "namespace App; public partial class Foo { public int B { get; set; } }",
        },
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    # Les deux parties fusionnent en un seul schéma dès la première collecte.
    schema = load_schema((raw / "app-foo.schema.yaml").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == {"A", "B"}


async def test_resolution_references_croisees_entre_fichiers(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:

    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    # Deux classes C# dans deux fichiers : chacune son schéma, donc deux groupes
    # distincts. Le parseur laisse Ligne en x-unresolved-type ; le socle résout.
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {
            "commande.cs": "namespace App; public class Commande { public Ligne L { get; set; } }",
            "ligne.cs": "namespace App; public class Ligne { public int N { get; set; } }",
        },
    )
    report = await _collect(collector, space, src)
    assert report.resolved == 1
    raw = _raw(tmp_path, space)
    commande = load_schema((raw / "app-commande.schema.yaml").read_text(encoding="utf-8"))
    assert commande["properties"]["L"]["$ref"] == "app-ligne.schema.yaml"
    # Idempotent : une 2e collecte sans changement ne recommit rien.
    second = await _collect(collector, space, src)
    assert second.local_commit is None


async def test_markdown_copie_les_documents_et_exclut_node_modules(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {"README.md": "# Titre\n\ntexte", "node_modules/pkg/x.md": "# ignore\n"},
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    # Le document est recopié sous raw/docs/ en conservant son chemin, et indexé.
    assert (raw / "docs" / "README.md").is_file()
    assert (raw / "docs-index.yaml").is_file()
    # node_modules est exclu du scan par défaut.
    assert not (raw / "docs" / "node_modules").exists()


async def test_collecte_idempotente(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(tmp_path, "src", {"a.schema.json": _schema("A")})
    await _collect(collector, space, src)
    second = await _collect(collector, space, src)
    assert second.local_commit is None  # aucun changement, pas de commit
    assert second.unchanged == 1


async def test_fichier_modifie(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, work = _make_bare_with(
        tmp_path, "src", {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    )
    await _collect(collector, space, src)
    (work / "b.schema.json").write_text('{"title": "B2", "type": "object"}', encoding="utf-8")
    _push(work)
    report = await _collect(collector, space, src)
    assert report.modified == 1
    assert report.unchanged == 1


async def test_fichier_supprime(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, work = _make_bare_with(
        tmp_path, "src", {"a.schema.json": _schema("A"), "b.schema.json": _schema("B")}
    )
    await _collect(collector, space, src)
    (work / "b.schema.json").unlink()
    _push(work)
    report = await _collect(collector, space, src)
    assert report.deleted == 1
    assert not (_raw(tmp_path, space) / "b.schema.yaml").exists()


async def test_collecte_forcee_relit_tout(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(tmp_path, "src", {"a.schema.json": _schema("A")})
    await _collect(collector, space, src)
    forced = await _collect(collector, space, src, force=True)
    assert forced.read == 1  # relu malgré checksum identique
    assert forced.local_commit is None  # sortie déterministe : rien à committer


class _FakeEngine:
    """Faux moteur : rend un catalogue figé, sans vraie base."""

    def __init__(self, objects: list, errors: list[tuple[str, str]] | None = None) -> None:
        self._objects = objects
        self._errors = errors or []

    async def read_catalog(self, params, secret_value, *, schemas, exclusions):
        from ossie_studio.databases.base import CatalogRead

        return CatalogRead(objects=list(self._objects), errors=list(self._errors), queries=1)


async def _collect_sql(collector, space, engine, *, force=False):
    return await collector.collect_sql(
        space=space,
        branch_slug="work/principale",
        branch_label="principale",
        space_username="x",
        space_token=None,
        source_slug="db",
        engine=engine,
        dialect="postgres",
        db_params={},
        secret_value="",
        schemas=None,
        exclusions=None,
        author_name="a@a",
        author_email="a@a",
        force=force,
    )


async def test_collect_sql_ecrit_les_schemas(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:

    from ossie_studio.databases.base import CatalogColumn, CatalogObject

    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    obj = CatalogObject(
        schema="app",
        name="client",
        kind="table",
        columns=(CatalogColumn(name="id", schema={"type": "string"}, required=True, position=1),),
        primary_key=("id",),
    )
    report = await _collect_sql(collector, space, _FakeEngine([obj]))
    assert report.added == 1
    raw = view_dir(tmp_path, space) / "sources" / "db" / "raw"
    assert (raw / "manifest-postgres.yaml").is_file()
    schema = load_schema((raw / "app-client.schema.yaml").read_text(encoding="utf-8"))
    assert schema["x-kind"] == "table" and schema["x-primary-key"] == ["id"]
    # Idempotent : une 2e collecte sans changement ne recommit rien.
    second = await _collect_sql(collector, space, _FakeEngine([obj]))
    assert second.local_commit is None


async def test_collect_sql_signale_les_schemas_inaccessibles(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, _tmp, _ = setup
    await _base_branch(collector, space)
    engine = _FakeEngine([], errors=[("prive", "insufficient_privilege")])
    report = await _collect_sql(collector, space, engine)
    assert any(e.source == "prive" for e in report.errors)


async def test_exclusion_retire_tout_de_suite_coherente_avec_la_collecte(
    setup: tuple[DraftCollector, GitSpace, Path, Path],
) -> None:
    """Ticket 64e7b42f : exclure retire aussitôt du brouillon schémas et documents
    du répertoire ; la collecte suivante (avec l'exclusion) n'a plus rien à faire."""
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {
            "a.schema.json": _schema("A"),
            "tests/t.schema.json": _schema("T"),
            "tests/README.md": "# Tests\n",
            "README.md": "# Racine\n",
        },
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    tests_schema = f"{_key_from_path('tests/t.schema.json')}.schema.yaml"
    assert (raw / tests_schema).is_file() and (raw / "docs" / "tests" / "README.md").is_file()

    removed: list[str] = []

    def exclude(wc: Path) -> None:
        removed.extend(prune_excluded_raw(wc, "api", ["tests"]))

    async def write() -> None:
        await collector.dw.commit_to_branch(
            space.id, "work/principale", space.repo_url, "x", None, "a@a", "a@a", "exclut", exclude
        )

    await write()
    assert sorted(removed) == sorted([tests_schema, "docs/tests/README.md"])
    raw = _raw(tmp_path, space)
    assert not (raw / tests_schema).exists()
    assert not (raw / "docs" / "tests" / "README.md").exists()
    assert (raw / "a.schema.yaml").is_file() and (raw / "docs" / "README.md").is_file()
    assert "tests/README.md" not in (raw / "docs-index.yaml").read_text()
    # Idempotent : rien de plus à retirer.
    removed.clear()
    await write()
    assert removed == []
    # La collecte suivante, exclusion comprise, n'a plus rien à retirer.
    report = await _collect(collector, space, src, exclusions=["tests"])
    assert report.deleted == 0 and report.local_commit is None


def test_exclusion_garde_un_schema_partage_et_suit_la_regle_de_la_collecte(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "sources" / "api" / "raw"
    raw.mkdir(parents=True)
    for name in ("a.schema.yaml", "t.schema.yaml", "g.schema.yaml", "s.schema.yaml"):
        (raw / name).write_text("{}")
    manifest = TechManifest(
        entries={
            "src/A.cs": TechEntry("x", ["a.schema.yaml"], "main", "csharp"),
            "tests/A.Part.cs": TechEntry(
                "x", ["a.schema.yaml"], "main", "csharp"
            ),  # classe partielle
            "tests/T.cs": TechEntry("x", ["t.schema.yaml"], "main", "csharp"),
            "src/Gen/G.g.cs": TechEntry("x", ["g.schema.yaml"], "main", "csharp"),
            "src/Lib/S.cs": TechEntry("x", ["s.schema.yaml"], "main", "csharp"),
        }
    )
    (raw / "manifest-csharp.yaml").write_text(dump_tech_manifest(manifest))
    removed = prune_excluded_raw(tmp_path, "api", ["tests", "src/Lib", "**/*.g.cs"])
    assert sorted(removed) == ["g.schema.yaml", "s.schema.yaml", "t.schema.yaml"]
    assert (raw / "a.schema.yaml").is_file()  # encore produit par src/A.cs
    left = parse_tech_manifest((raw / "manifest-csharp.yaml").read_text())
    assert sorted(left.entries) == ["src/A.cs"]
    assert prune_excluded_raw(tmp_path, "api", ["tests"]) == []
    assert prune_excluded_raw(tmp_path, "absente", ["tests"]) == []


async def test_repertoire_exclu_retire_a_la_collecte_suivante(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, _ = _make_bare_with(
        tmp_path,
        "src",
        {
            "a.schema.json": _schema("A"),
            "tests/t.schema.json": _schema("T"),
            "node_modules/n.schema.json": _schema("N"),
        },
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    tests_schema = f"{_key_from_path('tests/t.schema.json')}.schema.yaml"
    assert (raw / tests_schema).is_file()
    # Le répertoire est exclu dans la configuration : la collecte suivante (un
    # simple rafraîchissement) retire ses schémas et ne les reprend plus.
    report = await _collect(collector, space, src, exclusions=["tests"])
    assert report.deleted == 1
    assert not (raw / tests_schema).exists()
    assert (raw / "a.schema.yaml").is_file()
    # Les défauts restent appliqués malgré une exclusion saisie.
    assert not any(p.name.startswith("node") for p in raw.iterdir())
    again = await _collect(collector, space, src, exclusions=["tests"])
    assert again.local_commit is None
