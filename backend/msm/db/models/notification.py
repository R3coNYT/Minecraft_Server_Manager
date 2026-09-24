"""Réglages des notifications Discord propres à un serveur.

Chaque serveur peut annoncer ses événements dans son propre salon. Le webhook
global, lui, reste dans `app_settings` et ne sert qu'aux événements de MSM
(création et suppression de serveurs).
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base, TimestampMixin


class ServerNotification(Base, TimestampMixin):
    """Webhook, activation et événements cochés d'un serveur."""

    __tablename__ = "server_notifications"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: URL du webhook, chiffrée avec la clé applicative : elle permet à quiconque
    #: la détient d'écrire dans le salon.
    webhook_url_enc: Mapped[str | None] = mapped_column(String(1024))
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
