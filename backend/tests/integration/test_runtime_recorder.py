"""Persistance de l'état des processus, dont dépend la réadoption."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from msm.bus import get_event_bus
from msm.core.states import ServerState
from msm.db.repositories import ServerRepository
from msm.db.session import session_scope
from msm.services.runtime_recorder import RuntimeStateRecorder
from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def test_lost_insert_race_is_retried(
    app: FastAPI, admin: ApiClient, fake_server_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Au premier démarrage, la requête et l'enregistreur créent la même ligne.

    Celui qui perd la course voyait son écriture refusée par la contrainte
    d'unicité, et le PID n'était jamais enregistré : le serveur n'était pas
    réadopté au redémarrage suivant de MSM.
    """
    created = await admin.post(
        "/api/v1/servers", json=fake_server_payload("survie", fake_server_dir)
    )
    server_id = created.json()["id"]

    original = ServerRepository.save_runtime_state
    calls = {"count": 0}

    async def racing(self: ServerRepository, *args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ServerRepository, "save_runtime_state", racing)

    runtime = SimpleNamespace(
        state=ServerState.ONLINE, pid=4242, group_id=4242, process_create_time=123.0
    )
    supervisor = SimpleNamespace(find=lambda _id: runtime)
    recorder = RuntimeStateRecorder(get_event_bus(), supervisor)  # type: ignore[arg-type]

    await recorder._persist({"id": server_id, "consecutive_crashes": 0})

    assert calls["count"] == 2
    async with session_scope() as session:
        server = await ServerRepository(session).get(server_id)
        assert server is not None and server.runtime_state is not None
        assert server.runtime_state.pid == 4242
