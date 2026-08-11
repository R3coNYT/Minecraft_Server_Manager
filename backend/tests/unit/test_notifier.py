"""Tests des notifications Discord."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from msm.bus import EventBus, topics
from msm.services.notifier import (
    MAX_LINES_PER_MESSAGE,
    Notification,
    NotificationEvent,
    Notifier,
    render_batch,
    send_to_discord,
)

WEBHOOK = "https://discord.com/api/webhooks/1/jeton"


def _notification(event: NotificationEvent = NotificationEvent.SERVER_CRASHED) -> Notification:
    return Notification(event, "survie", "code 1", ts=datetime(2026, 8, 11, 3, 5, tzinfo=UTC))


class TestRendering:
    def test_a_line_says_what_where_and_when(self) -> None:
        rendered = _notification().render()

        assert "survie" in rendered
        assert "03:05" in rendered
        assert "code 1" in rendered

    def test_a_long_batch_is_summarised(self) -> None:
        """Cinquante lignes rendraient le message illisible."""
        batch = render_batch([_notification() for _ in range(MAX_LINES_PER_MESSAGE + 5)])

        assert batch.count("\n") == MAX_LINES_PER_MESSAGE
        assert "et 5 autre" in batch


@pytest.mark.asyncio
class TestSending:
    async def test_successful_send(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.append(json.loads(request.content))
            return httpx.Response(204)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        assert await send_to_discord(WEBHOOK, "bonjour", client=client) is True
        # Les mentions sont neutralisées : une notification ne doit pas réveiller
        # tout le serveur avec un `@everyone` venu d'un nom de monde.
        assert seen[0]["allowed_mentions"] == {"parse": []}
        await client.aclose()

    async def test_a_rejected_webhook_is_not_retried_forever(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(404, text="Unknown Webhook")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        assert await send_to_discord(WEBHOOK, "bonjour", client=client) is False
        # Un webhook supprimé ne réapparaîtra pas à la seconde tentative.
        assert attempts == 1
        await client.aclose()

    async def test_a_network_error_never_propagates(self) -> None:
        """Une notification qui échoue ne doit rien casser en amont."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("réseau coupé", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        assert await send_to_discord(WEBHOOK, "bonjour", client=client) is False
        await client.aclose()


@pytest.mark.asyncio
class TestFiltering:
    async def _flush(self, notifier: Notifier) -> bool:
        return await notifier.flush()

    async def test_nothing_is_sent_when_disabled(self, monkeypatch) -> None:
        sent: list[str] = []
        monkeypatch.setattr(
            "msm.services.notifier.send_to_discord",
            lambda url, content, **kwargs: sent.append(content) or True,
        )

        async def settings() -> dict:
            return {"enabled": False, "webhook_url": WEBHOOK, "events": ["server_crashed"]}

        notifier = Notifier(EventBus(), settings)
        notifier.notify(_notification())

        assert await notifier.flush() is False
        assert sent == []

    async def test_unchecked_events_are_dropped(self, monkeypatch) -> None:
        sent: list[str] = []

        async def fake_send(url: str, content: str, **kwargs) -> bool:
            sent.append(content)
            return True

        monkeypatch.setattr("msm.services.notifier.send_to_discord", fake_send)

        async def settings() -> dict:
            return {"enabled": True, "webhook_url": WEBHOOK, "events": ["backup_failed"]}

        notifier = Notifier(EventBus(), settings)
        notifier.notify(_notification(NotificationEvent.SERVER_CRASHED))
        notifier.notify(_notification(NotificationEvent.BACKUP_FAILED))

        assert await notifier.flush() is True
        assert len(sent) == 1
        assert "sauvegarde" in sent[0].lower()

    async def test_queue_is_emptied_even_when_nothing_is_sent(self) -> None:
        """Sinon la file grossirait indéfiniment sur une instance sans webhook."""

        async def settings() -> dict:
            return {}

        notifier = Notifier(EventBus(), settings)
        notifier.notify(_notification())
        await notifier.flush()

        assert await notifier.flush() is False


class TestBusTranslation:
    def test_a_crash_becomes_a_notification(self) -> None:
        notifier = Notifier(EventBus(), lambda: {})

        notifier._collect(
            topics.server_topic(1, topics.CRASH),
            {"server_id": 1, "server": "survie", "reason": "code 1"},
        )

        assert notifier._queue[0].event is NotificationEvent.SERVER_CRASHED
        assert notifier._queue[0].server_name == "survie"

    def test_a_failed_backup_becomes_a_notification(self) -> None:
        notifier = Notifier(EventBus(), lambda: {})

        notifier._collect(
            topics.server_topic(1, topics.BACKUP),
            {"server": "survie", "status": "FAILED", "error": "disque plein"},
        )

        assert notifier._queue[0].event is NotificationEvent.BACKUP_FAILED
        assert "disque plein" in notifier._queue[0].detail

    def test_progress_lines_are_not_notified(self) -> None:
        """Une sauvegarde en cours publie des dizaines de messages : aucun n'est un fait."""
        notifier = Notifier(EventBus(), lambda: {})

        notifier._collect(
            topics.server_topic(1, topics.BACKUP),
            {"server": "survie", "status": "RUNNING", "percent": 42},
        )

        assert notifier._queue == []
