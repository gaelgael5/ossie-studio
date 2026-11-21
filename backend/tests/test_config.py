from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ossie_studio.config import Settings

TEST_MASTER_KEY = "aa" * 32


def _base_kwargs(tmp_path: Path) -> dict[str, object]:
    work = tmp_path / "work"
    sources = tmp_path / "sources"
    work.mkdir()
    sources.mkdir()
    return {
        "database_url": "postgresql://u:p@localhost:5432/db",
        "work_volume_path": work,
        "sources_volume_path": sources,
        "secrets_master_key": TEST_MASTER_KEY,
        "_env_file": None,
    }


def test_session_secret_trop_court_refuse(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    with pytest.raises(ValidationError):
        Settings(session_secret="court", **kwargs)  # type: ignore[arg-type]


def test_master_key_non_hex_refuse(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    kwargs["secrets_master_key"] = "pas-hexadecimal" * 4
    with pytest.raises(ValidationError):
        Settings(session_secret="0123456789abcdef0123456789abcdef", **kwargs)  # type: ignore[arg-type]


def test_variable_obligatoire_absente_echoue(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    del kwargs["database_url"]
    with pytest.raises(ValidationError):
        Settings(session_secret="0123456789abcdef0123456789abcdef", **kwargs)  # type: ignore[arg-type]


def test_clef_inconnue_refusee(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    kwargs["cle_inconnue"] = "x"
    with pytest.raises(ValidationError):
        Settings(session_secret="0123456789abcdef0123456789abcdef", **kwargs)  # type: ignore[arg-type]


def test_asyncpg_dsn_retire_le_prefixe_sqlalchemy(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    kwargs["database_url"] = "postgresql+asyncpg://u:p@localhost:5432/db"
    settings = Settings(session_secret="0123456789abcdef0123456789abcdef", **kwargs)  # type: ignore[arg-type]
    assert settings.asyncpg_dsn() == "postgresql://u:p@localhost:5432/db"
