from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from ossie_studio.models import GitSpace
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.download import SourceDownloader
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


async def _collect(collector, space, source_url, *, force=False):
    return await collector.collect(
        space=space,
        branch_slug="work/principale",
        branche_label="principale",
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
    assert (raw / "a.json").is_file() and (raw / "b.json").is_file()
    assert (raw / "manifest-jsonschema-demo.yaml").is_file()


async def test_csharp_classes_partielles_une_seule_collecte(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    import json

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
    schema = json.loads((raw / "app-foo.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == {"A", "B"}


async def test_resolution_references_croisees_entre_fichiers(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    import json

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
    commande = json.loads((raw / "app-commande.json").read_text(encoding="utf-8"))
    assert commande["properties"]["L"]["$ref"] == "app-ligne.json"
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
    assert not (_raw(tmp_path, space) / "b.json").exists()


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
        branche_label="principale",
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
    import json

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
    schema = json.loads((raw / "app-client.json").read_text(encoding="utf-8"))
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
