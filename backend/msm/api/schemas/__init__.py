"""Schémas d'entrée et de sortie de l'API."""

from msm.api.schemas.auth import (
    CsrfOut,
    LoginRequest,
    MeOut,
    PasswordChangeRequest,
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)
from msm.api.schemas.console import (
    AuditEntryOut,
    AuditPageOut,
    CommandInspectOut,
    CommandOut,
    CommandRequest,
    LogLineOut,
    LogsOut,
    StopOut,
)
from msm.api.schemas.server import (
    DashboardOut,
    DetectionOut,
    DetectRequest,
    JarCandidateOut,
    ServerCreateRequest,
    ServerOut,
    ServerSettingsIn,
    ServerSettingsOut,
    ServerUpdateRequest,
)

__all__ = [
    "AuditEntryOut",
    "AuditPageOut",
    "CommandInspectOut",
    "CommandOut",
    "CommandRequest",
    "CsrfOut",
    "DashboardOut",
    "DetectRequest",
    "DetectionOut",
    "JarCandidateOut",
    "LogLineOut",
    "LoginRequest",
    "LogsOut",
    "MeOut",
    "PasswordChangeRequest",
    "ServerCreateRequest",
    "ServerOut",
    "ServerSettingsIn",
    "ServerSettingsOut",
    "ServerUpdateRequest",
    "StopOut",
    "UserCreateRequest",
    "UserOut",
    "UserUpdateRequest",
]
