"""Droits d'un compte sur un serveur, lus en base.

Complément asynchrone de :mod:`msm.security.rbac`, qui reste un calcul pur : ici
l'on va chercher l'adhésion du compte au serveur avant de faire le calcul.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission
from msm.db.models.server import Server
from msm.db.models.user import User
from msm.db.repositories import ServerMemberRepository
from msm.exceptions import NotFoundError
from msm.i18n import tr
from msm.security.rbac import AccessContext, build_server_context


async def server_context(session: AsyncSession, user: User, server: Server) -> AccessContext:
    """Droits effectifs de `user` sur `server` : rôle global et adhésion."""
    member_role = (
        None
        if server.owner_id == user.id
        else await ServerMemberRepository(session).role_of(user.id, server.id)
    )
    return build_server_context(user, server, member_role=member_role)


def server_not_found(server_id: int) -> NotFoundError:
    """L'erreur d'un serveur absent — **ou invisible** pour ce compte.

    Les deux cas répondent la même chose : un compte ne doit pas pouvoir deviner
    qu'un serveur qu'il ne voit pas existe.
    """
    return NotFoundError(
        tr("Server not found."),
        cause=tr("No server has the identifier {server_id}.", server_id=server_id),
        remediation=tr("Refresh the server list."),
    )


async def visible_server_context(
    session: AsyncSession, user: User, server: Server
) -> AccessContext:
    """Comme :func:`server_context`, mais « introuvable » si le compte ne le voit pas."""
    context = await server_context(session, user, server)
    if not context.has(Permission.SERVER_VIEW):
        raise server_not_found(server.id)
    return context
