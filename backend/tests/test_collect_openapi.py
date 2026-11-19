from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ossie_studio.models import GitSpace
from ossie_studio.sources.collect import SourceCollector
from ossie_studio.sources.contracts import (
    ContractEntry,
    ContractNotFoundError,
    deposit_contract_fn,
    remove_contract_fn,
)
from ossie_studio.sources.download import SourceDownloader
from ossie_studio.sources.manifest import SourceManifest
from ossie_studio.sources.service import declare_source
from ossie_studio.spaces.workdir import WorkdirManager

CONTRACT_V1 = """
openapi: 3.0.3
info:
  title: API boutique
  version: 1.0.0
paths:
  /commandes:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Commande'
      responses:
        '200':
          description: ok
components:
  schemas:
    Commande:
      type: object
      properties:
        note:
          type: string
          nullable: true
        ligne:
          $ref: '#/components/schemas/Ligne'
    Ligne:
      type: object
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_bare(tmp_path: Path) -> str:
    bare = tmp_path / "space.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(bare)], check=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("x", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "origin", "main")
    return f"file://{bare}"


@pytest.fixture
def setup(tmp_path: Path) -> tuple[SourceCollector, GitSpace, Path]:
    workdir = WorkdirManager(tmp_path / "work")
    downloader = SourceDownloader(tmp_path / "volume")
    collector = SourceCollector(workdir, downloader)
    space = GitSpace(
        id=uuid.uuid4(),
        name="modele",
        connection_id=uuid.uuid4(),
        repo_url=_make_bare(tmp_path),
        last_probe_status="valid",
        last_probe_at=None,
        created_at=datetime.now(UTC),
    )
    return collector, space, tmp_path


def _raw(tmp_path: Path, space: GitSpace) -> Path:
    return tmp_path / "work" / str(space.id) / "principale" / "sources" / "api" / "raw"


async def _prepare_source(collector: SourceCollector, space: GitSpace) -> None:
    await collector._workdir.create_base_branch(space.id, "principale", space.repo_url, "x", None)
    manifest = SourceManifest(slug="api", label="API", type="openapi", connexion="", parametres={})
    await declare_source(
        collector._workdir, space, "principale", "x", None, "a@a", "a@a", manifest
    )


def _entry(version: str) -> ContractEntry:
    return ContractEntry(
        version=version,
        fichier=f"{version}.yaml",
        fichier_origine=f"boutique-{version}.yaml",
        openapi="3.0.3",
        version_declaree="1.0.0",
        nb_schemas=2,
        nb_operations=1,
        depose_le=datetime.now(UTC).isoformat(),
        auteur="a@a",
    )


async def _commit(collector: SourceCollector, space: GitSpace, write_fn, msg: str) -> None:
    await collector._workdir.commit_to_branch(
        space.id, "principale", space.repo_url, "x", None, "a@a", "a@a", msg, write_fn
    )


async def _collect(collector: SourceCollector, space: GitSpace, *, force: bool = False):
    return await collector.collect_openapi(
        space=space,
        branch_slug="principale",
        branche_label="principale",
        space_username="x",
        space_token=None,
        source_slug="api",
        author_name="a@a",
        author_email="a@a",
        force=force,
    )


async def test_collecte_openapi_ecrit_les_schemas(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, tmp_path = setup
    await _prepare_source(collector, space)
    await _commit(
        collector,
        space,
        deposit_contract_fn("api", _entry("v1"), CONTRACT_V1.encode("utf-8"), replace=False),
        "dépose v1",
    )
    report = await _collect(collector, space)
    assert report.added == 2  # Commande + Ligne
    assert report.local_commit is not None
    raw = _raw(tmp_path, space)
    assert (raw / "v1-commande.json").is_file()
    assert (raw / "v1-ligne.json").is_file()


async def test_collecte_openapi_idempotente(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, _ = setup
    await _prepare_source(collector, space)
    await _commit(
        collector,
        space,
        deposit_contract_fn("api", _entry("v1"), CONTRACT_V1.encode("utf-8"), replace=False),
        "dépose v1",
    )
    await _collect(collector, space)
    second = await _collect(collector, space)
    assert second.local_commit is None
    assert second.unchanged == 1  # un contrat, inchangé


async def test_retrait_contrat_supprime_ses_schemas(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, tmp_path = setup
    await _prepare_source(collector, space)
    await _commit(
        collector,
        space,
        deposit_contract_fn("api", _entry("v1"), CONTRACT_V1.encode("utf-8"), replace=False),
        "dépose v1",
    )
    await _collect(collector, space)
    raw = _raw(tmp_path, space)
    assert (raw / "v1-commande.json").is_file()
    # Retrait : le fichier et ses schémas quittent la branche, en un commit.
    await _commit(collector, space, remove_contract_fn("api", "v1"), "retire v1")
    assert not (raw / "v1-commande.json").exists()
    assert not (raw / "v1-ligne.json").exists()
    contrats = tmp_path / "work" / str(space.id) / "principale" / "sources" / "api" / "contrats"
    assert not (contrats / "v1.yaml").exists()


async def test_versions_coexistent_sur_le_disque(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, tmp_path = setup
    await _prepare_source(collector, space)
    await _commit(
        collector,
        space,
        deposit_contract_fn("api", _entry("v1"), CONTRACT_V1.encode("utf-8"), replace=False),
        "dépose v1",
    )
    await _commit(
        collector,
        space,
        deposit_contract_fn("api", _entry("v2"), CONTRACT_V1.encode("utf-8"), replace=False),
        "dépose v2",
    )
    report = await _collect(collector, space)
    raw = _raw(tmp_path, space)
    # v1 et v2 produisent des schémas distincts.
    assert (raw / "v1-commande.json").is_file()
    assert (raw / "v2-commande.json").is_file()
    assert report.added == 4


async def test_collecte_sans_contrat_ne_produit_aucun_schema(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, _ = setup
    await _prepare_source(collector, space)
    # Source déclarée mais aucun contrat déposé : rien à transformer.
    report = await _collect(collector, space)
    assert report.added == 0
    assert report.retained == 0
    # Idempotent : un second passage ne recommit rien.
    assert (await _collect(collector, space)).local_commit is None


async def test_remove_contrat_inexistant_leve(
    setup: tuple[SourceCollector, GitSpace, Path],
) -> None:
    collector, space, _ = setup
    await _prepare_source(collector, space)
    with pytest.raises(ContractNotFoundError):
        await _commit(collector, space, remove_contract_fn("api", "v9"), "retire v9")
