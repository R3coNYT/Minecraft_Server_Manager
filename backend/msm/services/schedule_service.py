"""Tâches programmées : définition, exécution, rattrapage.

Deux objets distincts vivent ici :

* :class:`ScheduleService`, les cas d'usage appelés par l'API (créer, modifier,
  supprimer, déclencher à la main) ;
* :class:`Scheduler`, la boucle de fond qui réveille les tâches dues.

Deux décisions structurent l'exécution.

**Une tâche agit au nom de son auteur, avec ses droits d'aujourd'hui.** Ils sont
réévalués à chaque déclenchement : un modérateur qui perd le droit de redémarrer
ne doit pas continuer à le faire par l'intermédiaire d'une tâche créée hier. La
tâche échoue alors avec un message explicite, elle n'est pas exécutée en douce.

**Une exécution manquée n'est rattrapée que si elle a encore un sens.** MSM
arrêté une nuit, la sauvegarde de 4 h est lancée au démarrage si le retard reste
modéré ; au-delà, elle est marquée manquée et l'on passe à la suivante. Rejouer
douze heures après ferait tourner une sauvegarde en pleine affluence.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings, get_settings
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.misc import EventDefinition
from msm.db.models.schedule import Schedule, ScheduleAction, ScheduleStatus
from msm.db.models.server import Server, ServerPermission
from msm.db.models.user import User
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.exceptions import MsmError, NotFoundError, PermissionDenied, ValidationError
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.schedule.rules import Rule, describe, next_occurrence, parse_rule
from msm.security.rbac import AccessContext, build_context

logger = get_logger(__name__)

#: Actions et permission exigée pour les programmer comme pour les exécuter.
REQUIRED_PERMISSION: dict[ScheduleAction, Permission] = {
    ScheduleAction.BACKUP: Permission.BACKUP_CREATE,
    ScheduleAction.RESTART: Permission.SERVER_RESTART,
    ScheduleAction.START: Permission.SERVER_START,
    ScheduleAction.STOP: Permission.SERVER_STOP,
    ScheduleAction.EVENT: Permission.EVENT_RUN,
    ScheduleAction.COMMAND: Permission.CONSOLE_WRITE,
}


def _payload_for(action: ScheduleAction, payload: dict[str, Any]) -> dict[str, Any]:
    """Valide les paramètres propres à l'action."""
    if action is ScheduleAction.EVENT:
        event_id = payload.get("event_id")
        if not isinstance(event_id, int):
            raise ValidationError(
                "Événement manquant.",
                cause="Une tâche « événement » doit désigner l'événement à déclencher.",
                remediation="Choisir un événement enregistré dans la liste.",
            )
        return {"event_id": event_id}

    if action is ScheduleAction.COMMAND:
        command = str(payload.get("command", "")).strip()
        if not command:
            raise ValidationError(
                "Commande manquante.",
                cause="Une tâche « commande » doit indiquer la commande à envoyer.",
                remediation="Saisir la commande, sans le `/` initial.",
            )
        return {"command": command}

    return {}


