"""Notifications Discord.

Un panneau ne sert à rien quand personne ne le regarde. Les faits qui méritent
une réaction — un serveur qui plante à 3 h, une sauvegarde qui échoue — doivent
aller chercher l'administrateur, pas l'attendre.

Trois précautions, parce qu'une intégration sortante mal élevée finit toujours
par être coupée :

* **rien de ce qui se passe ici ne peut faire échouer autre chose.** Un webhook
  injoignable est journalisé, jamais propagé ;
* **les messages sont regroupés** avant envoi. Un serveur en boucle de
  redémarrage produirait sinon des dizaines d'appels par minute, et Discord
  limiterait — ou bannirait — le webhook ;
* **l'URL du webhook est un secret** : elle permet à quiconque la détient
  d'écrire dans le salon. Elle est chiffrée en base et n'est jamais renvoyée en
  clair par l'API.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx

from msm.bus import EventBus, topics
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Discord tolère environ 5 requêtes par seconde et par webhook ; une fenêtre de
#: regroupement de 3 s reste imperceptible pour une alerte.
BATCH_WINDOW_S = 3.0
#: Au-delà, le message deviendrait illisible : le reste est résumé en une ligne.
MAX_LINES_PER_MESSAGE = 10
REQUEST_TIMEOUT_S = 10.0
#: Deux tentatives : au-delà, c'est le webhook qui est en cause, pas le réseau.
MAX_ATTEMPTS = 2


class NotificationEvent(str, Enum):
    """Faits notifiables, cochés un à un dans les réglages."""

    SERVER_CRASHED = "server_crashed"
    SERVER_RESTARTED = "server_restarted"
    SERVER_STARTED = "server_started"
    SERVER_STOPPED = "server_stopped"
    BACKUP_FAILED = "backup_failed"
    BACKUP_COMPLETED = "backup_completed"
    SCHEDULE_FAILED = "schedule_failed"


#: Ceux qui sont cochés par défaut : les ennuis, pas la routine.
DEFAULT_EVENTS: tuple[NotificationEvent, ...] = (
    NotificationEvent.SERVER_CRASHED,
    NotificationEvent.SERVER_RESTARTED,
    NotificationEvent.BACKUP_FAILED,
    NotificationEvent.SCHEDULE_FAILED,
)

LABELS: dict[NotificationEvent, str] = {
    NotificationEvent.SERVER_CRASHED: "Plantage d'un serveur",
    NotificationEvent.SERVER_RESTARTED: "Redémarrage automatique",
    NotificationEvent.SERVER_STARTED: "Démarrage d'un serveur",
    NotificationEvent.SERVER_STOPPED: "Arrêt d'un serveur",
    NotificationEvent.BACKUP_FAILED: "Échec d'une sauvegarde",
    NotificationEvent.BACKUP_COMPLETED: "Sauvegarde terminée",
    NotificationEvent.SCHEDULE_FAILED: "Échec d'une tâche programmée",
}

#: Emoji ouvrant la ligne : dans un salon, la couleur se lit avant le texte.
_ICONS: dict[NotificationEvent, str] = {
    NotificationEvent.SERVER_CRASHED: "💥",
    NotificationEvent.SERVER_RESTARTED: "🔁",
    NotificationEvent.SERVER_STARTED: "▶️",
    NotificationEvent.SERVER_STOPPED: "⏹️",
    NotificationEvent.BACKUP_FAILED: "⚠️",
    NotificationEvent.BACKUP_COMPLETED: "💾",
    NotificationEvent.SCHEDULE_FAILED: "⚠️",
}


@dataclass(frozen=True, slots=True)
class Notification:
    """Un fait à annoncer."""

    event: NotificationEvent
    server_name: str
    detail: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def render(self) -> str:
        icon = _ICONS.get(self.event, "•")
        moment = self.ts.strftime("%H:%M")
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{icon} `{moment}` **{self.server_name}** · {LABELS[self.event]}{suffix}"


def render_batch(items: list[Notification]) -> str:
    """Assemble un lot en un seul message, borné en longueur."""
    lines = [item.render() for item in items[:MAX_LINES_PER_MESSAGE]]
    extra = len(items) - len(lines)
    if extra > 0:
        lines.append(f"… et {extra} autre{'s' if extra > 1 else ''} événement(s).")
    return "\n".join(lines)


async def send_to_discord(
    webhook_url: str, content: str, *, client: httpx.AsyncClient | None = None
) -> bool:
    """Envoie un message. Renvoie `False` en cas d'échec, sans jamais lever."""
    owned = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await http.post(
                    webhook_url,
                    json={"content": content, "allowed_mentions": {"parse": []}},
                )
            except httpx.HTTPError as exc:
                logger.warning("discord_request_failed", attempt=attempt, error=str(exc))
            else:
                if response.status_code in (200, 204):
                    return True
                if response.status_code == 429:
                    # Discord dit combien de temps attendre : on l'écoute.
                    delay = _retry_after(response)
                    logger.warning("discord_rate_limited", retry_after=delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "discord_rejected",
                    status=response.status_code,
                    body=response.text[:200],
                )
                # 4xx hors 429 : réessayer ne changera rien.
                if 400 <= response.status_code < 500:
                    return False
            await asyncio.sleep(1.0)
        return False
    finally:
        if owned:
            await http.aclose()


