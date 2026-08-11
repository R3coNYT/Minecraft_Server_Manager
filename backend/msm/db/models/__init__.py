"""Modèles de données.

Importer ce module suffit à enregistrer toutes les tables dans les métadonnées —
c'est ce dont Alembic a besoin pour détecter les changements de schéma.
"""

from msm.db.models.audit import AuditAction, AuditLog, AuditResult
from msm.db.models.metrics import MetricSample
from msm.db.models.misc import (
    AppSetting,
    Backup,
    BackupStatus,
    EventDefinition,
    EventRun,
    EventRunStatus,
    Player,
    SkinCache,
)
from msm.db.models.schedule import Schedule, ScheduleAction, ScheduleStatus
from msm.db.models.server import (
    Server,
    ServerPermission,
    ServerRuntimeStateRow,
    ServerSettings,
)
from msm.db.models.user import User, UserSession

__all__ = [
    "AppSetting",
    "AuditAction",
    "AuditLog",
    "AuditResult",
    "Backup",
    "BackupStatus",
    "EventDefinition",
    "EventRun",
    "EventRunStatus",
    "MetricSample",
    "Player",
    "Schedule",
    "ScheduleAction",
    "ScheduleStatus",
    "Server",
    "ServerPermission",
    "ServerRuntimeStateRow",
    "ServerSettings",
    "SkinCache",
    "User",
    "UserSession",
]
