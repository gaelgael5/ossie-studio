from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.health import resolve_health
from ossie_studio.volumes import check_volume_writable
from tests.conftest import make_settings


async def test_health_endpoint_200_quand_tout_va(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["database"]["reachable"] is True
    assert body["database"]["up_to_date"] is True
    assert all(v["writable"] for v in body["volumes"])


async def test_health_503_si_volume_absent(
    app: FastAPI, tmp_path: Path, test_database: str
) -> None:
    # Settings dont le volume de travail pointe un répertoire inexistant.
    settings = make_settings(tmp_path, test_database)
    object.__setattr__(settings, "work_volume_path", tmp_path / "absent")
    report = await resolve_health(app.state.pool, settings)
    assert report.ok is False
    work = next(v for v in report.volumes if v.name == "work")
    assert work.writable is False
    assert work.detail is not None


def test_volume_inscriptible_detecte(tmp_path: Path) -> None:
    status = check_volume_writable("work", tmp_path)
    assert status.writable is True


def test_volume_absent_detecte(tmp_path: Path) -> None:
    status = check_volume_writable("work", tmp_path / "nexiste-pas")
    assert status.writable is False
    assert status.detail == "répertoire absent"
