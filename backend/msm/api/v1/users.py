"""Gestion des comptes : consultation par l'équipe de MSM, gestion par les admins."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from msm.api.deps import (
    AuthServiceDep,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    require_permission,
)
from msm.api.schemas import UserCreateRequest, UserOut, UserUpdateRequest
from msm.core.permissions import Permission, Role
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository, ServerRepository
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.security.rbac import AccessContext

router = APIRouter(prefix="/users", tags=["users"])

AdminOnly = Annotated[AccessContext, Depends(require_permission(Permission.USER_MANAGE))]
StaffOnly = Annotated[AccessContext, Depends(require_permission(Permission.USER_VIEW))]

#: Ordre d'affichage : les admins d'abord, puis les modérateurs, puis les users.
_ROLE_ORDER = {Role.ADMIN: 0, Role.MODERATOR: 1, Role.USER: 2}


@router.get("", response_model=list[UserOut], summary="List accounts")
async def list_users(auth: AuthServiceDep, _: StaffOnly) -> list[UserOut]:
    users = sorted(
        await auth.list_users(),
        key=lambda user: (_ROLE_ORDER.get(user.role, 9), user.username.casefold()),
    )
    return [UserOut.model_validate(user) for user in users]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    dependencies=[CsrfProtected],
)
async def create_user(
    payload: UserCreateRequest,
    auth: AuthServiceDep,
    actor: CurrentUser,
    ip: ClientIp,
    _: AdminOnly,
) -> UserOut:
    user = await auth.create_user(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        display_name=payload.display_name,
        email=payload.email,
        actor=actor,
        ip_address=ip,
    )
    return UserOut.model_validate(user)


@router.put(
    "/{user_id}",
    response_model=UserOut,
    summary="Update an account",
    dependencies=[CsrfProtected],
)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    auth: AuthServiceDep,
    session: DbSession,
    actor: CurrentUser,
    ip: ClientIp,
    _: AdminOnly,
) -> UserOut:
    """Modifie le rôle, l'activation ou l'identité d'un compte."""
    user = await auth.get_user(user_id)
    if user is None:
        raise NotFoundError(
            tr("Account not found."),
            cause=tr("No user has the identifier {user_id}.", user_id=user_id),
            remediation=tr("Refresh the account list."),
        )

    changes = payload.model_dump(exclude_unset=True)

    # Un administrateur ne doit pas pouvoir se retirer lui-même ses propres
    # droits : le panel se retrouverait potentiellement sans aucun administrateur.
    if user.id == actor.id:
        if changes.get("is_active") is False:
            raise ValidationError(
                tr("You cannot disable your own account."),
                cause=tr("You would immediately lose all access."),
                remediation=tr("Ask another administrator to do this."),
            )
        if "role" in changes and changes["role"] != user.role:
            raise ValidationError(
                tr("You cannot change your own role."),
                cause=tr("The panel could be left without an administrator."),
                remediation=tr("Ask another administrator to do this."),
            )

    for field, value in changes.items():
        setattr(user, field, value)

    # Un changement de rôle ou une désactivation doit prendre effet tout de suite,
    # pas à l'expiration des sessions ouvertes.
    if "role" in changes or changes.get("is_active") is False:
        await auth.revoke_all_sessions(user.id)

    AuditRepository(session).record(
        action=AuditAction.USER_UPDATED,
        summary=tr("Account {username} updated.", username=user.username),
        actor_id=actor.id,
        actor_username=actor.username,
        actor_role=actor.role.value,
        ip_address=ip,
        target_type="user",
        target_id=str(user.id),
        payload={"changes": sorted(changes)},
    )
    return UserOut.model_validate(user)


@router.delete("/{user_id}", summary="Delete an account", dependencies=[CsrfProtected])
async def delete_user(
    user_id: int,
    auth: AuthServiceDep,
    session: DbSession,
    actor: CurrentUser,
    ip: ClientIp,
    _: AdminOnly,
) -> dict[str, str]:
    user = await auth.get_user(user_id)
    if user is None:
        raise NotFoundError(
            tr("Account not found."),
            cause=tr("No user has the identifier {user_id}.", user_id=user_id),
            remediation=tr("Refresh the account list."),
        )
    if user.id == actor.id:
        raise ConflictError(
            tr("You cannot delete your own account."),
            cause=tr("You are signed in with this account."),
            remediation=tr("Ask another administrator to do this."),
        )
    owned = await ServerRepository(session).list_owned_by(user.id)
    if owned:
        raise ConflictError(
            tr("This account still owns servers."),
            cause=tr(
                "{username} owns {count} server(s): {names}.",
                username=user.username,
                count=len(owned),
                names=", ".join(server.name for server in owned),
            ),
            remediation=tr("Delete its servers first."),
        )

    username = user.username
    await session.delete(user)

    AuditRepository(session).record(
        action=AuditAction.USER_DELETED,
        summary=tr("Account {username} deleted.", username=username),
        actor_id=actor.id,
        actor_username=actor.username,
        actor_role=actor.role.value,
        ip_address=ip,
        target_type="user",
        target_id=str(user_id),
    )
    return {"status": "deleted"}
