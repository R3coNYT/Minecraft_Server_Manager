"""Réglages des notifications Discord : un salon global, un salon par serveur.

* **Global** — dans `app_settings`, réservé aux administrateurs du panneau :
  les événements de MSM lui-même (création, suppression de serveurs).
* **Par serveur** — dans `server_notifications`, modifiable par qui peut
  modifier le serveur : ce qui arrive à ce serveur (plantage, démarrage,
  sauvegarde…).

L'URL d'un webhook Discord est un **secret** : elle autorise à publier dans le
salon. Elle est donc chiffrée en base, et l'API n'en renvoie jamais que la
présence et les derniers caractères — de quoi la reconnaître, pas de quoi s'en
servir.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.misc import AppSetting
from msm.db.models.notification import ServerNotification
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.exceptions import ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.security.crypto import decrypt_secret, encrypt_secret
from msm.security.rbac import AccessContext
from msm.services.notifier import (
    DEFAULT_EVENTS,
    GLOBAL_EVENTS,
    LABELS,
    SERVER_EVENTS,
    NotificationEvent,
)

logger = get_logger(__name__)

#: Clé du réglage global dans `app_settings`.
GLOBAL_KEY = "notifications.discord"

#: Un webhook Discord commence toujours ainsi ; refuser le reste évite d'envoyer
#: par mégarde l'état des serveurs à un hôte quelconque.
_ALLOWED_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
)


def _masked(url: str) -> str:
    """Fin de l'URL, assez pour la reconnaître sans pouvoir la rejouer."""
    return f"…{url[-6:]}" if len(url) > 6 else "…"


def _describe(stored: dict[str, Any], available: tuple[NotificationEvent, ...]) -> dict[str, Any]:
    """Réglages tels que renvoyés à l'interface, sans le secret."""
    encrypted = stored.get("webhook_url")
    url = decrypt_secret(encrypted) if isinstance(encrypted, str) else None
    known = {event.value for event in available}
    return {
        "enabled": bool(stored.get("enabled", False)),
        "events": [value for value in stored.get("events", []) if value in known],
        "available_events": [
            {"key": event.value, "label": tr(LABELS[event])} for event in available
        ],
        "webhook_configured": url is not None,
        "webhook_hint": _masked(url) if url else None,
        # Un secret illisible se signale : la clé applicative a changé, et il
        # faut ressaisir l'URL plutôt que de croire les notifications actives.
        "webhook_unreadable": encrypted is not None and url is None,
    }


def _apply(
    stored: dict[str, Any],
    available: tuple[NotificationEvent, ...],
    *,
    enabled: bool | None,
    events: list[str] | None,
    webhook_url: str | None,
    clear_webhook: bool,
) -> dict[str, Any]:
    """Applique une modification, identique pour les deux portées."""
    result = dict(stored)

    if clear_webhook:
        result.pop("webhook_url", None)
        result["enabled"] = False
    elif webhook_url is not None:
        clean = webhook_url.strip()
        if not clean.startswith(_ALLOWED_PREFIXES):
            raise ValidationError(
                tr("Invalid webhook address."),
                cause=tr("The address does not look like a Discord webhook."),
                remediation=tr(
                    "Copy the address from Discord: Channel settings → Integrations → Webhooks."
                ),
            )
        result["webhook_url"] = encrypt_secret(clean)

    if events is not None:
        known = {event.value for event in available}
        unknown = [value for value in events if value not in known]
        if unknown:
            raise ValidationError(
                tr("Unknown event."),
                cause=tr("“{event}” is not a notifiable event.", event=unknown[0]),
                remediation=tr("Tick the events offered by the interface."),
            )
        result["events"] = list(dict.fromkeys(events))

    if enabled is not None:
        if enabled and not result.get("webhook_url"):
            raise ValidationError(
                tr("No webhook configured."),
                cause=tr("Notifications cannot be enabled without an address."),
                remediation=tr("Enter the Discord webhook address first."),
            )
        result["enabled"] = enabled

    return result


def _plain_url(stored: dict[str, Any]) -> str:
    """URL en clair pour un envoi de test, ou une erreur explicite."""
    encrypted = stored.get("webhook_url")
    url = decrypt_secret(encrypted) if isinstance(encrypted, str) else None
    if not url:
        raise ValidationError(
            tr("No usable webhook."),
            cause=tr("No address saved, or the secret became unreadable after a key change."),
            remediation=tr("Enter the Discord webhook address."),
        )
    return url


def _row_to_dict(row: ServerNotification | None) -> dict[str, Any]:
    if row is None:
        # Jamais configuré : les événements par défaut sont proposés cochés.
        return {"enabled": False, "events": [event.value for event in DEFAULT_EVENTS]}
    stored: dict[str, Any] = {"enabled": row.enabled, "events": list(row.events or [])}
    if row.webhook_url_enc:
        stored["webhook_url"] = row.webhook_url_enc
    return stored


