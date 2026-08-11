"""Envoi de commandes à la console d'un serveur.

Trois contrôles s'enchaînent avant qu'une commande n'atteigne le serveur :

1. **assainissement** — aucun saut de ligne, donc aucune commande cachée ;
2. **classification** — une commande sensible exige une permission dédiée et une
   confirmation explicite du client ;
3. **audit** — l'auteur, la commande exacte et le serveur sont consignés.

Le troisième point est la raison pour laquelle les commandes passent par HTTP et
non par le WebSocket : elles empruntent ainsi le même chemin d'autorisation et de
journalisation que n'importe quelle autre action.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.commands import sanitize_command
from msm.core.danger import DangerLevel, classify, explain
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction, AuditResult
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.exceptions import ConfirmationRequired
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext

logger = get_logger(__name__)


class ConsoleService:
    """Cas d'usage de la console d'administration."""

    def __init__(self, session: AsyncSession, supervisor: Supervisor) -> None:
        self._session = session
        self._supervisor = supervisor
        self._audit = AuditRepository(session)

    async def send_command(
        self,
        server: Server,
        raw_command: str,
        *,
        context: AccessContext,
        confirm: bool = False,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Valide puis transmet une commande console."""
        command = sanitize_command(raw_command)
        level = classify(command)

        context.require(Permission.CONSOLE_WRITE, action="envoyer une commande")
        if level is not DangerLevel.SAFE:
            try:
                context.require(Permission.CONSOLE_DANGEROUS, action="exécuter cette commande")
            except Exception:
                self._audit.record(
                    action=AuditAction.COMMAND_SENT,
                    summary=f"Commande refusée sur « {server.name} » : {command}",
                    actor_id=context.user_id,
                    actor_username=context.username,
                    actor_role=context.role.value,
                    ip_address=ip_address,
                    server_id=server.id,
                    result=AuditResult.DENIED,
                    payload={"command": command, "danger": level.name},
                )
                # Validé avant de relever : sinon le rejet annulerait sa propre trace.
                await self._session.commit()
                raise

        if level is not DangerLevel.SAFE and not confirm:
            # Le refus n'est pas une erreur : le client doit simplement rejouer
            # la requête avec `confirm: true` après affichage de l'avertissement.
            raise ConfirmationRequired(
                "Confirmation requise.",
                cause=explain(command) or "Cette commande est sensible.",
                remediation="Renvoyer la requête avec `confirm: true` pour confirmer.",
                context={
                    "command": command,
                    "danger": level.name,
                    "strong": level is DangerLevel.DESTRUCTIVE,
                },
            )

        runtime = self._supervisor.get(server.id)
        sent = await runtime.send_command(command, actor=context.username)

        self._audit.record(
            action=AuditAction.COMMAND_SENT,
            summary=f"Commande sur « {server.name} » : {sent}",
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"command": sent, "danger": level.name},
        )
        logger.info(
            "console_command",
            server_id=server.id,
            actor=context.username,
            danger=level.name,
            command=sent,
        )
        return {"command": sent, "danger": level.name}

    def inspect(self, command: str) -> dict[str, Any]:
        """Décrit une commande sans l'exécuter — alimente la boîte de confirmation."""
        clean = sanitize_command(command)
        level = classify(clean)
        return {
            "command": clean,
            "danger": level.name,
            "requires_confirmation": level is not DangerLevel.SAFE,
            "requires_strong_confirmation": level is DangerLevel.DESTRUCTIVE,
            "explanation": explain(clean),
        }
