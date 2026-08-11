"""Moteur d'exécution des événements.

Un événement est une suite d'étapes exécutées **dans l'ordre**, avec des pauses
possibles entre elles. Le moteur en assure trois choses :

* **la progression est publiée** sur le bus à chaque étape, pour que l'interface
  suive un événement de trente minutes sans interroger le serveur ;
* **l'exécution est annulable** à tout moment, y compris pendant une attente ;
* **une étape qui échoue arrête la suite**. Poursuivre après un échec produirait
  un demi-événement, plus déroutant qu'un arrêt net.

Le moteur ne connaît ni la base de données, ni HTTP : il reçoit des rappels pour
signaler sa progression. C'est ce qui permet de le tester sans rien démarrer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from msm.events import registry
from msm.events.actions import ActionResult, ExecutionContext
from msm.exceptions import MsmError, ValidationError
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Garde-fou : un événement démesuré est probablement une erreur de saisie.
MAX_STEPS = 100


class RunStatus(str, Enum):
    """Issue d'une exécution."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class Step:
    """Une étape validée, prête à être exécutée."""

    action: str
    params: dict[str, Any]

    def describe(self) -> str:
        return registry.get(self.action).describe(self.params)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "params": self.params}


@dataclass(slots=True)
class RunProgress:
    """État d'avancement transmis à l'interface."""

    status: RunStatus
    current_step: int
    total_steps: int
    summary: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "summary": self.summary,
            "error": self.error,
        }


ProgressCallback = Callable[[RunProgress], Awaitable[None] | None]


def parse_steps(raw_steps: Any) -> list[Step]:
    """Valide une suite d'étapes venant de l'interface ou de la base.

    La validation a lieu **avant l'enregistrement** d'un événement, pas à son
    exécution : découvrir qu'une étape est invalide au milieu d'un tournoi serait
    le pire moment.
    """
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValidationError(
            "Événement vide.",
            cause="Aucune étape n'a été définie.",
            remediation="Ajouter au moins une action à l'événement.",
        )

    if len(raw_steps) > MAX_STEPS:
        raise ValidationError(
            "Événement trop long.",
            cause=f"{len(raw_steps)} étapes pour un maximum de {MAX_STEPS}.",
            remediation="Découper l'événement en plusieurs séquences.",
        )

    steps: list[Step] = []
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(
                f"Étape {index} invalide.",
                cause="Chaque étape doit être un objet décrivant une action.",
                remediation="Reconstruire l'étape depuis l'interface.",
            )

        key = str(raw.get("action", ""))
        action = registry.get(key)
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise ValidationError(
                f"Étape {index} invalide.",
                cause="Les paramètres de l'étape ne forment pas un objet.",
                remediation="Reconstruire l'étape depuis l'interface.",
            )

        try:
            steps.append(Step(action=key, params=action.validate(params)))
        except MsmError as exc:
            # Le numéro d'étape est ajouté à la cause : sans lui, l'utilisateur
            # ne saurait pas laquelle corriger dans une séquence de dix.
            raise ValidationError(
                f"Étape {index} — {exc.message}",
                cause=exc.cause,
                remediation=exc.remediation,
            ) from exc

    return steps


def max_danger(steps: list[Step]) -> Any:
    """Niveau de risque le plus élevé de la séquence."""
    from msm.core.danger import DangerLevel

    return max(
        (registry.danger_of(step.action, step.params) for step in steps),
        default=DangerLevel.SAFE,
    )


@dataclass(slots=True)
class EventRunner:
    """Exécute une suite d'étapes, en rendant compte de sa progression."""

    steps: list[Step]
    context: ExecutionContext
    on_progress: ProgressCallback | None = None
    results: list[ActionResult] = field(default_factory=list)

    async def run(self) -> RunProgress:
        """Déroule les étapes. Ne lève pas : l'issue est dans le résultat.

        L'annulation est rattrapée autour de **toute** la boucle, pas seulement
        autour des actions : rendre compte de la progression demande d'écrire en
        base, et une annulation tombant pendant cette écriture laisserait sinon
        l'exécution éternellement « en cours » dans l'historique.
        """
        total = len(self.steps)
        index = 0

        try:
            await self._report(
                RunProgress(RunStatus.RUNNING, 0, total, "Démarrage de l'événement.")
            )

            for index, step in enumerate(self.steps, start=1):
                action = registry.get(step.action)
                try:
                    result = await action.execute(self.context, step.params)
                except MsmError as exc:
                    logger.warning(
                        "event_step_failed", step=index, action=step.action, error=str(exc)
                    )
                    progress = RunProgress(
                        RunStatus.FAILED,
                        index,
                        total,
                        f"Échec à l'étape {index} : {step.describe()}",
                        error=str(exc),
                    )
                    await self._report(progress)
                    return progress
                except Exception as exc:  # pragma: no cover - filet de sécurité
                    logger.exception("event_step_crashed", step=index, action=step.action)
                    progress = RunProgress(
                        RunStatus.FAILED,
                        index,
                        total,
                        f"Erreur inattendue à l'étape {index}.",
                        error=str(exc),
                    )
                    await self._report(progress)
                    return progress

                self.results.append(result)
                await self._report(RunProgress(RunStatus.RUNNING, index, total, result.summary))
        except asyncio.CancelledError:
            # Rendre compte avant de propager : la tâche est déjà condamnée, mais
            # l'historique doit dire à quelle étape elle s'est arrêtée.
            await asyncio.shield(
                self._report(RunProgress(RunStatus.CANCELLED, index, total, "Événement annulé."))
            )
            raise

        progress = RunProgress(RunStatus.COMPLETED, total, total, "Événement terminé.")
        await self._report(progress)
        return progress

    async def _report(self, progress: RunProgress) -> None:
        if self.on_progress is None:
            return
        outcome = self.on_progress(progress)
        if asyncio.iscoroutine(outcome):
            await outcome
