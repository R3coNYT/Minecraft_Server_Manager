"""Tests de l'application HTTP : démarrage, santé, format des erreurs."""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from msm.config import Settings
from msm.exceptions import ServerStartFailed
from msm.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        secret_key="x" * 64,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
async def client(settings: Settings):
    app = create_app(settings)

    # Route de démonstration : vérifie le format des erreurs métier.
    router = APIRouter()

    @router.get("/api/v1/_test/boom")
    async def boom() -> None:
        raise ServerStartFailed(
            "Impossible de démarrer le serveur.",
            cause="run.sh n'est pas exécutable.",
            remediation="chmod +x /data/minecraft/modded/run.sh",
        )

    app.include_router(router)

    transport = ASGITransport(app=app)
    # Le contexte `lifespan` doit être déclenché explicitement en test.
    async with (
        AsyncClient(transport=transport, base_url="http://test") as http_client,
        app.router.lifespan_context(app),
    ):
        yield http_client


async def test_health_reports_the_runtime_environment(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["process_backend"] in ("posix", "windows")
    assert payload["servers_registered"] == 0


async def test_system_stats(client: AsyncClient) -> None:
    response = await client.get("/api/v1/system/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload["memory_total_mb"] > 0
    assert payload["cpu_count"] >= 1


async def test_launchers_are_listed_with_availability(client: AsyncClient) -> None:
    response = await client.get("/api/v1/system/launchers")

    assert response.status_code == 200
    keys = {item["key"] for item in response.json()}
    assert {"jar", "shell", "batch", "custom"} <= keys


async def test_business_errors_expose_cause_and_remediation(client: AsyncClient) -> None:
    """C'est ce format qui alimente l'affichage « Cause / Action » de l'interface."""
    response = await client.get("/api/v1/_test/boom")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "SERVER_START_FAILED"
    assert payload["cause"] == "run.sh n'est pas exécutable."
    assert payload["remediation"].startswith("chmod +x")
    assert payload["trace_id"]


async def test_unknown_route_returns_the_common_error_shape(client: AsyncClient) -> None:
    response = await client.get("/api/v1/inexistant")

    assert response.status_code == 404
    assert response.json()["code"] == "HTTP_404"
