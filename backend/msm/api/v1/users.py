"""Gestion des comptes et des droits par serveur (réservée aux administrateurs)."""

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
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository, ServerPermissionRepository
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.security.rbac import AccessContext

router = APIRouter(prefix="/users", tags=["utilisateurs"])

AdminOnly = Annotated[AccessContext, Depends(require_permission(Permission.USER_MANAGE))]


@router.get("", response_model=list[UserOut], summary="Lister les comptes")
async def list_users(auth: AuthServiceDep, _: AdminOnly) -> list[UserOut]:
    return [UserOut.model_validate(user) for user in await auth.list_users()]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un compte",
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
    summary="Modifier un compte",
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
            "Compte introuvable.",
            cause=f"Aucun utilisateur ne porte l'identifiant {user_id}.",
            remediation="Rafraîchir la liste des comptes.",
        )

    changes = payload.model_dump(exclude_unset=True)

    # Un administrateur ne doit pas pouvoir se retirer lui-même ses propres
    # droits : le panel se retrouverait potentiellement sans aucun administrateur.
    if user.id == actor.id:
        if changes.get("is_active") is False:
            raise ValidationError(
                "Impossible de désactiver son propre compte.",
                cause="Cette action vous priverait immédiatement de tout accès.",
                remediation="Demander à un autre administrateur d'effectuer cette opération.",
            )
        if "role" in changes and changes["role"] != user.role:
            raise ValidationError(
                "Impossible de modifier son propre rôle.",
                cause="Cette action pourrait laisser le panel sans administrateur.",
                remediation="Demander à un autre administrateur d'effectuer cette opération.",
            )

    for field, value in changes.items():
        setattr(user, field, value)

    # Un changement de rôle ou une désactivation doit prendre effet tout de suite,
    # pas à l'expiration des sessions ouvertes.
    if "role" in changes or changes.get("is_active") is False:
        await auth.revoke_all_sessions(user.id)

    AuditRepository(session).record(
        action=AuditAction.USER_UPDATED,
        summary=f"Modification du compte {user.username}.",
        actor_id=actor.id,
        actor_username=actor.username,
        actor_role=actor.role.value,
        ip_address=ip,
        target_type="user",
        target_id=str(user.id),
        payload={"changes": sorted(changes)},
    )
    return UserOut.model_validate(user)


@router.delete("/{user_id}", summary="Supprimer un compte", dependencies=[CsrfProtected])
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
            "Compte introuvable.",
            cause=f"Aucun utilisateur ne porte l'identifiant {user_id}.",
            remediation="Rafraîchir la liste des comptes.",
        )
    if user.id == actor.id:
        raise ConflictError(
            "Impossible de supprimer son propre compte.",
            cause="Vous êtes connecté avec ce compte.",
            remediation="Demander à un autre administrateur d'effectuer cette opération.",
        )

    username = user.username
    await session.delete(user)

    AuditRepository(session).record(
        action=AuditAction.USER_DELETED,
        summary=f"Suppression du compte {username}.",
        actor_id=actor.id,
        actor_username=actor.username,
        actor_role=actor.role.value,
        ip_address=ip,
        target_type="user",
        target_id=str(user_id),
    )
    return {"status": "supprimé"}


@router.put(
    "/{user_id}/servers/{server_id}/permissions",
    summary="Droits d'un utilisateur sur un serveur",
    dependencies=[CsrfProtected],
)
async def set_server_permissions(
    user_id: int,
    server_id: int,
    granted: list[str],
    revoked: list[str],
    session: DbSession,
    actor: CurrentUser,
    ip: ClientIp,
    _: AdminOnly,
) -> dict[str, list[str]]:
    """Définit les permissions ajoutées ou retirées sur un serveur précis.

    En cas de conflit, une permission à la fois accordée et révoquée est refusée :
    en sécurité, le refus l'emporte.
    """
    valid = {permission.value for permission in Permission}
    unknown = sorted((set(granted) | set(revoked)) - valid)
    if unknown:
        raise ValidationError(
            "Permission inconnue.",
            cause=f"Valeurs non reconnues : {', '.join(unknown)}.",
            remediation="Utiliser les identifiants de permission listés par l'API.",
        )

    record = await ServerPermissionRepository(session).upsert(
        user_id=user_id, server_id=server_id, granted=granted, revoked=revoked
    )

    AuditRepository(session).record(
        action=AuditAction.PERMISSIONS_UPDATED,
        summary=f"Droits du compte #{user_id} modifiés sur le serveur #{server_id}.",
        actor_id=actor.id,
        actor_username=actor.username,
        actor_role=actor.role.value,
        ip_address=ip,
        server_id=server_id,
        target_type="user",
        target_id=str(user_id),
        payload={"granted": granted, "revoked": revoked},
    )
    return {"granted": record.granted, "revoked": record.revoked}