class ScheduleService:
    """Cas d'usage des tâches programmées d'un serveur."""

    def __init__(
        self,
        session: AsyncSession,
        supervisor: Supervisor,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._settings = settings or get_settings()
        self._audit = AuditRepository(session)

    async def list_schedules(self, server: Server) -> list[Schedule]:
        statement = select(Schedule).where(Schedule.server_id == server.id).order_by(Schedule.name)
        return list((await self._session.execute(statement)).scalars())

    async def get_schedule(self, server: Server, schedule_id: int) -> Schedule:
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is None or schedule.server_id != server.id:
            raise NotFoundError(
                "Tâche programmée introuvable.",
                cause=f"Aucune tâche n'a l'identifiant {schedule_id} sur ce serveur.",
                remediation="Rafraîchir la liste des tâches programmées.",
            )
        return schedule

    async def create(
        self,
        server: Server,
        *,
        name: str,
        action: ScheduleAction,
        rule: dict[str, Any],
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> Schedule:
        # Programmer une action, c'est la différer : le droit exigé est celui de
        # l'action elle-même. Qui peut redémarrer à la demande peut le programmer ;
        # qui ne le peut pas ne doit pas obtenir par une tâche ce qu'on lui refuse
        # par un bouton. La vérification a lieu dès la création plutôt que de
        # produire une tâche qui échouerait chaque nuit.
        context.require(
            REQUIRED_PERMISSION[action], action=f"programmer une action « {action.value} »"
        )

        clean_name = name.strip()
        if not clean_name:
            raise ValidationError(
                "Nom manquant.",
                cause="Le nom de la tâche ne peut pas être vide.",
                remediation="Donner un nom à cette tâche.",
            )

        parsed = parse_rule(rule)
        clean_payload = _payload_for(action, payload or {})
        await self._check_event(server, action, clean_payload)

        schedule = Schedule(
            server_id=server.id,
            name=clean_name,
            action=action,
            payload=clean_payload,
            rule=parsed.to_dict(),
            enabled=enabled,
            next_run_at=next_occurrence(parsed, datetime.now(UTC)) if enabled else None,
            created_by=context.user_id,
        )
        self._session.add(schedule)
        await self._session.flush()

        self._record(
            f"Tâche « {clean_name} » programmée sur « {server.name} » : {describe(parsed)}.",
            server,
            context,
            ip_address,
            payload={"schedule_id": schedule.id, "action": action.value},
        )
        return schedule

    async def update(
        self,
        server: Server,
        schedule: Schedule,
        *,
        name: str | None = None,
        rule: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        enabled: bool | None = None,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> Schedule:
        context.require(
            REQUIRED_PERMISSION[schedule.action], action="modifier cette tâche programmée"
        )

        if name is not None and name.strip():
            schedule.name = name.strip()
        if payload is not None:
            schedule.payload = _payload_for(schedule.action, payload)
            await self._check_event(server, schedule.action, schedule.payload)
        if rule is not None:
            schedule.rule = parse_rule(rule).to_dict()
        if enabled is not None:
            schedule.enabled = enabled

        # La prochaine occurrence est recalculée quoi qu'il arrive : réactiver une
        # tâche suspendue depuis un mois ne doit pas la déclencher immédiatement.
        schedule.next_run_at = (
            next_occurrence(parse_rule(schedule.rule), datetime.now(UTC))
            if schedule.enabled
            else None
        )

        self._record(
            f"Tâche « {schedule.name} » modifiée sur « {server.name} ».",
            server,
            context,
            ip_address,
            payload={"schedule_id": schedule.id},
        )
        return schedule

    async def delete(
        self,
        server: Server,
        schedule: Schedule,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> None:
        context.require(
            REQUIRED_PERMISSION[schedule.action], action="supprimer cette tâche programmée"
        )
        name = schedule.name
        await self._session.delete(schedule)
        self._record(
            f"Tâche « {name} » supprimée sur « {server.name} ».",
            server,
            context,
            ip_address,
        )

    async def run_now(
        self,
        server: Server,
        schedule: Schedule,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> Schedule:
        """Déclenche la tâche à la main, sans toucher à sa programmation."""
        context.require(REQUIRED_PERMISSION[schedule.action], action="déclencher cette tâche")
        self._record(
            f"Déclenchement manuel de la tâche « {schedule.name} ».",
            server,
            context,
            ip_address,
            payload={"schedule_id": schedule.id},
        )
        await self._session.commit()

        await run_schedule(
            schedule.id, supervisor=self._supervisor, settings=self._settings, manual=True
        )
        await self._session.refresh(schedule)
        return schedule

    async def _check_event(
        self, server: Server, action: ScheduleAction, payload: dict[str, Any]
    ) -> None:
        """Vérifie que l'événement visé existe — plutôt que d'échouer à 4 h du matin."""
        if action is not ScheduleAction.EVENT:
            return
        event = await self._session.get(EventDefinition, payload["event_id"])
        if event is None or event.server_id not in (None, server.id):
            raise NotFoundError(
                "Événement introuvable.",
                cause="L'événement à déclencher n'existe pas sur ce serveur.",
                remediation="Choisir un événement existant dans la liste.",
            )

    def _record(
        self,
        summary: str,
        server: Server,
        context: AccessContext,
        ip_address: str | None,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._audit.record(
            action=AuditAction.SCHEDULE_UPDATED,
            summary=summary,
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="schedule",
            payload=payload,
        )


# --------------------------------------------------------------------------- #
#  Exécution
# --------------------------------------------------------------------------- #
async def _context_for(session: AsyncSession, schedule: Schedule) -> AccessContext:
    """Droits **actuels** de l'auteur de la tâche, sur ce serveur.

    Recalculés à chaque exécution : une tâche n'est pas un moyen de conserver des
    droits perdus depuis.
    """
    if schedule.created_by is None:
        raise PermissionDenied(
            "Auteur de la tâche inconnu.",
            cause="Le compte qui a créé cette tâche a été supprimé.",
            remediation="Recréer la tâche depuis un compte existant.",
        )

    user = await session.get(User, schedule.created_by)
    if user is None or not user.is_active:
        raise PermissionDenied(
            "Auteur de la tâche indisponible.",
            cause="Le compte qui a créé cette tâche est supprimé ou désactivé.",
            remediation="Recréer la tâche depuis un compte actif.",
        )

    override = (
        await session.execute(
            select(ServerPermission).where(
                ServerPermission.user_id == user.id,
                ServerPermission.server_id == schedule.server_id,
            )
        )
    ).scalar_one_or_none()
    return build_context(user, server_id=schedule.server_id, override=override)


async def _perform(
    session: AsyncSession,
    schedule: Schedule,
    server: Server,
    context: AccessContext,
    supervisor: Supervisor,
    settings: Settings,
) -> str:
    """Exécute l'action et renvoie ce qui a été fait, en clair."""
    # Importés ici : ces services importent des modules qui importent celui-ci.
    from msm.services.backup_service import BackupService
    from msm.services.console_service import ConsoleService
    from msm.services.event_service import EventService
    from msm.services.lifecycle_service import LifecycleService

    runtime = supervisor.find(server.id)
    running = runtime is not None and runtime.state.is_running

    match schedule.action:
        case ScheduleAction.BACKUP:
            service = BackupService(session, supervisor, settings=settings)
            backup = await service.start_backup(server, context=context)
            return f"Sauvegarde lancée (#{backup.id})."

        case ScheduleAction.RESTART:
            if not running:
                raise _Skipped("Le serveur était déjà arrêté.")
            await LifecycleService(session, supervisor).restart(server, context=context)
            return "Serveur redémarré."

        case ScheduleAction.START:
            if running:
                raise _Skipped("Le serveur tournait déjà.")
            await LifecycleService(session, supervisor).start(server, context=context)
            return "Serveur démarré."

        case ScheduleAction.STOP:
            if not running:
                raise _Skipped("Le serveur était déjà arrêté.")
            await LifecycleService(session, supervisor).stop(server, context=context)
            return "Serveur arrêté."

        case ScheduleAction.EVENT:
            events = EventService(session, supervisor)
            event = await events.get_event(server, schedule.payload["event_id"])
            run = await events.start_run(server, event, context=context, confirm=True)
            return f"Événement « {event.name} » lancé (#{run.id})."

        case _:
            command = schedule.payload["command"]
            await ConsoleService(session, supervisor).send_command(
                server, command, context=context, confirm=True
            )
            return f"Commande envoyée : {command}"


class _Skipped(MsmError):
    """L'action n'avait pas lieu d'être à cet instant."""

    code = "SCHEDULE_SKIPPED"
    status_code = 409


async def run_schedule(
    schedule_id: int,
    *,
    supervisor: Supervisor,
    settings: Settings,
    manual: bool = False,
) -> ScheduleStatus:
    """Exécute une tâche et consigne son issue. Ne lève jamais."""
    async with session_scope() as session:
        schedule = await session.get(Schedule, schedule_id)
        if schedule is None:  # pragma: no cover - supprimée entre-temps
            return ScheduleStatus.SKIPPED

        server = await session.get(Server, schedule.server_id)
        if server is None:  # pragma: no cover - serveur retiré entre-temps
            return ScheduleStatus.SKIPPED

        status = ScheduleStatus.SUCCESS
        message: str | None = None
        audit = AuditRepository(session)
        actor = "planification"

        try:
            context = await _context_for(session, schedule)
            actor = f"planification ({context.username})"
            context.require(
                REQUIRED_PERMISSION[schedule.action], action="exécuter cette tâche programmée"
            )
            summary = await _perform(session, schedule, server, context, supervisor, settings)
        except _Skipped as exc:
            status, message, summary = ScheduleStatus.SKIPPED, exc.message, exc.message
        except MsmError as exc:
            status = ScheduleStatus.FAILED
            message = " ".join(part for part in (exc.message, exc.cause) if part)
            summary = f"Échec de la tâche « {schedule.name} » : {message}"
            logger.warning(
                "schedule_failed",
                schedule_id=schedule.id,
                server_id=server.id,
                error=message,
            )
        except Exception as exc:
            status = ScheduleStatus.FAILED
            message = str(exc)
            summary = f"Échec de la tâche « {schedule.name} » : {message}"
            logger.exception("schedule_crashed", schedule_id=schedule.id)

        schedule.last_run_at = datetime.now(UTC)
        schedule.last_status = status
        schedule.last_error = message if status is ScheduleStatus.FAILED else None
        if not manual and schedule.enabled:
            schedule.next_run_at = next_occurrence(
                parse_rule(schedule.rule),
                datetime.now(UTC),
                last_run=schedule.last_run_at,
            )

        audit.record(
            action=AuditAction.SCHEDULE_RUN,
            summary=summary,
            actor_id=schedule.created_by,
            actor_username=actor,
            actor_role="SYSTEM",
            ip_address=None,
            server_id=server.id,
            target_type="schedule",
            payload={"schedule_id": schedule.id, "status": status.value, "manual": manual},
        )
        if status is ScheduleStatus.SUCCESS:
            logger.info("schedule_ran", schedule_id=schedule.id, server_id=server.id, manual=manual)
        return status


class Scheduler:
    """Boucle de fond qui réveille les tâches dues."""

    def __init__(self, supervisor: Supervisor, settings: Settings | None = None) -> None:
        self._supervisor = supervisor
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not self._settings.scheduler_enabled:
            logger.info("scheduler_disabled")
            return
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="msm-scheduler")

    async def stop(self, *, timeout: float = 10.0) -> None:
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
        interval = self._settings.scheduler_tick_s
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            if self._stop.is_set():
                break
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler_tick_failed")

    async def tick(self, *, now: datetime | None = None) -> int:
        """Exécute les tâches dues. Renvoie le nombre de tâches traitées."""
        moment = now or datetime.now(UTC)
        grace = timedelta(minutes=self._settings.scheduler_grace_minutes)

        async with session_scope() as session:
            statement = select(Schedule).where(
                Schedule.enabled.is_(True),
                Schedule.next_run_at.is_not(None),
                Schedule.next_run_at <= moment,
            )
            due = list((await session.execute(statement)).scalars())
            missed = [item for item in due if moment - (item.next_run_at or moment) > grace]

            for schedule in missed:
                # Trop tard pour avoir encore du sens : on le dit, et l'on passe
                # à l'occurrence suivante plutôt que de rejouer la nuit en plein jour.
                schedule.last_status = ScheduleStatus.MISSED
                schedule.last_error = (
                    "Exécution manquée pendant un arrêt de MSM, trop tardive pour être rattrapée."
                )
                schedule.next_run_at = next_occurrence(parse_rule(schedule.rule), moment)
                logger.info(
                    "schedule_missed",
                    schedule_id=schedule.id,
                    was_due=(schedule.last_run_at or moment).isoformat(),
                )

            runnable = [item.id for item in due if item not in missed]

        for schedule_id in runnable:
            await run_schedule(schedule_id, supervisor=self._supervisor, settings=self._settings)
        return len(runnable)


def next_run_for(rule: dict[str, Any]) -> datetime:
    """Prochaine occurrence d'une règle brute — utilisé par l'API d'aperçu."""
    return next_occurrence(parse_rule(rule), datetime.now(UTC))


def rule_of(schedule: Schedule) -> Rule:
    return parse_rule(schedule.rule)
