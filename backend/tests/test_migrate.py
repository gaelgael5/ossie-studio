from __future__ import annotations

from pathlib import Path

import pytest

from ossie_studio.db import create_pool
from ossie_studio.db.migrate import (
    MigrationChangedError,
    db_applied_migrations,
    db_current_version,
    file_load_migrations,
    run_migrations,
)


def _write(dir_: Path, name: str, sql: str) -> None:
    (dir_ / name).write_text(sql, encoding="utf-8")


async def test_applique_sur_base_vierge_et_est_idempotent(
    tmp_path: Path, test_database: str
) -> None:
    mig = tmp_path / "m"
    mig.mkdir()
    _write(mig, "0001_a.sql", "CREATE TABLE a (id int);")
    _write(mig, "0002_b.sql", "CREATE TABLE b (id int);")
    pool = await create_pool(test_database.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        version = await run_migrations(pool, mig)
        assert version == 2
        # Rejouer ne refait rien et ne plante pas (idempotent).
        version_again = await run_migrations(pool, mig)
        assert version_again == 2
        async with pool.acquire() as conn:
            applied = await db_applied_migrations(conn)
            assert set(applied) == {1, 2}
            assert await db_current_version(conn) == 2
    finally:
        await pool.close()


async def test_migration_appliquee_modifiee_refusee(tmp_path: Path, test_database: str) -> None:
    mig = tmp_path / "m"
    mig.mkdir()
    _write(mig, "0001_a.sql", "CREATE TABLE a (id int);")
    dsn = test_database.replace("postgresql+asyncpg://", "postgresql://")
    pool = await create_pool(dsn)
    try:
        await run_migrations(pool, mig)
        # On modifie le contenu d'une migration déjà jouée : refus (empreinte).
        _write(mig, "0001_a.sql", "CREATE TABLE a (id int, extra text);")
        with pytest.raises(MigrationChangedError):
            await run_migrations(pool, mig)
    finally:
        await pool.close()


async def test_applique_sur_base_existante_les_nouvelles_seulement(
    tmp_path: Path, test_database: str
) -> None:
    mig = tmp_path / "m"
    mig.mkdir()
    _write(mig, "0001_a.sql", "CREATE TABLE a (id int);")
    dsn = test_database.replace("postgresql+asyncpg://", "postgresql://")
    pool = await create_pool(dsn)
    try:
        assert await run_migrations(pool, mig) == 1
        _write(mig, "0002_b.sql", "CREATE TABLE b (id int);")
        assert await run_migrations(pool, mig) == 2
        async with pool.acquire() as conn:
            assert set(await db_applied_migrations(conn)) == {1, 2}
    finally:
        await pool.close()


def test_nom_de_migration_invalide_refuse(tmp_path: Path) -> None:
    _write(tmp_path, "sans_numero.sql", "SELECT 1;")
    with pytest.raises(ValueError, match="invalide"):
        file_load_migrations(tmp_path)


def test_numero_en_double_refuse(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 1;")
    _write(tmp_path, "0001_b.sql", "SELECT 1;")
    with pytest.raises(ValueError, match="double"):
        file_load_migrations(tmp_path)


def test_migrations_du_projet_chargent_sans_erreur() -> None:
    migrations = file_load_migrations()
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
