"""Tâches programmées : sauvegardes, redémarrages et événements automatiques.

La règle de déclenchement est stockée **décomposée** (type, heure, jours, fuseau)
plutôt qu'en expression cron : l'interface la présente en clair, et une règle
lisible en base reste lisible dans une sauvegarde de la base.

``next_run_at`` est persisté et non recalculé à la volée : c'est ce qui permet de
savoir, après un redémarrage de MSM, qu'une exécution a été manquée — et de
décider en connaissance de cause si elle doit encore avoir lieu.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base, TimestampMixin
from msm.db.types import UtcDateTime


class ScheduleAction(str, Enum):
    """Ce que la tâche déclenche."""

    BACKUP = "BACKUP"
    RESTART = "RESTART"
    START = "START"
    STOP = "STOP"
    EVENT = "EVENT"
    COMMAND = "COMMAND"


class ScheduleStatus(str, Enum):
    """Issue de la dernière exécution."""

    NEVER = "NEVER"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    #: Exécution manquée pendant un arrêt de MSM, trop tardive pour être rattrapée.
    MISSED = "MISSED"
    #: Sans objet à ce moment-là (serveur déjà arrêté, par exemple).
    SKIPPED = "SKIPPED"


class Schedule(Base, TimestampMixin):
    """Une tâche programmée sur un serveur."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    action: Mapped[ScheduleAction] = mapped_column(
        SAEnum(ScheduleAction, native_enum=False, length=16), nullable=False
    )
    #: Paramètres de l'action : identifiant d'événement, commande à envoyer…
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Règle de déclenchement validée par `msm.schedule.rules`.
    rule: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_status: Mapped[ScheduleStatus] = mapped_column(
        SAEnum(ScheduleStatus, native_enum=False, length=16),
        nullable=False,
        default=ScheduleStatus.NEVER,
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    #: Les droits de l'auteur sont réévalués à chaque exécution : une tâche ne
    #: doit pas continuer d'agir au nom de quelqu'un qui a perdu le droit de le faire.
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
