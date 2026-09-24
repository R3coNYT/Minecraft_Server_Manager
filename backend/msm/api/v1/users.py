"""Gestion des comptes : consultation par l'équipe de MSM, gestion par les admins."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse

from msm.api.deps import (
    AppSettings,
    AuthServiceDep,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    GlobalContext,
    SupervisorDep,
    require_permission,
)
from msm.api.schemas import (
    AccountBanRequest,
    InvitationCreatedOut,
    InvitationCreateRequest,
    InvitationOut,
    ServerRefOut,
    UserCreateRequest,
    UserDetailOut,
    UsernameChangeOut,
    UserOut,
    UserUpdateRequest,
)
from msm.core.permissions import Permission, Role
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository, ServerRepository
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.security.rbac import AccessContext
from msm.services.account_service import AccountService, normalise_email
from msm.services.avatar_service import avatar_path

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


# --------------------------------------------------------------------------- #
#  Invitations — déclarées avant `/{user_id}`, qui les capturerait sinon.
# --------------------------------------------------------------------------- #
@router.get("/invitations", response_model=list[InvitationOut], summary="Invitations")
async def list_invitations(
    settings: AppSettings, session: DbSession, context: GlobalContext
) -> list[InvitationOut]:
    accounts = AccountService(session, settings)
    return [
        InvitationOut.model_validate(item)
        for item in await accounts.list_invitations(context=context)
    ]


@router.post(
    "/invitations",
    response_model=InvitationCreatedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invitation link",
    dependencies=[CsrfProtected],
)
async def create_invitation(
    settings: AppSettings,
    payload: InvitationCreateRequest,
    session: DbSession,
    context: GlobalContext,
    ip: ClientIp,
) -> InvitationCreatedOut:
    """Le jeton n'est renvoyé qu'ici, une seule fois : la base n'en garde que l'empreinte."""
    created = await AccountService(session, settings).create_invitation(
        note=payload.note, days=payload.days, context=context, ip_address=ip
    )
    return InvitationCreatedOut(
        **InvitationOut.model_validate(created.invitation).model_dump(), token=created.token
    )


@router.delete(
    "/invitations/{invitation_id}",
    summary="Revoke an invitation",
    dependencies=[CsrfProtected],
)
async def revoke_invitation(
    settings: AppSettings, invitation_id: int, session: DbSession, context: GlobalContext
) -> dict[str, str]:
    await AccountService(session, settings).revoke_invitation(invitation_id, context=context)
    return {"status": "revoked"}


# --------------------------------------------------------------------------- #
#  Comptes
# --------------------------------------------------------------------------- #
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
    settings: AppSettings,
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
    if changes.get("email"):
        changes["email"] = normalise_email(changes["email"])
        await AccountService(session, settings).ensure_email_free(
            changes["email"], allow_user_id=user.id
        )

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


async def _target(auth: AuthServiceDep, user_id: int) -> Any:
    user = await auth.get_user(user_id)
    if user is None:
        raise NotFoundError(
            tr("Account not found."),
            cause=tr("No user has the identifier {user_id}.", user_id=user_id),
            remediation=tr("Refresh the account list."),
        )
    return user


@router.get("/{user_id}", response_model=UserDetailOut, summary="Account details")
async def user_details(
    settings: AppSettings, user_id: int, auth: AuthServiceDep, session: DbSession, _: StaffOnly
) -> UserDetailOut:
    """Fiche d'un compte pour l'équipe : pseudos successifs, serveurs, bannissement."""
    user = await _target(auth, user_id)
    accounts = AccountService(session, settings)
    owned, shared = await accounts.servers_of(user.id)
    return UserDetailOut(
        **UserOut.model_validate(user).model_dump(),
        username_history=[
            UsernameChangeOut.model_validate(item)
            for item in await accounts.username_history(user.id)
        ],
        servers_owned=[ServerRefOut(id=server.id, name=server.name) for server in owned],
        servers_shared=[
            ServerRefOut(id=server.id, name=server.name, role=role) for server, role in shared
        ],
    )


@router.post(
    "/{user_id}/ban", response_model=UserOut, summary="Ban an account", dependencies=[CsrfProtected]
)
async def ban_user(
    settings: AppSettings,
    user_id: int,
    payload: AccountBanRequest,
    auth: AuthServiceDep,
    session: DbSession,
    supervisor: SupervisorDep,
    context: GlobalContext,
    ip: ClientIp,
) -> UserOut:
    """Sessions fermées, connexion refusée avec le motif, serveurs arrêtés et bloqués."""
    user = await _target(auth, user_id)
    await AccountService(session, settings).ban(
        user, reason=payload.reason, context=context, supervisor=supervisor, ip_address=ip
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/unban",
    response_model=UserOut,
    summary="Lift a ban",
    dependencies=[CsrfProtected],
)
async def unban_user(
    settings: AppSettings,
    user_id: int,
    auth: AuthServiceDep,
    session: DbSession,
    context: GlobalContext,
    ip: ClientIp,
) -> UserOut:
    user = await _target(auth, user_id)
    await AccountService(session, settings).unban(user, context=context, ip_address=ip)
    return UserOut.model_validate(user)


@router.get("/{user_id}/avatar", summary="Avatar of an account")
async def user_avatar(settings: AppSettings, user_id: int, _: CurrentUser) -> FileResponse:
    """Visible de tout compte connecté : les avatars s'affichent à côté des pseudos."""
    path = avatar_path(settings, user_id)
    if not path.is_file():
        raise NotFoundError(
            tr("No avatar."),
            cause=tr("This account has not chosen an avatar."),
            remediation=tr("Show the default avatar instead."),
        )
    # L'adresse change à chaque nouvel avatar (paramètre `v`) : cache long sans risque.
    return FileResponse(
        path, media_type="image/webp", headers={"Cache-Control": "private, max-age=604800"}
    )