class NotificationService:
    """Lecture et écriture des réglages de notification."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Salon global
    # ------------------------------------------------------------------ #
    async def _global_raw(self) -> dict[str, Any]:
        row = await self._session.get(AppSetting, GLOBAL_KEY)
        value = row.value if row is not None else None
        if not isinstance(value, dict):
            return {"enabled": False, "events": [event.value for event in GLOBAL_EVENTS]}
        return value

    async def global_settings(self, *, context: AccessContext) -> dict[str, Any]:
        context.require(Permission.SETTINGS_MANAGE, action=tr("view the settings"))
        return _describe(await self._global_raw(), GLOBAL_EVENTS)

    async def update_global(
        self,
        *,
        enabled: bool | None = None,
        events: list[str] | None = None,
        webhook_url: str | None = None,
        clear_webhook: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        context.require(Permission.SETTINGS_MANAGE, action=tr("change the settings"))
        stored = _apply(
            await self._global_raw(),
            GLOBAL_EVENTS,
            enabled=enabled,
            events=events,
            webhook_url=webhook_url,
            clear_webhook=clear_webhook,
        )
        row = await self._session.get(AppSetting, GLOBAL_KEY)
        if row is None:
            self._session.add(AppSetting(key=GLOBAL_KEY, value=stored))
        else:
            row.value = stored

        self._audit.record(
            action=AuditAction.SETTINGS_UPDATED,
            summary=tr("Discord notification settings changed."),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="settings",
            # Jamais l'URL : le journal d'audit est consultable par d'autres.
            payload={"enabled": stored.get("enabled"), "events": stored.get("events")},
        )
        return _describe(stored, GLOBAL_EVENTS)

    async def global_webhook(self, *, context: AccessContext) -> str:
        context.require(Permission.SETTINGS_MANAGE, action=tr("test notifications"))
        return _plain_url(await self._global_raw())

    # ------------------------------------------------------------------ #
    #  Salon d'un serveur
    # ------------------------------------------------------------------ #
    async def server_settings(self, server: Server, *, context: AccessContext) -> dict[str, Any]:
        context.require(Permission.SERVER_EDIT, action=tr("view the notification settings"))
        row = await self._session.get(ServerNotification, server.id)
        return _describe(_row_to_dict(row), SERVER_EVENTS)

    async def update_server(
        self,
        server: Server,
        *,
        enabled: bool | None = None,
        events: list[str] | None = None,
        webhook_url: str | None = None,
        clear_webhook: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        context.require(Permission.SERVER_EDIT, action=tr("change the notification settings"))
        row = await self._session.get(ServerNotification, server.id)
        stored = _apply(
            _row_to_dict(row),
            SERVER_EVENTS,
            enabled=enabled,
            events=events,
            webhook_url=webhook_url,
            clear_webhook=clear_webhook,
        )
        if row is None:
            row = ServerNotification(server_id=server.id)
            self._session.add(row)
        row.enabled = bool(stored.get("enabled", False))
        row.events = list(stored.get("events", []))
        row.webhook_url_enc = stored.get("webhook_url")

        self._audit.record(
            action=AuditAction.SERVER_UPDATED,
            summary=tr("Discord notifications of “{name}” changed.", name=server.name),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="notifications",
            payload={"enabled": row.enabled, "events": row.events},
        )
        return _describe(stored, SERVER_EVENTS)

    async def server_webhook(self, server: Server, *, context: AccessContext) -> str:
        context.require(Permission.SERVER_EDIT, action=tr("test notifications"))
        row = await self._session.get(ServerNotification, server.id)
        return _plain_url(_row_to_dict(row))


async def load_notification_settings(server_id: int | None) -> dict[str, Any]:
    """Réglages en clair pour le notifier : ceux d'un serveur, ou les globaux.

    Ouvre sa propre session, et relit à chaque envoi : cocher un événement ou
    couper les notifications s'applique sans redémarrer MSM.
    """
    try:
        async with session_scope() as session:
            if server_id is None:
                row = await session.get(AppSetting, GLOBAL_KEY)
                stored = row.value if row is not None and isinstance(row.value, dict) else {}
            else:
                stored = _row_to_dict(await session.get(ServerNotification, server_id))
    except Exception as exc:
        logger.warning("notification_settings_unavailable", server_id=server_id, error=str(exc))
        return {}

    encrypted = stored.get("webhook_url")
    return {
        "enabled": bool(stored.get("enabled", False)),
        "events": list(stored.get("events", [])),
        "webhook_url": decrypt_secret(encrypted) if isinstance(encrypted, str) else None,
    }
