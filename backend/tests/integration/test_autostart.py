"""Tests du démarrage automatique des serveurs au lancement de MSM.

Le cas d'usage : la machine redémarre. MSM revient par systemd, et les serveurs
marqués « démarrer avec MSM » doivent repartir seuls — mais **uniquement** ceux-là,
et jamais ceux qui tournent déjà.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def _set_autostart(admin: ApiClient, server_id: int, value: bool) -> None:
    response = await admin.put(
        f"/api/v1/servers/{server_id}", json={"settings": {"autostart_on_boot": value}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["autostart_on_boot"] is value


async def _autostart(app) -> int:
    from msm.db.session import session_scope
    from msm.services.server_service import ServerService

    async with session_scope() as session:
        service = ServerService(session, app.state.settings, app.state.supervisor)
        return await service.autostart()


class TestAutostart:
    async def test_a_marked_server_starts_on_its_own(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        from msm.core.states import ServerState

        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("survie", fake_server_dir)
        )
        server_id = created.json()["id"]
        await _set_autostart(admin, server_id, True)

        assert await _autostart(app) == 1

        runtime = app.state.supervisor.find(server_id)
        assert runtime.state in (ServerState.STARTING, ServerState.ONLINE)

        await admin.post(f"/api/v1/servers/{server_id}/stop")

    async def test_an_unmarked_server_stays_stopped(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        """C'est le défaut : rien ne démarre sans qu'on l'ait demandé."""
        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("survie", fake_server_dir)
        )
        server_id = created.json()["id"]

        assert await _autostart(app) == 0
        assert not app.state.supervisor.find(server_id).state.is_running

    async def test_an_already_running_server_is_left_alone(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        """Relancer un serveur en ligne couperait ses joueurs pour rien."""
        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("survie", fake_server_dir)
        )
        server_id = created.json()["id"]
        await _set_autostart(admin, server_id, True)
        await admin.post(f"/api/v1/servers/{server_id}/start")

        pid_before = app.state.supervisor.find(server_id).pid

        assert await _autostart(app) == 0
        assert app.state.supervisor.find(server_id).pid == pid_before

        await admin.post(f"/api/v1/servers/{server_id}/stop")

    async def test_one_failure_does_not_stop_the_others(
        self, admin: ApiClient, fake_server_dir: Path, tmp_path: Path, app
    ) -> None:
        """Un serveur cassé ne doit pas empêcher les autres de repartir.

        Le cas est concret au démarrage d'une machine : un disque de données qui
        n'a pas été remonté fait disparaître le dossier d'un serveur, et les
        autres n'ont aucune raison d'en pâtir.
        """
        import shutil

        broken_dir = tmp_path / "disque-absent"
        broken_dir.mkdir()
        broken = await admin.post("/api/v1/servers", json=fake_server_payload("absent", broken_dir))
        assert broken.status_code == 201, broken.text
        await _set_autostart(admin, broken.json()["id"], True)
        # Le dossier disparaît après l'enregistrement, comme un disque non remonté.
        shutil.rmtree(broken_dir)

        healthy = await admin.post(
            "/api/v1/servers", json=fake_server_payload("survie", fake_server_dir)
        )
        healthy_id = healthy.json()["id"]
        await _set_autostart(admin, healthy_id, True)

        assert await _autostart(app) == 1
        assert app.state.supervisor.find(healthy_id).state.is_running

        await admin.post(f"/api/v1/servers/{healthy_id}/stop")
