"""Jetons de session et jetons CSRF.

Le jeton de session est envoyé au navigateur mais **n'est jamais stocké en
clair** : la base ne conserve que son empreinte SHA-256. Une fuite de la base ne
permet donc pas d'usurper une session en cours.

SHA-256 suffit ici, là où argon2 est indispensable pour un mot de passe : un
jeton est aléatoire sur 48 octets, il n'existe aucun dictionnaire à parcourir.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: 48 octets aléatoires ≈ 64 caractères après encodage.
TOKEN_BYTES = 48


def generate_token() -> str:
    """Produit un jeton opaque, imprévisible."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Empreinte stockée en base à la place du jeton."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Comparaison à temps constant, pour ne pas fuiter d'information par la durée."""
    return hmac.compare_digest(left, right)
