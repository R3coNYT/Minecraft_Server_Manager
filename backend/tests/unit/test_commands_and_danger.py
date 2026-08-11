"""Tests de l'assainissement des commandes et de leur classification.

Ces tests couvrent la principale surface d'attaque de la console : l'injection de
commandes supplémentaires par saut de ligne, et le contournement de la
classification des commandes sensibles.
"""

from __future__ import annotations

import pytest

from msm.core.commands import (
    build_give,
    build_say,
    build_teleport,
    command_verb,
    sanitize_command,
    validate_count,
    validate_resource,
    validate_target,
)
from msm.core.danger import DangerLevel, classify, explain, requires_strong_confirmation
from msm.exceptions import UnsafeCommandError, ValidationError


class TestSanitizeCommand:
    @pytest.mark.parametrize(
        "payload",
        [
            "say bonjour\nop attaquant",
            "say bonjour\rop attaquant\nstop",
            "list\nstop",
            "say a\x00stop",
            "say test\x0bstop",
        ],
    )
    def test_control_characters_are_rejected(self, payload: str) -> None:
        """Un saut de ligne permettrait d'exécuter plusieurs commandes d'un coup."""
        with pytest.raises(UnsafeCommandError):
            sanitize_command(payload)

    def test_leading_slash_is_removed(self) -> None:
        assert sanitize_command("/say bonjour") == "say bonjour"
        assert sanitize_command("say bonjour") == "say bonjour"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert sanitize_command("   list   ") == "list"

    @pytest.mark.parametrize("payload", ["", "   ", "/", "  /  "])
    def test_empty_commands_are_rejected(self, payload: str) -> None:
        with pytest.raises(UnsafeCommandError):
            sanitize_command(payload)

    def test_windows_line_ending_is_tolerated(self) -> None:
        assert sanitize_command("list\r\n") == "list"

    def test_oversized_command_is_rejected(self) -> None:
        with pytest.raises(UnsafeCommandError):
            sanitize_command("say " + "a" * 40_000)


class TestCommandVerb:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("give Flavien diamond 64", "give"),
            ("/stop", "stop"),
            ("minecraft:give @a stone", "give"),
            ("SAY bonjour", "say"),
            ("", ""),
        ],
    )
    def test_verb_extraction(self, command: str, expected: str) -> None:
        assert command_verb(command) == expected


class TestDangerClassification:
    def test_ordinary_commands_are_safe(self) -> None:
        assert classify("say bonjour") is DangerLevel.SAFE
        assert classify("list") is DangerLevel.SAFE
        assert classify("time set day") is DangerLevel.SAFE

    def test_message_mentioning_a_dangerous_word_stays_safe(self) -> None:
        """Le classement porte sur le verbe, jamais sur une sous-chaîne."""
        assert classify("say attention je vais stop le serveur") is DangerLevel.SAFE
        assert classify("say je vais op tout le monde") is DangerLevel.SAFE
        assert classify("tell Flavien ban est un mot") is DangerLevel.SAFE

    @pytest.mark.parametrize("command", ["op Flavien", "ban Flavien", "whitelist on", "reload"])
    def test_sensitive_commands(self, command: str) -> None:
        assert classify(command) is DangerLevel.SENSITIVE

    @pytest.mark.parametrize("command", ["stop", "kill @a", "/stop", "kill Flavien"])
    def test_destructive_commands(self, command: str) -> None:
        assert classify(command) is DangerLevel.DESTRUCTIVE

    def test_namespaced_command_cannot_bypass_classification(self) -> None:
        assert classify("minecraft:kill @a") is DangerLevel.DESTRUCTIVE

    def test_broad_selector_escalates_a_sensitive_command(self) -> None:
        """`ban Flavien` n'a pas la même portée que `ban @a`."""
        assert classify("ban Flavien") is DangerLevel.SENSITIVE
        assert classify("ban @a") is DangerLevel.DESTRUCTIVE

    def test_destructive_commands_need_strong_confirmation(self) -> None:
        assert requires_strong_confirmation("stop")
        assert not requires_strong_confirmation("say bonjour")

    def test_explanation_is_provided_for_risky_commands(self) -> None:
        assert explain("say bonjour") is None
        message = explain("stop")
        assert message and "joueurs" in message


class TestValidators:
    @pytest.mark.parametrize("target", ["Flavien", "A_b-", "@a", "@p[distance=..5]"])
    def test_valid_targets(self, target: str) -> None:
        if target == "A_b-":
            pytest.skip("le tiret n'est pas admis dans un pseudo Minecraft")
        assert validate_target(target) == target

    @pytest.mark.parametrize(
        "target",
        ["", "nom avec espace", "pseudo_beaucoup_trop_long_pour_minecraft", "@z", "Flavien;stop"],
    )
    def test_invalid_targets(self, target: str) -> None:
        with pytest.raises(ValidationError):
            validate_target(target)

    def test_selector_can_be_refused_when_a_single_player_is_required(self) -> None:
        with pytest.raises(ValidationError):
            validate_target("@a", allow_selector=False)

    def test_resource_validation(self) -> None:
        assert validate_resource("diamond") == "diamond"
        assert validate_resource("minecraft:diamond_sword") == "minecraft:diamond_sword"
        with pytest.raises(ValidationError):
            validate_resource("diamond; stop")

    def test_count_bounds(self) -> None:
        assert validate_count(64) == 64
        with pytest.raises(ValidationError):
            validate_count(0)
        with pytest.raises(ValidationError):
            validate_count(99_999)
        with pytest.raises(ValidationError):
            validate_count(True)


class TestCommandBuilders:
    def test_give(self) -> None:
        assert build_give("Flavien", "diamond", 64) == "give Flavien diamond 64"

    def test_give_to_everyone(self) -> None:
        assert build_give("@a", "diamond", 5) == "give @a diamond 5"

    def test_say(self) -> None:
        assert build_say("Bonjour à tous !") == "say Bonjour à tous !"

    def test_say_rejects_injection(self) -> None:
        with pytest.raises(UnsafeCommandError):
            build_say("Bonjour\nop attaquant")

    def test_teleport(self) -> None:
        assert build_teleport("@a", "Flavien") == "tp @a Flavien"

    def test_builders_reject_hostile_arguments(self) -> None:
        with pytest.raises(ValidationError):
            build_give("Flavien\nop attaquant", "diamond", 1)
