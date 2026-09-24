"""Tâche de création d'un serveur : étapes, progression, issue.

Une création dure de quelques secondes (Paper) à quelques minutes (NeoForge,
dont l'installeur télécharge Minecraft et ses bibliothèques) : elle ne peut pas
tenir dans une requête HTTP. Elle tourne en tâche de fond, et l'interface suit
son état — conservé en mémoire, le temps qu'on le consulte.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from msm.exceptions import MsmError
from msm.i18n import tr


class StepKey(str, Enum):
    FOLDER = "folder"
    DOWNLOAD = "download"
    INSTALL = "install"
    CONFIGURE = "configure"
    REGISTER = "register"
    START = "start"


#: Libellés anglais, traduits à l'affichage.
STEP_LABELS: dict[StepKey, str] = {
    StepKey.FOLDER: "Create the folder",
    StepKey.DOWNLOAD: "Download",
    StepKey.INSTALL: "Run the installer",
    StepKey.CONFIGURE: "Write the configuration",
    StepKey.REGISTER: "Register the server",
    StepKey.START: "Start the server",
}


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(slots=True)
class Step:
    key: StepKey
    status: StepStatus = StepStatus.PENDING
    detail: str = ""


@dataclass(slots=True)
class ProvisioningJob:
    """État d'une création, tel que l'interface le suit."""

    name: str
    directory: str
    distribution: str
    version: str
    build: str | None
    created_by: int
    steps: list[Step]
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    status: JobStatus = JobStatus.RUNNING
    #: Avancement du téléchargement, de 0 à 1 ; `None` si la taille est inconnue.
    progress: float | None = None
    downloaded_bytes: int = 0
    error: dict[str, str | None] | None = None
    server_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def step(self, key: StepKey) -> Step:
        return next(item for item in self.steps if item.key == key)

    def begin(self, key: StepKey, detail: str = "") -> None:
        step = self.step(key)
        step.status, step.detail = StepStatus.RUNNING, detail

    def finish(self, key: StepKey, detail: str = "") -> None:
        step = self.step(key)
        step.status = StepStatus.DONE
        if detail:
            step.detail = detail

    def skip(self, key: StepKey, detail: str = "") -> None:
        step = self.step(key)
        step.status, step.detail = StepStatus.SKIPPED, detail

    def fail(self, exc: BaseException) -> None:
        """Marque l'étape en cours comme échouée et retient une erreur lisible."""
        for step in self.steps:
            if step.status is StepStatus.RUNNING:
                step.status = StepStatus.FAILED
        if isinstance(exc, MsmError):
            self.error = {
                "message": exc.message,
                "cause": exc.cause,
                "remediation": exc.remediation,
            }
        else:
            self.error = {
                "message": tr("The server could not be created."),
                "cause": str(exc) or type(exc).__name__,
                "remediation": tr("Check the MSM logs, then try again."),
            }
        self.status = JobStatus.FAILED
        self.finished_at = datetime.now(UTC)

    def complete(self, server_id: int) -> None:
        self.server_id = server_id
        self.status = JobStatus.COMPLETED
        self.finished_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "directory": self.directory,
            "distribution": self.distribution,
            "version": self.version,
            "build": self.build,
            "status": self.status.value,
            "progress": self.progress,
            "downloaded_bytes": self.downloaded_bytes,
            "steps": [
                {
                    "key": step.key.value,
                    "label": tr(STEP_LABELS[step.key]),
                    "status": step.status.value,
                    "detail": step.detail,
                }
                for step in self.steps
            ],
            "error": self.error,
            "server_id": self.server_id,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class JobRegistry:
    """Tâches récentes, en mémoire : leur état n'a de sens que le temps de les suivre."""

    #: Au-delà, une tâche terminée n'intéresse plus personne.
    RETENTION = timedelta(hours=6)

    def __init__(self) -> None:
        self._jobs: dict[str, ProvisioningJob] = {}

    def add(self, job: ProvisioningJob) -> None:
        self._prune()
        self._jobs[job.id] = job

    def get(self, job_id: str) -> ProvisioningJob | None:
        return self._jobs.get(job_id)

    def running(self) -> list[ProvisioningJob]:
        return [job for job in self._jobs.values() if job.status is JobStatus.RUNNING]

    async def cancel_all(self) -> None:
        """À l'arrêt de MSM : une création interrompue nettoie derrière elle."""
        tasks = [job.task for job in self.running() if job.task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            # L'issue (annulée, échouée) est déjà consignée dans la tâche.
            with contextlib.suppress(BaseException):
                await task

    def _prune(self) -> None:
        limit = datetime.now(UTC) - self.RETENTION
        for job_id, job in list(self._jobs.items()):
            if job.finished_at is not None and job.finished_at < limit:
                del self._jobs[job_id]
