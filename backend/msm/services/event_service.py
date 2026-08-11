"""Événements : définitions enregistrées et exécutions.

Deux usages cohabitent, volontairement :

* l'**action immédiate** — un message, un titre, un don d'objets — déclenchée en
  un clic sans rien enregistrer ;
* l'**événement enregistré**, suite d'étapes réutilisable, exécutée en tâche de
  fond avec suivi de progression.

Une exécution longue vit dans une tâche asyncio : elle survit à la requête HTTP
qui l'a lancée, mais **pas à un redémarrage de MSM**. Les exécutions restées en
cours sont marquées interrompues au démarrage suivant, plutôt que de rester
éternellement « en cours » dans l'historique.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.bus import EventBus, get_event_bus, topics
from msm.core.danger import DangerLevel
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.misc import EventDefinition, EventRun, EventRunStatus
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.events import registry
from msm.events.actions import ExecutionContext
from msm.events.engine import EventRunner, RunProgress, RunStatus, Step, max_danger, parse_steps
from msm.exceptions import ConfirmationRequired, NotFoundError, ServerNotRunning, ValidationError
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext

logger = get_logger(__name__)

#: Exécutions en cours, par identifiant de run — permet l'annulation.
_ACTIVE_RUNS: dict[int, asyncio.Task[None]] = {}


class EventService:
    """Cas d'usage des événements d'un serveur."""

    def __init__(
        self,
        session: AsyncSession,
        supervisor: Supervisor,
        bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._bus = bus or get_event_bus()
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Définitions
    # ------------------------------------------------------------------ #
    async def list_events(self, server: Server) -> list[EventDefinition]:
        """Événements du serveur, et modèles globaux."""
        statement = (
            select(EventDefinition)
            .where((EventDefinition.server_id == server.id) | (EventDefinition.server_id.is_(None)))
            .order_by(EventDefinition.name)
        )
        return list((await self._session.execute(statement)).scalars())

    async def get_event(self, server: Server, event_id: int) -> EventDefinition:
        event = await self._session.get(EventDefinition, event_id)
        if event is None or (event.server_id not in (None, server.id)):
            raise NotFoundError(
                "Événement introuvable.",
                cause=f"Aucun événement n'a l'identifiant {event_id} sur ce serveur.",
                remediation="Rafraîchir la liste des événements.",
            )
        return event

    async def create_event(
        self,
        server: Server,
        *,
        name: str,
        steps: list[dict[str, Any]],
        description: str | None = None,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> EventDefinition:
        context.require(Permission.EVENT_EDIT, action="créer un événement")

        clean_name = name.strip()
        if not clean_name:
            raise ValidationError(
                "Nom d'événement manquant.",
                cause="Le nom ne peut pas être vide.",
                remediation="Donner un nom à cet événement.",
            )

        # Les étapes sont validées à l'enregistrement : découvrir une erreur en
        # plein déroulement serait le pire moment.
        parsed = parse_steps(steps)

        event = EventDefinition(
            server_id=server.id,
            name=clean_name,
            description=description,
            steps=[step.to_dict() for step in parsed],
            created_by=context.user_id,
        )
        self._session.add(event)
        await self._session.flush()

        self._record(
            AuditAction.EVENT_RUN,
            f"Création de l'événement « {clean_name} » sur « {server.name} ».",
            server,
            context,
            ip_address,
            payload={"event": clean_name, "steps": len(parsed)},
        )
        return event

    async def update_event(
        self,
        server: Server,
        event: EventDefinition,
        *,
        name: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        description: str | None = None,
        context: AccessContext,
    ) -> EventDefinition:
        context.require(Permission.EVENT_EDIT, action="modifier un événement")

        if name is not None and name.strip():
            event.name = name.strip()
        if description is not None:
            event.description = description
        if steps is not None:
            event.steps = [step.to_dict() for step in parse_steps(steps)]

        await self._session.flush()
        return event

    async def delete_event(
        self, server: Server, event: EventDefinition, *, context: AccessContext
    ) -> None:
        context.require(Permission.EVENT_EDIT, action="supprimer un événement")
        await self._session.delete(event)

    # ------------------------------------------------------------------ #
    #  Exécution
    # ------------------------------------------------------------------ #
    def _authorize(
        self,
        steps: list[Step],
        *,
        context: AccessContext,
        confirm: bool,
        single: bool = False,
    ) -> DangerLevel:
        """Contrôle les droits et la confirmation exigés par la séquence.

        `single` distingue l'action immédiate de la séquence enregistrée : la
        confirmation étant lue avant de cliquer, elle doit décrire ce que
        l'utilisateur s'apprête réellement à faire.
        """
        context.require(Permission.EVENT_RUN, action="déclencher un événement")
        danger = max_danger(steps)

        if danger is DangerLevel.SAFE:
            return danger

        context.require(
            Permission.EVENT_RUN_DESTRUCTIVE, action="déclencher une action destructrice"
        )
        if not confirm:
            destructive = [
                step.describe()
                for step in steps
                if registry.danger_of(step.action, step.params) is not DangerLevel.SAFE
            ]
            preamble = (
                "Cette action est irréversible : "
                if single
                else "Cet événement contient des actions irréversibles : "
            )
            raise ConfirmationRequired(
                "Confirmation requise.",
                cause=preamble + " ; ".join(destructive) + ".",
                remediation="Renvoyer la requête avec `confirm: true` pour confirmer.",
                context={"danger": danger.name, "actions": destructive},
            )
        return danger

    def _runtime_or_fail(self, server: Server) -> Any:
        runtime = self._supervisor.find(server.id)
        if runtime is None or not runtime.state.is_running:
            raise ServerNotRunning(
                "Le serveur n'est pas démarré.",
                cause="Un événement passe par la console du serveur.",
                remediation="Démarrer le serveur avant de déclencher un événement.",
            )
        return runtime

    async def run_quick(
        self,
        server: Server,
        action_key: str,
        params: dict[str, Any],
        *,
        context: AccessContext,
        confirm: bool = False,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Exécute une action unique, sans rien enregistrer.

        Le cas courant : envoyer un message ou distribuer un objet. Passer par le
        moteur complet et l'historique serait disproportionné.
        """
        steps = parse_steps([{"action": action_key, "params": params}])
        self._authorize(steps, context=context, confirm=confirm, single=True)

        runtime = self._runtime_or_fail(server)
        step = steps[0]

        execution = ExecutionContext(
            server_name=server.name,
            actor=context.username,
            send=lambda command: runtime.send_command(command, actor=context.username),
        )
        result = await registry.get(step.action).execute(execution, step.params)

        self._record(
            AuditAction.EVENT_RUN,
            f"Action « {result.summary} » sur « {server.name} ».",
            server,
            context,
            ip_address,
            payload={"action": step.action, "commands": list(result.commands)},
        )
        logger.info(
            "event_quick_action",
            server_id=server.id,
            action=step.action,
            actor=context.username,
        )
        return result.to_dict()

    async def start_run(
        self,
        server: Server,
        event: EventDefinition,
        *,
        context: AccessContext,
        confirm: bool = False,
        ip_address: str | None = None,
    ) -> EventRun:
        """Lance un événement enregistré en tâche de fond."""
        steps = parse_steps(event.steps)
        self._authorize(steps, context=context, confirm=confirm)
        self._runtime_or_fail(server)

        run = EventRun(
            event_id=event.id,
            server_id=server.id,
            started_by=context.user_id,
            status=EventRunStatus.RUNNING,
            current_step=0,
            total_steps=len(steps),
            started_at=datetime.now(UTC),
            log=[],
        )
        self._session.add(run)
        await self._session.flush()

        self._record(
            AuditAction.EVENT_RUN,
            f"Lancement de l'événement « {event.name} » sur « {server.name} ».",
            server,
            context,
            ip_address,
            payload={"event": event.name, "steps": len(steps), "run_id": run.id},
        )

        # La transaction est validée avant de lancer la tâche : celle-ci écrira
        # dans ses propres sessions, et doit pouvoir relire cette ligne.
        await self._session.commit()

        task = asyncio.create_task(
            _execute_run(
                run_id=run.id,
                server_id=server.id,
                server_name=server.name,
                steps=steps,
                actor=context.username,
                supervisor=self._supervisor,
                bus=self._bus,
            ),
            name=f"msm-event-run-{run.id}",
        )
        _ACTIVE_RUNS[run.id] = task
        task.add_done_callback(lambda _: _ACTIVE_RUNS.pop(run.id, None))

        return run

    async def cancel_run(self, server: Server, run_id: int, *, context: AccessContext) -> bool:
        """Interrompt une exécution en cours, y compris pendant une attente."""
        context.require(Permission.EVENT_RUN, action="annuler un événement")

        run = await self._session.get(EventRun, run_id)
        if run is None or run.server_id != server.id:
            raise NotFoundError(
                "Exécution introuvable.",
                cause=f"Aucune exécution n'a l'identifiant {run_id} sur ce serveur.",
                remediation="Rafraîchir l'historique des événements.",
            )

        task = _ACTIVE_RUNS.get(run_id)
        if task is None or task.done():
            return False

        task.cancel()
        logger.info("event_run_cancelled", run_id=run_id, actor=context.username)
        return True

    async def list_runs(self, server: Server, *, limit: int = 20) -> list[EventRun]:
        statement = (
            select(EventRun)
            .where(EventRun.server_id == server.id)
            .order_by(EventRun.started_at.desc().nullslast(), EventRun.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())

    async def mark_interrupted_runs(self) -> int:
        """Clôt les exécutions restées « en cours » après un arrêt de MSM.

        Une tâche asyncio ne survit pas au processus : laisser ces lignes en
        RUNNING ferait croire indéfiniment à un événement en train de se dérouler.
        """
        statement = select(EventRun).where(EventRun.status == EventRunStatus.RUNNING)
        interrupted = list((await self._session.execute(statement)).scalars())
        for run in interrupted:
            run.status = EventRunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.error = "Exécution interrompue par un redémarrage de MSM."
        if interrupted:
            logger.info("event_runs_marked_interrupted", count=len(interrupted))
        return len(interrupted)

    # ------------------------------------------------------------------ #
    def _record(
        self,
        action: AuditAction,
        summary: str,
        server: Server,
        context: AccessContext,
        ip_address: str | None,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._audit.record(
            action=action,
            summary=summary,
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="event",
            payload=payload,
        )


# --------------------------------------------------------------------------- #
#  Exécution en tâche de fond
# --------------------------------------------------------------------------- #
async def _execute_run(
    *,
    run_id: int,
    server_id: int,
    server_name: str,
    steps: list[Step],
    actor: str,
    supervisor: Supervisor,
    bus: EventBus,
) -> None:
    """Déroule un événement et tient l'historique à jour.

    Fonction libre plutôt que méthode : la tâche survit à la requête HTTP, donc à
    la session de base qui l'a créée. Elle ouvre les siennes, au fil de sa
    progression.
    """
    topic = topics.server_topic(server_id, "event_run")

    async def report(progress: RunProgress) -> None:
        bus.publish(topic, {"run_id": run_id, "server_id": server_id, **progress.to_dict()})
        try:
            async with session_scope() as session:
                run = await session.get(EventRun, run_id)
                if run is None:  # pragma: no cover - supprimé entre-temps
                    return
                run.current_step = progress.current_step
                run.log = [*(run.log or []), progress.to_dict()]
                if progress.status is not RunStatus.RUNNING:
                    run.status = EventRunStatus(progress.status.value)
                    run.finished_at = datetime.now(UTC)
                    run.error = progress.error
        except Exception as exc:
            logger.warning("event_progress_persist_failed", run_id=run_id, error=str(exc))

    runtime = supervisor.find(server_id)
    if runtime is None:  # pragma: no cover - serveur retiré entre-temps
        await report(RunProgress(RunStatus.FAILED, 0, len(steps), "Serveur introuvable."))
        return

    context = ExecutionContext(
        server_name=server_name,
        actor=actor,
        send=lambda command: runtime.send_command(command, actor=f"événement ({actor})"),
    )

    try:
        await EventRunner(steps=steps, context=context, on_progress=report).run()
    except asyncio.CancelledError:
        # `report` a déjà consigné l'annulation ; on la laisse se propager pour
        # que la tâche se termine réellement.
        raise
