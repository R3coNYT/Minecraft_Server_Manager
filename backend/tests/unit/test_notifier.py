"""Tests des notifications Discord."""

from __future__ import annotations

import asyncio
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
        # Horodatage Discord, affiché dans le fuseau de chaque lecteur.
        moment = int(datetime(2026, 8, 11, 3, 5, tzinfo=UTC).timestamp())
        assert f"<t:{moment}:t>" in rendered
        assert "code 1" in rendered

    def test_a_long_batch_is_summarised(self) -> None:
        """Cinquante lignes rendraient le message illisible."""
        batch = render_batch([_notification() for _ in range(MAX_LINES_PER_MESSAGE + 5)])

        assert batch.count("\n") == MAX_LINES_PER_MESSAGE
        assert "and 5 more" in batch


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

        async def settings(_server_id: int | None) -> dict:
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

        async def settings(_server_id: int | None) -> dict:
            return {"enabled": True, "webhook_url": WEBHOOK, "events": ["backup_failed"]}

        notifier = Notifier(EventBus(), settings)
        notifier.notify(_notification(NotificationEvent.SERVER_CRASHED))
        notifier.notify(_notification(NotificationEvent.BACKUP_FAILED))

        assert await notifier.flush() is True
        assert len(sent) == 1
        assert "backup" in sent[0].lower()

    async def test_queue_is_emptied_even_when_nothing_is_sent(self) -> None:
        """Sinon la file grossirait indéfiniment sur une instance sans webhook."""

        async def settings(_server_id: int | None) -> dict:
            return {}

        notifier = Notifier(EventBus(), settings)
        notifier.notify(_notification())
        await notifier.flush()

        assert await notifier.flush() is False


class TestBusTranslation:
    def test_a_crash_becomes_a_notification(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.server_topic(1, topics.CRASH),
            {"server_id": 1, "server": "survie", "reason": "code 1"},
        )

        assert notifier._queue[0].event is NotificationEvent.SERVER_CRASHED
        assert notifier._queue[0].server_name == "survie"

    def test_a_failed_backup_becomes_a_notification(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.server_topic(1, topics.BACKUP),
            {"server": "survie", "status": "FAILED", "error": "disque plein"},
        )

        assert notifier._queue[0].event is NotificationEvent.BACKUP_FAILED
        assert "disque plein" in notifier._queue[0].detail

    def test_progress_lines_are_not_notified(self) -> None:
        """Une sauvegarde en cours publie des dizaines de messages : aucun n'est un fait."""
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.server_topic(1, topics.BACKUP),
            {"server": "survie", "status": "RUNNING", "percent": 42},
        )

        assert notifier._queue == []


def _status(notifier: Notifier, state: str, reason: str = "", server_id: int = 1) -> None:
    notifier._collect(
        topics.server_topic(server_id, topics.STATUS),
        {"id": server_id, "name": "survie", "state": state, "state_reason": reason},
    )


