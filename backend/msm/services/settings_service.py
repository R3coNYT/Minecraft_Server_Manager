"""Réglages modifiables depuis l'interface, stockés en base.

Distincts de la configuration `.env`, et c'est délibéré : ce qui touche au
déploiement — chemins, clé secrète, base de données — reste dans un fichier que
seul l'administrateur système modifie ; ce qui touche à l'usage quotidien se
règle depuis le panneau, sans redémarrer le service.

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
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.exceptions import ValidationError
from msm.logging_conf import get_logger
from msm.security.crypto import decrypt_secret, encrypt_secret
from msm.security.rbac import AccessContext
from msm.services.notifier import DEFAULT_EVENTS, NotificationEvent

logger = get_logger(__name__)

#: Clé du réglage des notifications dans `app_settings`.
NOTIFICATIONS_KEY = "notifications.discord"

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


class SettingsService:
    """Lecture et écriture des réglages applicatifs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    async def _raw(self, key: str) -> dict[str, Any]:
        row = await self._session.get(AppSetting, key)
        value = row.value if row is not None else None
        return value if isinstance(value, dict) else {}

    async def notifications(self, *, context: AccessContext) -> dict[str, Any]:
        """Réglages des notifications, sans le secret."""
        context.require(Permission.SETTINGS_MANAGE, action="consulter les réglages")
        stored = await self._raw(NOTIFICATIONS_KEY)
        encrypted = stored.get("webhook_url")
        url = decrypt_secret(encrypted) if isinstance(encrypted, str) else None

        return {
            "enabled": bool(stored.get("enabled", False)),
            "events": list(stored.get("events", [event.value for event in DEFAULT_EVENTS])),
            "webhook_configured": url is not None,
            "webhook_hint": _masked(url) if url else None,
            # Un secret illisible se signale : la clé applicative a changé, et il
            # faut ressaisir l'URL plutôt que de croire les notifications actives.
            "webhook_unreadable": encrypted is not None and url is None,
        }

    async def update_notifications(
        self,
        *,
        enabled: bool | None = None,
        events: list[str] | None = None,
        webhook_url: str | None = None,
        clear_webhook: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        context.require(Permission.SETTINGS_MANAGE, action="modifier les réglages")
        stored = dict(await self._raw(NOTIFICATIONS_KEY))

        if clear_webhook:
            stored.pop("webhook_url", None)
            stored["enabled"] = False
        elif webhook_url is not None:
            clean = webhook_url.strip()
            if not clean.startswith(_ALLOWED_PREFIXES):
                raise ValidationError(
                    "Adresse de webhook invalide.",
                    cause="L'adresse ne ressemble pas à un webhook Discord.",
                    remediation=(
                        "Copier l'adresse depuis Discord : Paramètres du salon → "
                        "Intégrations → Webhooks."
                    ),
                )
            stored["webhook_url"] = encrypt_secret(clean)

        if events is not None:
            known = {event.value for event in NotificationEvent}
            unknown = [value for value in events if value not in known]
            if unknown:
                raise ValidationError(
                    "Événement inconnu.",
                    cause=f"« {unknown[0]} » n'est pas un événement notifiable.",
                    remediation="Cocher les événements proposés par l'interface.",
                )
            stored["events"] = list(dict.fromkeys(events))

        if enabled is not None:
            if enabled and "webhook_url" not in stored:
                raise ValidationError(
                    "Aucun webhook configuré.",
                    cause="Les notifications ne peuvent pas être activées sans adresse.",
                    remediation="Renseigner d'abord l'adresse du webhook Discord.",
                )
            stored["enabled"] = enabled

        await self._write(NOTIFICATIONS_KEY, stored)

        self._audit.record(
            action=AuditAction.SETTINGS_UPDATED,
            summary="Réglages des notifications Discord modifiés.",
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="settings",
            # Jamais l'URL : le journal d'audit est consultable par d'autres.
            payload={"enabled": stored.get("enabled"), "events": stored.get("events")},
        )
        return await self.notifications(context=context)

    async def webhook_url(self, *, context: AccessContext) -> str:
        """URL en clair, pour l'envoi d'un message de test."""
        context.require(Permission.SETTINGS_MANAGE, action="tester les notifications")
        stored = await self._raw(NOTIFICATIONS_KEY)
        url = decrypt_secret(stored["webhook_url"]) if stored.get("webhook_url") else None
        if not url:
            raise ValidationError(
                "Aucun webhook utilisable.",
                cause=(
                    "Aucune adresse enregistrée, ou secret illisible depuis un changement de clé."
                ),
                remediation="Renseigner l'adresse du webhook Discord.",
            )
        return url

    async def _write(self, key: str, value: dict[str, Any]) -> None:
        row = await self._session.get(AppSetting, key)
        if row is None:
            self._session.add(AppSetting(key=key, value=value))
        else:
            row.value = value


async def load_notification_settings() -> dict[str, Any]:
    """Réglages en clair, pour le notifieur. Ouvre sa propre session.

    Relu à chaque envoi : cocher un événement ou couper les notifications
    s'applique sans redémarrer MSM.
    """
    try:
        async with session_scope() as session:
            row = await session.get(AppSetting, NOTIFICATIONS_KEY)
            stored = row.value if row is not None and isinstance(row.value, dict) else {}
    except Exception as exc:
        logger.warning("notification_settings_unavailable", error=str(exc))
        return {}

    encrypted = stored.get("webhook_url")
    url = decrypt_secret(encrypted) if isinstance(encrypted, str) else None
    return {
        "enabled": bool(stored.get("enabled", False)),
        "events": list(stored.get("events", [event.value for event in DEFAULT_EVENTS])),
        "webhook_url": url,
    }
