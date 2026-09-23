"""Hachage et vérification des mots de passe (argon2id).

Argon2id est le standard actuel : contrairement à bcrypt, il est coûteux en
mémoire, ce qui rend les attaques par GPU nettement moins rentables.

Le paramétrage est laissé à ``argon2-cffi`` (profil recommandé par la RFC 9106),
puis vérifié à chaque connexion : si la bibliothèque durcit ses valeurs par
défaut, :func:`needs_rehash` le signale et l'empreinte est recalculée de façon
transparente au moment où l'utilisateur se connecte.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from msm.exceptions import ValidationError
from msm.i18n import tr

_hasher = PasswordHasher()

#: Longueur minimale acceptée. Volontairement plus exigeante que la coutume :
#: le panel donne accès à la console de tous les serveurs.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024


def hash_password(password: str) -> str:
    """Calcule l'empreinte argon2id d'un mot de passe."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Vérifie un mot de passe. Ne lève jamais : renvoie simplement ``False``."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """L'empreinte doit-elle être recalculée avec des paramètres plus robustes ?"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> str:
    """Valide un mot de passe candidat et le renvoie inchangé.

    La règle est délibérément simple — une longueur minimale — plutôt qu'un jeu
    de contraintes de composition. Ces dernières poussent en pratique vers des
    mots de passe courts et prévisibles.
    """
    if not isinstance(password, str) or not password:
        raise ValidationError(
            tr("Missing password."),
            cause=tr("No password was provided."),
            remediation=tr("Enter a password."),
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            tr("Password too short."),
            cause=tr(
                "{length} characters for a minimum of {minimum}.",
                length=len(password),
                minimum=MIN_PASSWORD_LENGTH,
            ),
            remediation=tr(
                "Choose a password of at least {minimum} characters — a passphrase is "
                "both safer and easier to remember.",
                minimum=MIN_PASSWORD_LENGTH,
            ),
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            tr("Password too long."),
            cause=tr(
                "{length} characters for a maximum of {maximum}.",
                length=len(password),
                maximum=MAX_PASSWORD_LENGTH,
            ),
            remediation=tr("Shorten the password."),
        )
    return password
