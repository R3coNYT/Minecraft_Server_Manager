"""Réglages modifiables depuis l'interface, stockés en base.

Distincts de la configuration `.env`, et c'est délibéré : ce qui touche au
déploiement — chemins, clé secrète, base de données — reste dans un fichier que
seul l'administrateur système modifie ; ce qui touche à l'usage quotidien se
règle depuis le panneau, sans redémarrer le service.

Les notifications Discord ont leur propre service (`notification_service`).
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
from msm.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, set_language, tr
from msm.logging_conf import get_logger
from msm.security.rbac import AccessContext

logger = get_logger(__name__)

#: Clé de la langue de l'interface dans `app_settings`.
LANGUAGE_KEY = "ui.language"


class SettingsService:
    """Lecture et écriture des réglages applicatifs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    async def update_language(
        self, language: str, *, context: AccessContext, ip_address: str | None = None
    ) -> str:
        """Change la langue de l'interface, pour tous les comptes."""
        context.require(Permission.SETTINGS_MANAGE, action=tr("change the settings"))
        if language not in SUPPORTED_LANGUAGES:
            raise ValidationError(
                tr("Unsupported language."),
                cause=tr("“{language}” is not an available language.", language=language),
                remediation=tr("Choose one of: {choices}.", choices=", ".join(SUPPORTED_LANGUAGES)),
            )
        await self._write(LANGUAGE_KEY, {"language": language})
        # Appliquée aussitôt : les textes produits à partir de maintenant — y
        # compris ce résumé d'audit — sortent dans la nouvelle langue.
        set_language(language)
        self._audit.record(
            action=AuditAction.SETTINGS_UPDATED,
            summary=tr("Interface language set to {language}.", language=language),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="settings",
            payload={"language": language},
        )
        return language

    async def _write(self, key: str, value: dict[str, Any]) -> None:
        row = await self._session.get(AppSetting, key)
        if row is None:
            self._session.add(AppSetting(key=key, value=value))
        else:
            row.value = value


async def load_language() -> str:
    """Applique la langue enregistrée. Appelée au démarrage de MSM."""
    try:
        async with session_scope() as session:
            row = await session.get(AppSetting, LANGUAGE_KEY)
            stored = row.value if row is not None and isinstance(row.value, dict) else {}
    except Exception as exc:  # base absente ou migrations non appliquées
        logger.warning("language_setting_unavailable", error=str(exc))
        stored = {}
    language = stored.get("language")
    return set_language(language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE)
