"""Tests de l'analyse des logs, du tampon circulaire et de la détection d'événements."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from msm.core.log_line import LineSource, LogLevel, LogLine, parse_line, strip_formatting
from msm.core.patterns import MinecraftEventKind, detect_events, diagnose_fatal
from msm.runtime.ring_buffer import RingBuffer


def make_line(text: str, seq: int = 1) -> LogLine:
    return LogLine(seq=seq, ts=datetime.now(UTC), text=text, raw=text)


class TestParsing:
    def test_modern_format(self) -> None:
        line = parse_line("[12:30:04] [Server thread/INFO]: Done (1.234s)!", seq=1)
        assert line.level is LogLevel.INFO
        assert line.thread == "Server thread"
        assert line.server_time == "12:30:04"
        assert line.text == "Done (1.234s)!"

    def test_modern_format_with_category(self) -> None:
        line = parse_line(
            "[12:30:04] [Server thread/WARN] [minecraft/DedicatedServer]: Can't keep up!", seq=1
        )
        assert line.level is LogLevel.WARN
        assert line.category == "minecraft/DedicatedServer"
        assert line.text == "Can't keep up!"

    def test_legacy_format(self) -> None:
        line = parse_line("[12:30:04 INFO]: <Flavien> bonjour", seq=1)
        assert line.level is LogLevel.INFO
        assert line.text == "<Flavien> bonjour"

    def test_unstructured_line_is_kept(self) -> None:
        line = parse_line("\tat java.base/java.lang.Thread.run(Thread.java:840)", seq=1)
        assert line.level is LogLevel.RAW
        assert "Thread.run" in line.text

    def test_stderr_lines_are_marked_as_errors(self) -> None:
        line = parse_line("Exception in thread main", seq=1, source=LineSource.STDERR)
        assert line.level is LogLevel.ERROR

    def test_warning_is_normalised(self) -> None:
        assert parse_line("[12:30:04] [main/WARNING]: attention", seq=1).level is LogLevel.WARN

    def test_colour_codes_are_stripped_but_raw_is_preserved(self) -> None:
        raw = "[12:30:04] [Server thread/INFO]: \x1b[32m§aBonjour§r"
        line = parse_line(raw, seq=1)
        assert "\x1b" not in line.text
        assert "§" not in line.text
        assert line.raw == raw

    def test_strip_formatting_helper(self) -> None:
        assert strip_formatting("§aVert§r normal") == "Vert normal"

    def test_parsing_never_raises(self) -> None:
        for payload in ("", "[", "]]]", "[99:99:99 ???]:", "\x00\x01"):
            assert parse_line(payload, seq=1) is not None


class TestEventDetection:
    def test_ready_event(self) -> None:
        line = parse_line(
            '[12:30:04] [Server thread/INFO]: Done (1.234s)! For help, type "help"', seq=1
        )
        assert detect_events(line)[0].kind is MinecraftEventKind.SERVER_READY

    def test_join_and_leave(self) -> None:
        join = parse_line("[12:31:15] [Server thread/INFO]: Flavien joined the game", seq=1)
        event = detect_events(join)[0]
        assert event.kind is MinecraftEventKind.PLAYER_JOIN
        assert event.username == "Flavien"

        leave = parse_line("[12:40:00] [Server thread/INFO]: Flavien left the game", seq=2)
        assert detect_events(leave)[0].kind is MinecraftEventKind.PLAYER_LEAVE

    def test_uuid_extraction(self) -> None:
        line = parse_line(
            "[12:31:14] [User Authenticator #1/INFO]: UUID of player Flavien is "
            "069a79f4-44e9-4726-a5be-fca90e38aaf5",
            seq=1,
        )
        event = detect_events(line)[0]
        assert event.kind is MinecraftEventKind.PLAYER_UUID
        assert event.uuid == "069a79f4-44e9-4726-a5be-fca90e38aaf5"

    def test_player_list(self) -> None:
        line = parse_line(
            "[12:35:00] [Server thread/INFO]: There are 2 of a max of 20 players online: "
            "Flavien, Steve",
            seq=1,
        )
        event = detect_events(line)[0]
        assert event.kind is MinecraftEventKind.PLAYER_LIST
        assert event.players == ("Flavien", "Steve")
        assert event.online == 2
        assert event.max_players == 20

    def test_chat_message_is_not_a_join(self) -> None:
        """Un joueur qui écrit « X joined the game » dans le chat ne doit rien déclencher."""
        line = parse_line("[12:31:15] [Server thread/INFO]: <Flavien> Steve joined the game", seq=1)
        assert not [e for e in detect_events(line) if e.kind is MinecraftEventKind.PLAYER_JOIN]

    def test_ordinary_line_produces_nothing(self) -> None:
        assert detect_events(make_line("Preparing spawn area: 42%")) == []

    @pytest.mark.parametrize(
        ("text", "expected_word"),
        [
            ("**** FAILED TO BIND TO PORT!", "port"),
            ("You need to agree to the EULA in order to run the server.", "EULA"),
            ("Error: Unable to access jarfile server.jar", "JAR"),
        ],
    )
    def test_fatal_diagnostics_are_actionable(self, text: str, expected_word: str) -> None:
        diagnostic = diagnose_fatal(text)
        assert diagnostic is not None
        cause, remediation = diagnostic
        assert expected_word.casefold() in cause.casefold()
        assert remediation


class TestRingBuffer:
    def test_keeps_only_the_most_recent_lines(self) -> None:
        buffer = RingBuffer(maxlen=3)
        for seq in range(1, 6):
            buffer.append(make_line(f"ligne {seq}", seq))

        assert len(buffer) == 3
        assert buffer.dropped == 2
        assert buffer.first_seq == 3
        assert buffer.last_seq == 5

    def test_since_supports_resuming_after_a_disconnection(self) -> None:
        buffer = RingBuffer(maxlen=10)
        for seq in range(1, 6):
            buffer.append(make_line(f"ligne {seq}", seq))

        resumed = buffer.since(3)
        assert [line.seq for line in resumed] == [4, 5]

    def test_since_on_an_up_to_date_client_returns_nothing(self) -> None:
        buffer = RingBuffer(maxlen=10)
        buffer.append(make_line("ligne", 1))
        assert buffer.since(1) == []

    def test_tail_and_before(self) -> None:
        buffer = RingBuffer(maxlen=10)
        for seq in range(1, 8):
            buffer.append(make_line(f"ligne {seq}", seq))

        assert [line.seq for line in buffer.tail(3)] == [5, 6, 7]
        assert [line.seq for line in buffer.before(4, limit=2)] == [2, 3]

    def test_search(self) -> None:
        buffer = RingBuffer(maxlen=10)
        buffer.append(make_line("Flavien joined the game", 1))
        buffer.append(make_line("Preparing spawn area", 2))
        buffer.append(make_line("Steve joined the game", 3))

        assert len(buffer.search("joined")) == 2
        assert len(buffer.search("JOINED")) == 2
        assert buffer.search("inexistant") == []

    def test_regex_search(self) -> None:
        buffer = RingBuffer(maxlen=10)
        buffer.append(make_line("Flavien joined the game", 1))
        buffer.append(make_line("Steve left the game", 2))
        assert len(buffer.search(r"^\w+ joined", use_regex=True)) == 1

    def test_invalid_regex_falls_back_to_literal_search(self) -> None:
        """Une console ne doit pas refuser une recherche à cause d'un `(` isolé."""
        buffer = RingBuffer(maxlen=10)
        buffer.append(make_line("Done (1.234s)!", 1))
        assert len(buffer.search("(1.234s)", use_regex=True)) == 1

    def test_clear_resets_the_dropped_counter(self) -> None:
        buffer = RingBuffer(maxlen=2)
        for seq in range(1, 6):
            buffer.append(make_line("x", seq))
        assert buffer.dropped > 0
        buffer.clear()
        assert len(buffer) == 0
        assert buffer.dropped == 0

    def test_resize_keeps_the_most_recent(self) -> None:
        buffer = RingBuffer(maxlen=10)
        for seq in range(1, 6):
            buffer.append(make_line("x", seq))
        buffer.resize(2)
        assert [line.seq for line in buffer.tail(10)] == [4, 5]

    def test_maxlen_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positif"):
            RingBuffer(maxlen=0)
