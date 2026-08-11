"""Tests des primitives de sécurité : mots de passe, jetons, chiffrement, droits."""

from __future__ import annotations

import pytest

from msm.core.permissions import Permission, Role
from msm.exceptions import PermissionDenied, ValidationError
from msm.security.password import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password_strength,
    verify_password,
)
from msm.security.rbac import AccessContext, build_context
from msm.security.tokens import generate_token, hash_token, tokens_equal


class TestPasswords:
    def test_hash_is_never_the_password(self) -> None:
        password = "un-mot-de-passe-solide-42"
        digest = hash_password(password)

        assert password not in digest
        assert digest.startswith("$argon2id$")

    def test_verification(self) -> None:
        digest = hash_password("un-mot-de-passe-solide-42")

        assert verify_password(digest, "un-mot-de-passe-solide-42")
        assert not verify_password(digest, "autre-chose")

    def test_two_hashes_of_the_same_password_differ(self) -> None:
        """Le sel aléatoire empêche de repérer deux comptes au même mot de passe."""
        password = "un-mot-de-passe-solide-42"

        assert hash_password(password) != hash_password(password)

    def test_verification_never_raises_on_garbage(self) -> None:
        assert not verify_password("pas-une-empreinte", "peu importe")
        assert not verify_password("", "peu importe")

    def test_length_policy(self) -> None:
        assert validate_password_strength("a" * MIN_PASSWORD_LENGTH)

        with pytest.raises(ValidationError) as excinfo:
            validate_password_strength("court")
        assert excinfo.value.remediation and "phrase de passe" in excinfo.value.remediation

    def test_empty_password_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_password_strength("")


class TestTokens:
    def test_tokens_are_unique_and_long(self) -> None:
        tokens = {generate_token() for _ in range(100)}

        assert len(tokens) == 100
        assert all(len(token) >= 60 for token in tokens)

    def test_hash_is_stable_and_irreversible(self) -> None:
        token = generate_token()

        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token)
        assert len(hash_token(token)) == 64

    def test_comparison(self) -> None:
        assert tokens_equal("abc", "abc")
        assert not tokens_equal("abc", "abd")


class TestAccessContext:
    def test_admin_context_has_everything(self) -> None:
        context = AccessContext(
            user_id=1,
            username="flavien",
            role=Role.ADMIN,
            permissions=frozenset(Permission),
        )

        context.require(Permission.SERVER_DELETE)  # ne lève pas
        assert context.has(Permission.CONSOLE_DANGEROUS)

    def test_denial_explains_what_is_missing(self) -> None:
        context = AccessContext(
            user_id=2,
            username="lecteur",
            role=Role.VIEWER,
            permissions=frozenset({Permission.SERVER_VIEW}),
            server_id=3,
        )

        with pytest.raises(PermissionDenied) as excinfo:
            context.require(Permission.SERVER_START, action="démarrer ce serveur")

        error = excinfo.value
        assert "#3" in error.message
        assert error.cause and "server:start" in error.cause
        assert error.remediation

    def test_unknown_stored_permission_is_ignored(self) -> None:
        """Une permission retirée du code ne doit pas bloquer la connexion."""

        class FakeOverride:
            granted = ("console:write", "permission:qui:nexiste:plus")
            revoked = ()

        class FakeUser:
            id = 5
            username = "moderateur"
            role = Role.MODERATOR

        context = build_context(FakeUser(), server_id=1, override=FakeOverride())  # type: ignore[arg-type]

        assert context.has(Permission.CONSOLE_WRITE)

    def test_revocation_beats_grant(self) -> None:
        class FakeOverride:
            granted = ("console:write",)
            revoked = ("console:write",)

        class FakeUser:
            id = 5
            username = "moderateur"
            role = Role.MODERATOR

        context = build_context(FakeUser(), server_id=1, override=FakeOverride())  # type: ignore[arg-type]

        assert not context.has(Permission.CONSOLE_WRITE)


class TestSecretEncryption:
    def test_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from msm.security import crypto

        crypto.reset_cipher_cache()
        monkeypatch.setattr(
            crypto, "get_settings", lambda: type("S", (), {"secret_key": "k" * 64})()
        )

        encrypted = crypto.encrypt_secret("mot-de-passe-rcon")

        assert "mot-de-passe-rcon" not in encrypted
        assert crypto.decrypt_secret(encrypted) == "mot-de-passe-rcon"
        crypto.reset_cipher_cache()

    def test_wrong_key_returns_none_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un secret illisible ne doit pas empêcher le panel de démarrer."""
        from msm.security import crypto

        crypto.reset_cipher_cache()
        monkeypatch.setattr(
            crypto, "get_settings", lambda: type("S", (), {"secret_key": "a" * 64})()
        )
        encrypted = crypto.encrypt_secret("secret")

        crypto.reset_cipher_cache()
        monkeypatch.setattr(
            crypto, "get_settings", lambda: type("S", (), {"secret_key": "b" * 64})()
        )

        assert crypto.decrypt_secret(encrypted) is None
        crypto.reset_cipher_cache()
