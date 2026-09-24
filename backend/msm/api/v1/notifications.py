"""Notifications Discord : le salon global, et celui de chaque serveur."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from msm.api.deps import ClientIp, CsrfProtected, DbSession, GlobalContext, ServerAccess
from msm.api.schemas import NotificationSettingsOut, NotificationSettingsRequest
from msm.i18n import tr
from msm.services.notification_service import NotificationService
from msm.services.notifier import send_to_discord

router = APIRouter(tags=["notifications"])


def _service(session: DbSession) -> NotificationService:
    return NotificationService(session)


NotificationsDep = Annotated[NotificationService, Depends(_service)]


# --------------------------------------------------------------------------- #
#  Salon global — création et suppression de serveurs
# --------------------------------------------------------------------------- #
@router.get(
    "/notifications",
    response_model=NotificationSettingsOut,
    summary="Global notification settings",
)
async def get_global(context: GlobalContext, service: NotificationsDep) -> NotificationSettingsOut:
    return NotificationSettingsOut.model_validate(await service.global_settings(context=context))


@router.put(
    "/notifications",
    response_model=NotificationSettingsOut,
    summary="Update global notifications",
    dependencies=[CsrfProtected],
)
async def update_global(
    payload: NotificationSettingsRequest,
    context: GlobalContext,
    service: NotificationsDep,
    ip: ClientIp,
) -> NotificationSettingsOut:
    """L'adresse du webhook est chiffrée en base et n'est jamais renvoyée."""
    result = await service.update_global(
        enabled=payload.enabled,
        events=payload.events,
        webhook_url=payload.webhook_url,
        clear_webhook=payload.clear_webhook,
        context=context,
        ip_address=ip,
    )
    return NotificationSettingsOut.model_validate(result)


@router.post(
    "/notifications/test",
    summary="Send a test message to the global channel",
    dependencies=[CsrfProtected],
)
async def test_global(context: GlobalContext, service: NotificationsDep) -> dict[str, bool]:
    """Vérifie la configuration en publiant réellement dans le salon."""
    url = await service.global_webhook(context=context)
    content = tr("✅ **Minecraft Server Manager** · test message — notifications will arrive here.")
    return {"sent": await send_to_discord(url, content)}


# --------------------------------------------------------------------------- #
#  Salon d'un serveur
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/notifications",
    response_model=NotificationSettingsOut,
    summary="Server notification settings",
)
async def get_server(access: ServerAccess, service: NotificationsDep) -> NotificationSettingsOut:
    server, context = access
    return NotificationSettingsOut.model_validate(
        await service.server_settings(server, context=context)
    )


@router.put(
    "/servers/{server_id}/notifications",
    response_model=NotificationSettingsOut,
    summary="Update server notifications",
    dependencies=[CsrfProtected],
)
async def update_server(
    payload: NotificationSettingsRequest,
    access: ServerAccess,
    service: NotificationsDep,
    ip: ClientIp,
) -> NotificationSettingsOut:
    server, context = access
    result = await service.update_server(
        server,
        enabled=payload.enabled,
        events=payload.events,
        webhook_url=payload.webhook_url,
        clear_webhook=payload.clear_webhook,
        context=context,
        ip_address=ip,
    )
    return NotificationSettingsOut.model_validate(result)


@router.post(
    "/servers/{server_id}/notifications/test",
    summary="Send a test message to the server's channel",
    dependencies=[CsrfProtected],
)
async def test_server(access: ServerAccess, service: NotificationsDep) -> dict[str, bool]:
    server, context = access
    url = await service.server_webhook(server, context=context)
    content = tr(
        "✅ **{server}** · test message — this server's notifications will arrive here.",
        server=server.name,
    )
    return {"sent": await send_to_discord(url, content)}
