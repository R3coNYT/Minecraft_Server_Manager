"""Tests de l'acceptation du CLUF et du bus d'événements."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from msm.bus.event_bus import EventBus
from msm.minecraft import eula

# --------------------------------------------------------------------------- #
#  eula.txt
# --------------------------------------------------------------------------- #
REAL_EULA = (
    "#By changing the setting below to TRUE you are indicating your agreement to our EULA "
    "(https://aka.ms/MinecraftEULA).\n"
    "#Mon Nov 18 21:04:11 CET 2024\n"
    "eula=false\n"
)


class TestEula:
    def test_absent_file_is_not_an_error(self, tmp_path: Path) -> None:
        status = eula.read_status(tmp_path)
        assert not status.exists
        assert not status.needs_acceptance
        assert eula.accept(tmp_path) is False

    def test_detects_refusal(self, tmp_path: Path) -> None:
        (tmp_path / "eula.txt").write_text(REAL_EULA, encoding="utf-8")
        status = eula.read_status(tmp_path)
        assert status.exists
        assert not status.accepted
        assert status.needs_acceptance

    def test_acceptance_modifies_only_the_eula_line(self, tmp_path: Path) -> None:
        path = tmp_path / "eula.txt"
        path.write_text(REAL_EULA, encoding="utf-8")

        assert eula.accept(tmp_path) is True

        content = path.read_text(encoding="utf-8")
        assert "eula=true" in content
        # Commentaire d'en-tête et horodatage intacts.
        assert content.startswith("#By changing the setting below")
        assert "Mon Nov 18 21:04:11 CET 2024" in content
        assert len(content.splitlines()) == 3

    def test_already_accepted_is_a_no_op(self, tmp_path: Path) -> None:
        path = tmp_path / "eula.txt"
        path.write_text("eula=true\n", encoding="utf-8")
        assert eula.accept(tmp_path) is False
        assert path.read_text(encoding="utf-8") == "eula=true\n"

    def test_non_utf8_content_is_preserved(self, tmp_path: Path) -> None:
        """Les fichiers localisés peuvent être en latin-1 : rien ne doit être corrompu."""
        path = tmp_path / "eula.txt"
        original = "#En changeant ce paramètre, vous acceptez le contrat.\neula=false\n"
        path.write_bytes(original.encode("latin-1"))

        assert eula.accept(tmp_path) is True

        content = path.read_bytes()
        assert b"eula=true" in content
        assert content.startswith("#En changeant ce param\xe8tre".encode("latin-1"))

    def test_spacing_and_line_endings_are_preserved(self, tmp_path: Path) -> None:
        path = tmp_path / "eula.txt"
        path.write_bytes(b"#commentaire\r\neula = false\r\n")

        assert eula.accept(tmp_path) is True

        assert path.read_bytes() == b"#commentaire\r\neula = true\r\n"

    def test_commented_eula_line_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "eula.txt"
        path.write_text("#eula=true\neula=false\n", encoding="utf-8")

        assert eula.read_status(tmp_path).accepted is False
        eula.accept(tmp_path)
        assert path.read_text(encoding="utf-8") == "#eula=true\neula=true\n"

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        (tmp_path / "eula.txt").write_text(REAL_EULA, encoding="utf-8")
        eula.accept(tmp_path)
        assert [p.name for p in tmp_path.iterdir()] == ["eula.txt"]


# --------------------------------------------------------------------------- #
#  Bus d'événements
# --------------------------------------------------------------------------- #
class TestEventBus:
    @pytest.mark.asyncio
    async def test_subscriber_receives_its_topic(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe("server.1.log")

        bus.publish("server.1.log", {"text": "bonjour"})
        event = await asyncio.wait_for(subscription.get(), timeout=1)

        assert event.payload == {"text": "bonjour"}
        subscription.close()

    @pytest.mark.asyncio
    async def test_other_topics_are_not_delivered(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe("server.1.log")

        bus.publish("server.2.log", {"text": "autre serveur"})

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(subscription.get(), timeout=0.1)
        subscription.close()

    @pytest.mark.asyncio
    async def test_prefix_subscription_captures_every_event_of_a_server(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe("server.3.")

        bus.publish("server.3.log", 1)
        bus.publish("server.3.status", 2)
        bus.publish("server.4.log", 3)

        first = await asyncio.wait_for(subscription.get(), timeout=1)
        second = await asyncio.wait_for(subscription.get(), timeout=1)
        assert {first.payload, second.payload} == {1, 2}
        assert subscription.pending == 0
        subscription.close()

    def test_publishing_without_subscriber_is_free(self) -> None:
        bus = EventBus()
        bus.publish("server.1.log", "personne n'écoute")  # ne doit pas lever

    def test_has_subscribers(self) -> None:
        bus = EventBus()
        assert not bus.has_subscribers("server.1.stats")
        subscription = bus.subscribe("server.1.")
        assert bus.has_subscribers("server.1.stats")
        subscription.close()
        assert not bus.has_subscribers("server.1.stats")

    def test_slow_subscriber_drops_oldest_events_and_counts_them(self) -> None:
        """Un client lent doit perdre des lignes, jamais faire enfler la mémoire."""
        bus = EventBus()
        subscription = bus.subscribe("server.1.log", maxsize=5)

        for index in range(20):
            bus.publish("server.1.log", index)

        assert subscription.pending == 5
        assert subscription.dropped == 15
        assert subscription.take_dropped() == 15
        assert subscription.dropped == 0
        subscription.close()

    def test_closing_unsubscribes(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe("server.1.log")
        assert bus.subscriber_count == 1
        subscription.close()
        assert bus.subscriber_count == 0
        subscription.close()  # idempotent

    def test_subscribe_requires_a_topic(self) -> None:
        with pytest.raises(ValueError, match="sujet"):
            EventBus().subscribe()