class TestStartAndStop:
    def test_a_completed_start_is_announced_with_who_asked(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "OFFLINE")
        _status(notifier, "STARTING", "Start requested by flavien")
        _status(notifier, "ONLINE", "Start-up complete.")

        assert [item.event for item in notifier._queue] == [NotificationEvent.SERVER_STARTED]
        assert notifier._queue[0].server_name == "survie"
        assert notifier._queue[0].detail == "Start requested by flavien"

    def test_a_requested_stop_is_announced_with_who_asked(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "ONLINE")
        _status(notifier, "STOPPING", "Stop requested by flavien")
        _status(notifier, "OFFLINE", "Server stopped.")

        assert [item.event for item in notifier._queue] == [NotificationEvent.SERVER_STOPPED]
        assert notifier._queue[0].detail == "Stop requested by flavien"

    def test_a_server_stopping_on_its_own_is_announced(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "ONLINE")
        _status(notifier, "OFFLINE", "The server stopped on its own (code 0).")

        assert [item.event for item in notifier._queue] == [NotificationEvent.SERVER_STOPPED]
        assert "on its own" in notifier._queue[0].detail

    def test_msm_restarting_announces_nothing(self) -> None:
        """Réadopté au lancement de MSM, détaché à son arrêt : ni démarré, ni arrêté."""
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "ONLINE")  # premier état vu : aucune transition
        _status(notifier, "UNKNOWN", "MSM stopped; server detached.")
        _status(notifier, "UNKNOWN", "Re-adopted after an MSM restart.")
        _status(notifier, "ONLINE")

        assert notifier._queue == []

    def test_a_crash_is_not_also_a_stop(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "ONLINE")
        _status(notifier, "CRASHED", "Exit code 1")

        assert notifier._queue == []

    def test_a_failed_start_is_not_a_start(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "STARTING", "Start requested by flavien")
        _status(notifier, "OFFLINE", "Java not found")

        assert notifier._queue == []

    def test_servers_are_tracked_separately(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        _status(notifier, "STARTING", server_id=1)
        _status(notifier, "ONLINE", server_id=2)
        _status(notifier, "ONLINE", server_id=1)

        assert [item.event for item in notifier._queue] == [NotificationEvent.SERVER_STARTED]


class TestScheduledTasks:
    def test_a_failed_task_is_announced(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.server_topic(1, topics.SCHEDULE),
            {
                "server": "survie",
                "task": "Nightly backup",
                "status": "FAILED",
                "error": "disk full",
            },
        )

        assert [item.event for item in notifier._queue] == [NotificationEvent.SCHEDULE_FAILED]
        assert "Nightly backup" in notifier._queue[0].detail
        assert "disk full" in notifier._queue[0].detail

    def test_a_successful_task_is_not_announced(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.server_topic(1, topics.SCHEDULE),
            {"server": "survie", "task": "Nightly backup", "status": "SUCCESS", "error": None},
        )

        assert notifier._queue == []


class TestRouting:
    """Chaque serveur a son salon ; les événements de MSM vont au salon global."""

    @pytest.mark.asyncio
    async def test_each_batch_goes_to_its_own_channel(self, monkeypatch) -> None:
        sent: list[tuple[str, str]] = []

        async def fake_send(url: str, content: str, **kwargs) -> bool:
            sent.append((url, content))
            return True

        monkeypatch.setattr("msm.services.notifier.send_to_discord", fake_send)
        webhooks = {1: f"{WEBHOOK}-survie", 2: f"{WEBHOOK}-creatif", None: f"{WEBHOOK}-global"}

        async def settings(server_id: int | None) -> dict:
            return {
                "enabled": True,
                "webhook_url": webhooks[server_id],
                "events": [event.value for event in NotificationEvent],
            }

        notifier = Notifier(EventBus(), settings)
        notifier._collect(topics.server_topic(1, topics.CRASH), {"server": "survie"})
        notifier._collect(topics.server_topic(2, topics.CRASH), {"server": "creatif"})
        notifier._collect(
            topics.server_topic(1, topics.BACKUP), {"server": "survie", "status": "COMPLETED"}
        )
        notifier._collect(
            topics.system_topic(topics.SERVER_CREATED), {"server": "lobby", "actor": "flavien"}
        )

        assert await notifier.flush() is True
        by_channel = dict(sent)
        assert len(sent) == 3
        assert "survie" in by_channel[webhooks[1]]
        # Plantage et sauvegarde de « survie » : deux lignes, un seul message.
        assert len(by_channel[webhooks[1]].splitlines()) == 2
        assert "creatif" in by_channel[webhooks[2]]
        assert "lobby" in by_channel[webhooks[None]]
        assert "flavien" in by_channel[webhooks[None]]

    @pytest.mark.asyncio
    async def test_a_server_without_webhook_sends_nothing(self, monkeypatch) -> None:
        sent: list[str] = []

        async def fake_send(url: str, content: str, **kwargs) -> bool:
            sent.append(url)
            return True

        monkeypatch.setattr("msm.services.notifier.send_to_discord", fake_send)

        async def settings(server_id: int | None) -> dict:
            if server_id == 1:
                return {"enabled": True, "webhook_url": WEBHOOK, "events": ["server_crashed"]}
            return {"enabled": False, "webhook_url": None, "events": []}

        notifier = Notifier(EventBus(), settings)
        notifier._collect(topics.server_topic(1, topics.CRASH), {"server": "survie"})
        notifier._collect(topics.server_topic(2, topics.CRASH), {"server": "creatif"})

        assert await notifier.flush() is True
        assert sent == [WEBHOOK]

    def test_creation_and_deletion_are_global_events(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(
            topics.system_topic(topics.SERVER_CREATED), {"server": "lobby", "actor": "flavien"}
        )
        notifier._collect(
            topics.system_topic(topics.SERVER_DELETED), {"server": "lobby", "actor": "flavien"}
        )

        assert [item.event for item in notifier._queue] == [
            NotificationEvent.SERVER_CREATED,
            NotificationEvent.SERVER_DELETED,
        ]
        assert all(item.server_id is None for item in notifier._queue)

    def test_server_events_carry_their_server(self) -> None:
        notifier = Notifier(EventBus(), lambda _server_id: {})

        notifier._collect(topics.server_topic(7, topics.CRASH), {"server": "survie"})

        assert notifier._queue[0].server_id == 7


@pytest.mark.asyncio
class TestSubscription:
    async def test_events_published_right_after_start_are_not_missed(self, monkeypatch) -> None:
        """Les serveurs en démarrage automatique partent juste après le notifier.

        L'abonnement se faisait dans la tâche de fond, au premier tour de boucle :
        un démarrage publié entre-temps était perdu, et jamais annoncé.
        """
        monkeypatch.setattr("msm.services.notifier.BATCH_WINDOW_S", 0)
        bus = EventBus()
        notifier = Notifier(bus, lambda _server_id: {})
        flushed: list[NotificationEvent] = []

        async def fake_flush() -> bool:
            flushed.extend(item.event for item in notifier._queue)
            notifier._queue.clear()
            return True

        monkeypatch.setattr(notifier, "flush", fake_flush)

        notifier.start()
        topic = topics.server_topic(1, topics.STATUS)
        bus.publish(topic, {"id": 1, "name": "survie", "state": "OFFLINE"})
        bus.publish(
            topic,
            {"id": 1, "name": "survie", "state": "STARTING", "state_reason": "autostart"},
        )
        bus.publish(topic, {"id": 1, "name": "survie", "state": "ONLINE"})
        # La boucle traite ce qu'elle a reçu, puis on l'arrête.
        for _ in range(50):
            if flushed:
                break
            await asyncio.sleep(0.02)
        await notifier.stop()
        bus.close()

        assert flushed == [NotificationEvent.SERVER_STARTED]
