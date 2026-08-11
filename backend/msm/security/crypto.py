"""Chiffrement des secrets stockés en base (mots de passe RCON).

Ces secrets doivent être **réversibles** : MSM doit pouvoir s'authentifier auprès
du serveur Minecraft. Un hachage est donc exclu, contrairement aux mots de passe
des utilisateurs.

La clé de chiffrement est dérivée de ``MSM_SECRET_KEY``. Conséquence à connaître :
changer cette clé rend les secrets déjà chiffrés illisibles — ils devront être
ressaisis. C'est un compromis assumé pour n'avoir qu'un seul secret à protéger.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from msm.config import get_settings
from msm.exceptions import ConfigurationError

_SALT = b"msm.secrets.v1"


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Fabrique le chiffreur à partir de la clé applicative."""
    secret = get_settings().secret_key
    if not secret:  # pragma: no cover - déjà validé par la configuration
        raise ConfigurationError(
            "Clé secrète absente.",
            cause="MSM_SECRET_KEY est vide ; impossible de chiffrer les secrets.",
            remediation="Renseigner MSM_SECRET_KEY dans le fichier .env.",
        )
    derived = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _SALT, 200_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    """Chiffre un secret destiné à la base."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    """Déchiffre un secret. Renvoie ``None`` si la clé applicative a changé.

    Le ``None`` est délibéré : un mot de passe RCON illisible n'est pas une raison
    d'empêcher le panel de démarrer. L'appelant désactive simplement RCON et le
    signale.
    """
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return None


def reset_cipher_cache() -> None:
    """Vide le cache — réservé aux tests."""
    _cipher.cache_clear()
