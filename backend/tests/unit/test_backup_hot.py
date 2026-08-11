"""Tests de la suspension des écritures pendant une sauvegarde à chaud."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from msm.backup.hot import BackupNotSafe, frozen_world
from msm.bus import EventBus, topics
from msm.core.log_line import LogLevel, LogLine

pytestmark = pytest.mark.asyncio


class FakeServer:
    """Serveur factice : répond aux commandes en publiant des lignes de console."""

    def __init__(self, bus: EventBus, *, answers: bool = True, server_id: int = 1) -> None:
        self.id = server_id
        self.commands: list[str] = []
        self._bus = bus
        self._answers = answers
        self._seq = 0

    async def send_command(self, command: str, *, actor: str | None = None) -> str:
        self.commands.append(command)
        if not self._answers:
            return command

        replies = {
            "save-off": "Automatic saving is now disabled",
            "save-all flush": "Saved the game",
            "save-on": "Automatic saving is now enabled",
        }
        if (reply := replies.get(command)) is not None:
            self._emit(reply)
        return command

    def _emit(self, text: str) -> None:
        self._seq += 1
        self._bus.publish(
            topics.server_topic(self.id, topics.LOG),
            LogLine(
                seq=self._seq,
                ts=datetime.now(UTC),
                text=text,
                raw=text,
                level=LogLevel.INFO,
            ),
        )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


class TestFrozenWorld:
    async def test_sequence_is_save_off_flush_then_save_on(self, bus: EventBus) -> None:
        server = FakeServer(bus)

        async with frozen_world(server, bus, timeout=2.0):
            # Au moment de la copie, l'écriture est suspendue et le monde à jour.
            assert server.commands == ["save-off", "save-all flush"]

        assert server.commands == ["save-off", "save-all flush", "save-on"]

    async def test_writing_is_restored_even_if_the_copy_fails(self, bus: EventBus) -> None:
        """Un serveur laissé sans sauvegarde automatique perdrait tout au prochain plantage."""
        server = FakeServer(bus)

        with pytest.raises(RuntimeError):
            async with frozen_world(server, bus, timeout=2.0):
                raise RuntimeError("disque plein")

        assert server.commands[-1] == "save-on"

    async def test_silent_server_refuses_the_backup(self, bus: EventBus) -> None:
        """Sans confirmation, copier produirait un monde incohérent : on refuse."""
        server = FakeServer(bus, answers=False)

        with pytest.raises(BackupNotSafe) as excinfo:
            async with frozen_world(server, bus, timeout=0.2):
                pytest.fail("Le bloc ne doit pas être exécuté.")

        assert excinfo.value.remediation
        # L'écriture est rétablie malgré tout : on ignore si `save-off` est passé.
        assert server.commands == ["save-off", "save-on"]

    async def test_a_reply_arriving_immediately_is_not_missed(self, bus: EventBus) -> None:
        """L'abonnement est ouvert avant l'envoi, sinon la réponse passe à côté."""
        server = FakeServer(bus)

        async with frozen_world(server, bus, timeout=0.5):
            pass

        assert "save-all flush" in server.commands

    async def test_bukkit_wording_is_recognised(self, bus: EventBus) -> None:
        """Les forks Bukkit répondent autrement que le serveur officiel."""

        class BukkitServer(FakeServer):
            async def send_command(self, command: str, *, actor: str | None = None) -> str:
                self.commands.append(command)
                replies = {
                    "save-off": "Turned off world auto-saving",
                    "save-all flush": "Saved the world",
                }
                if (reply := replies.get(command)) is not None:
                    self._emit(reply)
                return command

        server = BukkitServer(bus)

        async with frozen_world(server, bus, timeout=2.0):
            pass

        assert server.commands == ["save-off", "save-all flush", "save-on"]

    async def test_a_player_cannot_fake_the_confirmation(self, bus: EventBus) -> None:
        """Un joueur écrivant la phrase du serveur dans le chat ne confirme rien.

        Sa ligne porte son pseudo (« <Flavien> Saved the game ») : les motifs
        sont ancrés en début de message. Sans cela, n'importe qui pourrait faire
        copier un monde encore en cours d'écriture.
        """
        server = FakeServer(bus, answers=False)
        loop = asyncio.get_running_loop()
        noise = [
            loop.call_later(
                0.05, lambda: server._emit("<Flavien> Automatic saving is now disabled")
            ),
            loop.call_later(0.08, lambda: server._emit("<Flavien> Saved the game")),
        ]

        with pytest.raises(BackupNotSafe):
            async with frozen_world(server, bus, timeout=0.3):
                pytest.fail("Le bloc ne doit pas être exécuté.")

        for timer in noise:
            timer.cancel()
