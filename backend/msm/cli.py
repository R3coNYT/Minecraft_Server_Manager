"""Ligne de commande d'administration.

Deux commandes sont indispensables au premier démarrage :

* ``msm migrate``     — applique le schéma de base de données ;
* ``msm createadmin`` — crée le premier compte administrateur.

Le mot de passe n'est jamais accepté en argument de ligne de commande : il
apparaîtrait dans l'historique du shell et dans la liste des processus. Il est
demandé de façon masquée, ou lu dans la variable ``MSM_ADMIN_PASSWORD`` pour les
installations automatisées.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys

from msm import __version__
from msm.config import get_settings
from msm.core.permissions import Role
from msm.db.session import dispose_engine, init_engine, session_scope
from msm.logging_conf import configure_logging
from msm.security.password import MIN_PASSWORD_LENGTH
from msm.services.auth_service import AuthService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="msm", description="Minecraft Server Manager")
    parser.add_argument("--version", action="version", version=f"MSM {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("serve", help="Démarrer le serveur web")
    subparsers.add_parser("migrate", help="Appliquer les migrations de base de données")

    create_admin = subparsers.add_parser("createadmin", help="Créer un compte administrateur")
    create_admin.add_argument("username", help="Nom du compte à créer")
    create_admin.add_argument("--display-name", default=None)
    create_admin.add_argument("--email", default=None)
    create_admin.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.ADMIN.value,
        help="Rôle du compte (ADMIN par défaut)",
    )

    subparsers.add_parser("purge-sessions", help="Supprimer les sessions expirées")
    subparsers.add_parser("secret", help="Générer une clé secrète pour le fichier .env")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée de la commande ``msm``."""
    args = _build_parser().parse_args(argv)

    if args.command == "secret":
        print(secrets.token_urlsafe(64))
        return 0

    settings = get_settings()
    configure_logging(settings)

    if args.command == "serve":
        from msm.main import main as serve

        serve()
        return 0

    if args.command == "migrate":
        return _run_migrations()

    return asyncio.run(_run_async_command(args, settings))


async def _run_async_command(args: argparse.Namespace, settings: object) -> int:
    init_engine(settings)  # type: ignore[arg-type]
    try:
        if args.command == "createadmin":
            return await _create_admin(args, settings)
        if args.command == "purge-sessions":
            return await _purge_sessions(settings)
    finally:
        await dispose_engine()
    return 1


async def _create_admin(args: argparse.Namespace, settings: object) -> int:
    password = os.environ.get("MSM_ADMIN_PASSWORD") or _prompt_password()
    if password is None:
        return 1

    async with session_scope() as session:
        auth = AuthService(session, settings)  # type: ignore[arg-type]
        try:
            user = await auth.create_user(
                username=args.username,
                password=password,
                role=Role(args.role),
                display_name=args.display_name,
                email=args.email,
            )
        except Exception as exc:
            print(f"Échec : {exc}", file=sys.stderr)
            return 1

    print(f"Compte « {user.username} » créé avec le rôle {user.role.value}.")
    return 0


async def _purge_sessions(settings: object) -> int:
    async with session_scope() as session:
        removed = await AuthService(session, settings).purge_expired_sessions()  # type: ignore[arg-type]
    print(f"{removed} session(s) expirée(s) supprimée(s).")
    return 0


def _prompt_password() -> str | None:
    """Demande le mot de passe deux fois, sans écho."""
    password = getpass.getpass(f"Mot de passe ({MIN_PASSWORD_LENGTH} caractères minimum) : ")
    confirmation = getpass.getpass("Confirmation : ")
    if password != confirmation:
        print("Les mots de passe ne correspondent pas.", file=sys.stderr)
        return None
    return password


def _run_migrations() -> int:
    """Applique les migrations Alembic depuis le dossier `backend`."""
    from alembic import command
    from alembic.config import Config

    from msm.config import BACKEND_ROOT

    config_path = BACKEND_ROOT / "alembic.ini"
    if not config_path.is_file():
        print(f"Configuration Alembic introuvable : {config_path}", file=sys.stderr)
        return 1

    command.upgrade(Config(str(config_path)), "head")
    print("Migrations appliquées.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
