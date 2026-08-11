"""Échantillonnage périodique des ressources de chaque serveur.

Le runtime publie ses statistiques toutes les deux secondes : les enregistrer
toutes ferait 43 200 lignes par jour et par serveur pour une information qui ne
se lit qu'en tendance. L'échantillonneur interroge donc les runtimes à son
propre rythme (30 s par défaut), indépendant de celui du bus.

Il **purge aussi** : un historique qui ne s'efface jamais finit par occuper plus
de place que ce qu'il surveille.

Un serveur arrêté est enregistré lui aussi, à zéro. Un trou dans la courbe est
ambigu — panne du serveur, panne de MSM, machine éteinte ? — alors qu'un zéro
explicite se lit sans hésitation.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from msm.config import Settings, get_settings
from msm.db.models.metrics import MetricSample
from msm.db.session import session_scope
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor

logger = get_logger(__name__)

#: La purge est bien plus coûteuse que l'écriture : une fois par heure suffit.
PURGE_INTERVAL_S = 3600.0


class MetricsRecorder:
    """Boucle d'échantillonnage des ressources, arrêtable proprement."""

    def __init__(self, supervisor: Supervisor, settings: Settings | None = None) -> None:
        self._supervisor = supervisor
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not self._settings.metrics_enabled:
            logger.info("metrics_recorder_disabled")
            return
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="msm-metrics-recorder")

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Arrête la boucle en laissant l'écriture en cours s'achever."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def _run(self) -> None:
        interval = self._settings.metrics_interval_s
        since_purge = 0.0
        while not self._stop.is_set():
            # `wait_for` sur l'événement plutôt que `sleep` : l'arrêt est
            # immédiat au lieu d'attendre la fin de l'intervalle.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            if self._stop.is_set():
                break

            await self.sample_once()
            since_purge += interval
            if since_purge >= PURGE_INTERVAL_S:
                since_purge = 0.0
                await self.purge()

    async def sample_once(self) -> int:
        """Enregistre un point par serveur connu. Renvoie le nombre de points."""
        moment = datetime.now(UTC)
        samples: list[MetricSample] = []

        for runtime in self._supervisor.all():
            stats = runtime.stats
            running = runtime.state.is_running
            samples.append(
                MetricSample(
                    server_id=runtime.id,
                    ts=moment,
                    cpu_percent=float(stats.cpu_percent or 0.0) if running else 0.0,
                    memory_mb=float(stats.memory_mb or 0.0) if running else 0.0,
                    players_online=len(runtime.online_players) if running else 0,
                    online=running,
                )
            )

        if not samples:
            return 0

        try:
            async with session_scope() as session:
                session.add_all(samples)
        except Exception as exc:
            logger.warning("metrics_sample_failed", error=str(exc))
            return 0
        return len(samples)

    async def purge(self) -> int:
        """Supprime les points plus anciens que la rétention configurée."""
        cutoff = datetime.now(UTC) - timedelta(days=self._settings.metrics_retention_days)
        try:
            async with session_scope() as session:
                result = await session.execute(delete(MetricSample).where(MetricSample.ts < cutoff))
        except Exception as exc:
            logger.warning("metrics_purge_failed", error=str(exc))
            return 0

        removed = result.rowcount or 0
        if removed:
            logger.info("metrics_purged", removed=removed, older_than=cutoff.isoformat())
        return removed