def _retry_after(response: httpx.Response) -> float:
    with contextlib.suppress(ValueError, TypeError):
        return min(float(response.headers.get("retry-after", 1.0)), 30.0)
    return 1.0  # pragma: no cover - en-tête absent ou illisible


class Notifier:
    """Consommateur du bus qui alimente le salon Discord.

    Il ne connaît ni les serveurs ni la base : il reçoit des faits, applique les
    réglages, et poste. Couper les notifications revient à ne pas le démarrer.
    """

    _WAKE_TOPIC = "system.__notifier_wake__"

    def __init__(self, bus: EventBus, settings_loader: Any) -> None:
        #: Fonction asynchrone renvoyant les réglages courants — relus à chaque
        #: envoi, pour qu'un changement s'applique sans redémarrage.
        self._load_settings = settings_loader
        self._bus = bus
        self._queue: list[Notification] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="msm-notifier")

    async def stop(self, *, timeout: float = 5.0) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._bus.publish(self._WAKE_TOPIC, None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    def notify(self, notification: Notification) -> None:
        """Met un fait en file. Non bloquant, appelable depuis n'importe où."""
        self._queue.append(notification)

    async def _run(self) -> None:
        subscription = self._bus.subscribe("server.", "system.")
        try:
            async for event in subscription:
                if self._stop.is_set():
                    break
                self._collect(event.topic, event.payload)
                if self._queue:
                    # Fenêtre de regroupement : ce qui arrive dans les trois
                    # secondes part dans le même message.
                    await asyncio.sleep(BATCH_WINDOW_S)
                    await self.flush()
        except asyncio.CancelledError:
            raise
        finally:
            subscription.close()
            with contextlib.suppress(Exception):
                await self.flush()

    def _collect(self, topic: str, payload: Any) -> None:
        """Traduit un événement du bus en fait notifiable, s'il en est un."""
        suffix = topic.rsplit(".", 1)[-1]
        if not isinstance(payload, dict):
            return

        # Les événements du runtime nomment le serveur « server » ; le statut
        # complet, publié ailleurs, dit « name ». Les deux sont acceptés.
        name = payload.get("server") or payload.get("name") or "serveur"

        if suffix == topics.CRASH:
            self.notify(
                Notification(
                    NotificationEvent.SERVER_CRASHED,
                    str(name),
                    str(payload.get("reason") or payload.get("last_error") or ""),
                )
            )
        elif suffix == topics.RESTART_SCHEDULED:
            delay = payload.get("delay_s")
            self.notify(
                Notification(
                    NotificationEvent.SERVER_RESTARTED,
                    str(name),
                    f"redémarrage dans {delay} s" if delay else "",
                )
            )
        elif suffix == topics.BACKUP:
            status = payload.get("status")
            if status == "FAILED":
                self.notify(
                    Notification(
                        NotificationEvent.BACKUP_FAILED,
                        str(name),
                        str(payload.get("error") or ""),
                    )
                )
            elif status == "COMPLETED":
                self.notify(Notification(NotificationEvent.BACKUP_COMPLETED, str(name)))

    async def flush(self) -> bool:
        """Envoie ce qui est en file. Renvoie `True` si un message est parti."""
        if not self._queue:
            return False

        settings = await self._load_settings()
        pending, self._queue = self._queue, []

        if not settings or not settings.get("enabled") or not settings.get("webhook_url"):
            return False

        selected = {
            NotificationEvent(value)
            for value in settings.get("events", [])
            if value in NotificationEvent._value2member_map_
        }
        retained = [item for item in pending if item.event in selected]
        if not retained:
            return False

        return await send_to_discord(settings["webhook_url"], render_batch(retained))
