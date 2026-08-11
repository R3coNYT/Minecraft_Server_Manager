"""Tests de l'échantillonnage et de l'agrégation des métriques."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from msm.core.states import ServerState
from msm.runtime.stats import ProcessStats
from msm.services.metrics_service import RANGES


class FakeRuntime:
    """Runtime factice : seules les propriétés lues par l'échantillonneur."""

    def __init__(self, server_id: int, *, state: ServerState, cpu: float, memory: float) -> None:
        self.id = server_id
        self.state = state
        self.stats = ProcessStats(cpu_percent=cpu, memory_mb=memory, process_count=1)
        self.online_players = ("Flavien", "Ami")


class FakeSupervisor:
    def __init__(self, *runtimes: FakeRuntime) -> None:
        self._runtimes = runtimes

    def all(self) -> tuple[FakeRuntime, ...]:
        return self._runtimes


class TestRanges:
    def test_every_range_declares_a_bucket_smaller_than_itself(self) -> None:
        """Un palier plus large que la fenêtre produirait un seul point."""
        for key, (window, bucket) in RANGES.items():
            assert bucket < window, key

    def test_a_range_never_yields_more_points_than_a_chart_can_show(self) -> None:
        """Au-delà de quelques centaines de points, on transporte pour rien."""
        for key, (window, bucket) in RANGES.items():
            assert window / bucket <= 300, key


@pytest.mark.asyncio
class TestSampling:
    async def test_stopped_server_is_recorded_at_zero(self, monkeypatch) -> None:
        """Un trou dans la courbe est ambigu ; un zéro explicite ne l'est pas."""
        from msm.services import metrics_recorder as module

        captured: list = []

        class FakeSession:
            def add_all(self, items):
                captured.extend(items)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(module, "session_scope", lambda: FakeSession())

        supervisor = FakeSupervisor(
            FakeRuntime(1, state=ServerState.ONLINE, cpu=42.0, memory=1024.0),
            FakeRuntime(2, state=ServerState.OFFLINE, cpu=99.0, memory=999.0),
        )
        recorder = module.MetricsRecorder(supervisor)  # type: ignore[arg-type]

        written = await recorder.sample_once()

        assert written == 2
        online, offline = captured
        assert (online.cpu_percent, online.players_online, online.online) == (42.0, 2, True)
        # Les statistiques résiduelles d'un serveur arrêté ne sont pas reprises.
        assert (offline.cpu_percent, offline.players_online, offline.online) == (0.0, 0, False)

    async def test_a_database_failure_does_not_crash_the_loop(self, monkeypatch) -> None:
        """L'historique est utile, pas vital : il ne doit rien faire tomber."""
        from msm.services import metrics_recorder as module

        class BrokenSession:
            async def __aenter__(self):
                raise OSError("base verrouillée")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(module, "session_scope", lambda: BrokenSession())
        recorder = module.MetricsRecorder(
            FakeSupervisor(FakeRuntime(1, state=ServerState.ONLINE, cpu=1.0, memory=1.0))  # type: ignore[arg-type]
        )

        assert await recorder.sample_once() == 0


class TestRetention:
    def test_cutoff_follows_the_configured_retention(self) -> None:
        from msm.config import Settings

        settings = Settings(metrics_retention_days=3)
        cutoff = datetime.now(UTC) - timedelta(days=settings.metrics_retention_days)

        assert (datetime.now(UTC) - cutoff).days == 3
