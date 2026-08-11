"""Hiérarchie d'erreurs de MSM.

Toute erreur métier porte quatre informations destinées à l'utilisateur final :

* ``code``        — identifiant stable, exploitable par le frontend ;
* ``message``     — ce qui n'a pas fonctionné ;
* ``cause``       — pourquoi (optionnel mais fortement encouragé) ;
* ``remediation`` — ce que l'utilisateur peut faire pour corriger.

Ce quadruplet alimente directement l'affichage « Cause / Action » de l'interface.
La trace technique complète, elle, ne quitte jamais les logs serveur.
"""

from __future__ import annotations

from typing import Any


class MsmError(Exception):
    """Erreur métier convertible en réponse HTTP structurée."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        cause: str | None = None,
        remediation: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.remediation = remediation
        self.context = context or {}
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code

    def to_payload(self, trace_id: str | None = None) -> dict[str, Any]:
        """Représentation JSON renvoyée au client."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.cause:
            payload["cause"] = self.cause
        if self.remediation:
            payload["remediation"] = self.remediation
        if trace_id:
            payload["trace_id"] = trace_id
        return payload

    def __str__(self) -> str:
        parts = [self.message]
        if self.cause:
            parts.append(f"Cause : {self.cause}")
        if self.remediation:
            parts.append(f"Action : {self.remediation}")
        return " | ".join(parts)


# --------------------------------------------------------------------------- #
#  Configuration / démarrage
# --------------------------------------------------------------------------- #
class ConfigurationError(MsmError):
    code = "CONFIGURATION_ERROR"
    status_code = 500


# --------------------------------------------------------------------------- #
#  Authentification / autorisation
# --------------------------------------------------------------------------- #
class AuthenticationError(MsmError):
    code = "AUTHENTICATION_FAILED"
    status_code = 401


class PermissionDenied(MsmError):
    code = "PERMISSION_DENIED"
    status_code = 403


# --------------------------------------------------------------------------- #
#  Ressources
# --------------------------------------------------------------------------- #
class NotFoundError(MsmError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(MsmError):
    code = "CONFLICT"
    status_code = 409


class ValidationError(MsmError):
    code = "VALIDATION_ERROR"
    status_code = 422


# --------------------------------------------------------------------------- #
#  Cycle de vie des serveurs
# --------------------------------------------------------------------------- #
class ServerError(MsmError):
    """Base des erreurs liées à un serveur Minecraft."""

    code = "SERVER_ERROR"
    status_code = 409


class InvalidStateTransition(ServerError):
    code = "INVALID_STATE_TRANSITION"


class ServerAlreadyRunning(ServerError):
    code = "SERVER_ALREADY_RUNNING"


class ServerNotRunning(ServerError):
    code = "SERVER_NOT_RUNNING"


class ServerStartFailed(ServerError):
    code = "SERVER_START_FAILED"
    status_code = 500


class ServerStopFailed(ServerError):
    code = "SERVER_STOP_FAILED"
    status_code = 500


class LaunchError(ServerError):
    """Le lanceur ne peut pas construire une commande de démarrage valide."""

    code = "LAUNCH_ERROR"
    status_code = 400


class ConsoleUnavailable(ServerError):
    """Le processus tourne mais son entrée standard n'est pas atteignable."""

    code = "CONSOLE_UNAVAILABLE"


# --------------------------------------------------------------------------- #
#  Système de fichiers
# --------------------------------------------------------------------------- #
class PathTraversalError(MsmError):
    """Un chemin fourni tente de sortir du répertoire autorisé."""

    code = "PATH_TRAVERSAL"
    status_code = 400


class UnsafeUploadError(MsmError):
    code = "UNSAFE_UPLOAD"
    status_code = 400


# --------------------------------------------------------------------------- #
#  Commandes Minecraft
# --------------------------------------------------------------------------- #
class UnsafeCommandError(MsmError):
    """Commande malformée ou contenant une tentative d'injection."""

    code = "UNSAFE_COMMAND"
    status_code = 400


class ConfirmationRequired(MsmError):
    """Action sensible : le client doit renvoyer la requête avec ``confirm=true``."""

    code = "CONFIRMATION_REQUIRED"
    status_code = 428
