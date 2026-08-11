"""Événements : catalogue d'actions, actions immédiates et séquences."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from msm.api.deps import (
    ClientIp,
    CsrfProtected,
    DbSession,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas import (
    ActionOut,
    EventCreateRequest,
    EventOut,
    EventRunOut,
    EventUpdateRequest,
    QuickActionOut,
    QuickActionRequest,
    RunRequest,
    StepOut,
)
from msm.core.permissions import Permission
from msm.db.models.misc import EventDefinition
from msm.events import registry
from msm.events.engine import max_danger, parse_steps
from msm.services.event_service import EventService

router = APIRouter(tags=["événements"])


def _events(session: DbSession, supervisor: SupervisorDep) -> EventService:
    return EventService(session, supervisor)


EventsDep = Annotated[EventService, Depends(_events)]


def _to_out(event: EventDefinition) -> EventOut:
    """Enrichit une définition de son résumé et de son niveau de risque.

    Les deux sont calculés côté serveur : le frontend afficherait sinon une
    description qui pourrait diverger de celle consignée dans l'audit.
    """
    steps = parse_steps(event.steps)
    return EventOut(
        id=event.id,
        name=event.name,
        description=event.description,
        server_id=event.server_id,
        steps=[
            StepOut(action=step.action, params=step.params, summary=step.describe())
            for step in steps
        ],
        danger=max_danger(steps).name,
    )


# --------------------------------------------------------------------------- #
#  Catalogue
# --------------------------------------------------------------------------- #
@router.get(
    "/events/actions",
    response_model=list[ActionOut],
    summary="Types d'actions disponibles",
)
async def list_actions() -> list[ActionOut]:
    """Catalogue des actions, avec la description de leurs champs.

    Le frontend construit ses formulaires à partir de cette réponse : ajouter une
    action côté serveur la rend disponible sans toucher à l'interface.
    """
    return [ActionOut(**action) for action in registry.describe_all()]


# --------------------------------------------------------------------------- #
#  Actions immédiates
# --------------------------------------------------------------------------- #
@router.post(
    "/servers/{server_id}/events/quick",
    response_model=QuickActionOut,
    summary="Déclencher une action immédiate",
    dependencies=[CsrfProtected],
)
async def quick_action(
    payload: QuickActionRequest,
    access: ServerAccess,
    service: EventsDep,
    ip: ClientIp,
) -> QuickActionOut:
    """Exécute une action unique sans l'enregistrer.

    Une action destructrice — `kill @a` par exemple — exige la permission dédiée
    **et** `confirm: true` ; sinon la réponse est un 428 décrivant ce qu'elle
    ferait.
    """
    server, context = access
    result = await service.run_quick(
        server,
        payload.action,
        payload.params,
        context=context,
        confirm=payload.confirm,
        ip_address=ip,
    )
    return QuickActionOut(**result)


# --------------------------------------------------------------------------- #
#  Événements enregistrés
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/events",
    response_model=list[EventOut],
    summary="Lister les événements",
)
async def list_events(access: ServerAccess, service: EventsDep) -> list[EventOut]:
    server, context = access
    context.require(Permission.EVENT_RUN, action="consulter les événements")
    return [_to_out(event) for event in await service.list_events(server)]


@router.post(
    "/servers/{server_id}/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un événement",
    dependencies=[CsrfProtected],
)
async def create_event(
    payload: EventCreateRequest,
    access: ServerAccess,
    service: EventsDep,
    ip: ClientIp,
) -> EventOut:
    server, context = access
    event = await service.create_event(
        server,
        name=payload.name,
        steps=[step.model_dump() for step in payload.steps],
        description=payload.description,
        context=context,
        ip_address=ip,
    )
    return _to_out(event)


@router.put(
    "/servers/{server_id}/events/{event_id}",
    response_model=EventOut,
    summary="Modifier un événement",
    dependencies=[CsrfProtected],
)
async def update_event(
    event_id: int,
    payload: EventUpdateRequest,
    access: ServerAccess,
    service: EventsDep,
) -> EventOut:
    server, context = access
    event = await service.get_event(server, event_id)
    updated = await service.update_event(
        server,
        event,
        name=payload.name,
        steps=[step.model_dump() for step in payload.steps] if payload.steps else None,
        description=payload.description,
        context=context,
    )
    return _to_out(updated)


@router.delete(
    "/servers/{server_id}/events/{event_id}",
    summary="Supprimer un événement",
    dependencies=[CsrfProtected],
)
async def delete_event(event_id: int, access: ServerAccess, service: EventsDep) -> dict[str, str]:
    server, context = access
    event = await service.get_event(server, event_id)
    name = event.name
    await service.delete_event(server, event, context=context)
    return {"status": "supprimé", "name": name}


# --------------------------------------------------------------------------- #
#  Exécutions
# --------------------------------------------------------------------------- #
@router.post(
    "/servers/{server_id}/events/{event_id}/run",
    response_model=EventRunOut,
    summary="Déclencher un événement",
    dependencies=[CsrfProtected],
)
async def run_event(
    event_id: int,
    payload: RunRequest,
    access: ServerAccess,
    service: EventsDep,
    ip: ClientIp,
) -> EventRunOut:
    """Lance la séquence en tâche de fond.

    La réponse arrive immédiatement : la progression est ensuite poussée par
    WebSocket, ce qui permet de suivre un événement de trente minutes sans
    maintenir la requête ouverte.
    """
    server, context = access
    event = await service.get_event(server, event_id)
    run = await service.start_run(
        server, event, context=context, confirm=payload.confirm, ip_address=ip
    )
    return EventRunOut(
        id=run.id,
        event_id=run.event_id,
        status=run.status.value,
        current_step=run.current_step,
        total_steps=run.total_steps,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


@router.get(
    "/servers/{server_id}/events/runs",
    response_model=list[EventRunOut],
    summary="Historique des exécutions",
)
async def list_runs(
    access: ServerAccess,
    service: EventsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[EventRunOut]:
    server, context = access
    context.require(Permission.EVENT_RUN, action="consulter l'historique des événements")
    return [
        EventRunOut(
            id=run.id,
            event_id=run.event_id,
            status=run.status.value,
            current_step=run.current_step,
            total_steps=run.total_steps,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
        )
        for run in await service.list_runs(server, limit=limit)
    ]


@router.post(
    "/servers/{server_id}/events/runs/{run_id}/cancel",
    summary="Annuler une exécution",
    dependencies=[CsrfProtected],
)
async def cancel_run(run_id: int, access: ServerAccess, service: EventsDep) -> dict[str, bool]:
    """Interrompt une séquence en cours, y compris pendant une attente."""
    server, context = access
    return {"cancelled": await service.cancel_run(server, run_id, context=context)}
