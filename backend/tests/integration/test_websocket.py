"""Tests du flux temps réel.

Ces tests utilisent le client synchrone de Starlette : c'est le seul qui sache
ouvrir une vraie connexion WebSocket contre l'application ASGI. La préparation de
la base est faite dans une boucle séparée, puis le moteur est libéré pour que le
`lifespan` du client en recrée un dans sa propre boucle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from msm.config import Settings
from msm.core.permissions import Role
from msm.db.base import Base
from msm.db.session import dispose_engine, get_engine, init_engine, session_scope
from msm.main import create_app
from msm.services.auth_service import AuthService
from tests.integration.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FAKE_SERVER,
    fake_server_payload,
)

RECEIVE_TIMEOUT_MESSAGES = 200


@pytest.fixture
def ws_client(api_settings: Settings) -> Iterator[TestClient]:
    """Client synchrone authentifié, application prête."""
    application = create_app(api_settings)

    async def prepare() -> None:
        init_engine(api_settings)
        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_scope() as session:
            auth = AuthService(session, api_settings)
            await auth.create_user(
                username=ADMIN_USERNAME, password=ADMIN_PASSWORD, role=Role.ADMIN
            )
            await auth.create_user(username="lecteur", password=ADMIN_PASSWORD, role=Role.VIEWER)
        # Libéré pour que le `lifespan` recrée le moteur dans la boucle du client.
        await dispose_engine()

    asyncio.run(prepare())

    with TestClient(application) as client:
        yield client


def _login(client: TestClient, username: str = ADMIN_USERNAME) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["msm_csrf"]}


def _collect(websocket: Any, message_type: str, limit: int = RECEIVE_TIMEOUT_MESSAGES) -> Any:
    """Lit des messages jusqu'à trouver le type attendu."""
    for _ in range(limit):
        message = websocket.receive_json()
        if message["t"] == message_type:
            return message
    raise AssertionError(f"Message « {message_type} » jamais reçu")


class TestAuthentication:
    def test_connection_without_session_is_refused(self, ws_client: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect) as excinfo, ws_client.websocket_connect("/ws"):
            pass

        assert excinfo.value.code == 4401

    def test_authenticated_connection_is_accepted(self, ws_client: TestClient) -> None:
        _login(ws_client)

        with ws_client.websocket_connect("/ws") as websocket:
            ready = websocket.receive_json()

        assert ready["t"] == "ready"
        assert ready["d"]["user"] == ADMIN_USERNAME
        assert ready["seq"] == 1


class TestSubscription:
    def test_subscribe_sends_the_current_status(
        self, ws_client: TestClient, fake_server_dir: Path
    ) -> None:
        _login(ws_client)
        created = ws_client.post(
            "/api/v1/servers",
            json=fake_server_payload("survie", fake_server_dir),
            headers=_csrf(ws_client),
        )
        assert created.status_code == 201, created.text
        server_id = created.json()["id"]

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()  # ready
            websocket.send_json(
                {"t": "subscribe", "d": {"server_id": server_id, "channels": ["status"]}}
            )

            subscribed = _collect(websocket, "subscribed")
            assert subscribed["sid"] == server_id

            status = _collect(websocket, "server.status")
            assert status["d"]["state"] == "OFFLINE"

    def test_subscribe_to_unknown_server_reports_an_error(self, ws_client: TestClient) -> None:
        _login(ws_client)

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json({"t": "subscribe", "d": {"server_id": 9999}})

            error = _collect(websocket, "error")

        assert error["d"]["code"] == "NOT_FOUND"
        assert error["d"]["remediation"]

    def test_unknown_message_is_reported(self, ws_client: TestClient) -> None:
        _login(ws_client)

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json({"t": "n-importe-quoi"})

            error = _collect(websocket, "error")

        assert error["d"]["code"] == "UNKNOWN_MESSAGE"
        assert "subscribe" in error["d"]["remediation"]

    def test_ping_pong(self, ws_client: TestClient) -> None:
        _login(ws_client)

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json({"t": "ping"})

            assert _collect(websocket, "pong")["t"] == "pong"


class TestLiveLogs:
    def test_logs_are_pushed_in_real_time(
        self, ws_client: TestClient, fake_server_dir: Path
    ) -> None:
        """Le serveur pousse ses logs : aucune interrogation périodique n'intervient."""
        _login(ws_client)
        created = ws_client.post(
            "/api/v1/servers",
            json=fake_server_payload("survie", fake_server_dir),
            headers=_csrf(ws_client),
        )
        server_id = created.json()["id"]

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "t": "subscribe",
                    "d": {"server_id": server_id, "channels": ["status", "logs"]},
                }
            )
            _collect(websocket, "subscribed")

            started = ws_client.post(f"/api/v1/servers/{server_id}/start", headers=_csrf(ws_client))
            assert started.status_code == 200, started.text

            # Les lignes arrivent groupées : un message porte plusieurs lignes.
            found = False
            for _ in range(RECEIVE_TIMEOUT_MESSAGES):
                message = websocket.receive_json()
                if message["t"] == "server.log":
                    texts = [line["text"] for line in message["d"]["lines"]]
                    if any("Done" in text for text in texts):
                        found = True
                        break
            assert found, "Le message de fin de démarrage n'a pas été reçu en direct"

        ws_client.post(f"/api/v1/servers/{server_id}/stop", headers=_csrf(ws_client))


class TestPermissions:
    def test_viewer_can_follow_the_console(
        self, ws_client: TestClient, fake_server_dir: Path
    ) -> None:
        """Le rôle VIEWER a le droit de lire la console, pas d'y écrire."""
        _login(ws_client)
        created = ws_client.post(
            "/api/v1/servers",
            json=fake_server_payload("survie", fake_server_dir),
            headers=_csrf(ws_client),
        )
        server_id = created.json()["id"]

        ws_client.post("/api/v1/auth/logout", headers=_csrf(ws_client))
        _login(ws_client, "lecteur")

        with ws_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {"t": "subscribe", "d": {"server_id": server_id, "channels": ["logs"]}}
            )
            subscribed = _collect(websocket, "subscribed")

        assert "logs" in subscribed["d"]["channels"]


def test_fake_server_fixture_exists() -> None:
    """Garde-fou : les tests d'intégration dépendent de ce script."""
    assert FAKE_SERVER.is_file()
