"""Format des messages WebSocket.

Enveloppe commune à tous les messages ::

    {"t": "server.log", "sid": 3, "seq": 42, "ts": "…", "d": {…}}

* ``t``   — type du message ;
* ``sid`` — serveur concerné, ``null`` pour un message global ;
* ``seq`` — numéro de message **de la connexion**, à ne pas confondre avec le
  numéro de ligne de log, qui vit dans ``d`` ;
* ``ts``  — horodatage UTC ;
* ``d``   — charge utile.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Types de messages échangés."""

    # --- client → serveur ---
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"

    # --- serveur → client ---
    READY = "ready"
    PONG = "pong"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    ERROR = "error"

    SERVER_STATUS = "server.status"
    SERVER_LOG = "server.log"
    SERVER_STATS = "server.stats"
    SERVER_PLAYERS = "server.players"
    SERVER_PLAYER_JOIN = "server.player.join"
    SERVER_PLAYER_LEAVE = "server.player.leave"
    SERVER_CRASH = "server.crash"
    SERVER_RESTART_SCHEDULED = "server.restart.scheduled"
    EVENT_RUN = "event.run"

    LOG_TRUNCATED = "log.truncated"
    SYSTEM_STATS = "system.stats"
    NOTIFICATION = "notification"


#: Correspondance entre les sujets du bus et les types de messages sortants.
TOPIC_TO_MESSAGE: dict[str, MessageType] = {
    "status": MessageType.SERVER_STATUS,
    "log": MessageType.SERVER_LOG,
    "stats": MessageType.SERVER_STATS,
    "players": MessageType.SERVER_PLAYERS,
    "player_join": MessageType.SERVER_PLAYER_JOIN,
    "player_leave": MessageType.SERVER_PLAYER_LEAVE,
    "crash": MessageType.SERVER_CRASH,
    "restart_scheduled": MessageType.SERVER_RESTART_SCHEDULED,
    "event_run": MessageType.EVENT_RUN,
}

#: Canaux qu'un client peut demander, et sujets correspondants.
CHANNELS: dict[str, tuple[str, ...]] = {
    "status": ("status", "crash", "restart_scheduled"),
    "logs": ("log",),
    "stats": ("stats",),
    "players": ("players", "player_join", "player_leave"),
    "events": ("event_run",),
}


def envelope(
    message_type: MessageType,
    payload: Any,
    *,
    seq: int,
    server_id: int | None = None,
) -> dict[str, Any]:
    """Construit un message sortant."""
    return {
        "t": message_type.value,
        "sid": server_id,
        "seq": seq,
        "ts": datetime.now(UTC).isoformat(),
        "d": payload,
    }


def error_payload(
    code: str,
    message: str,
    *,
    cause: str | None = None,
    remediation: str | None = None,
) -> dict[str, Any]:
    """Erreur WebSocket, au même format que les erreurs HTTP."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if cause:
        payload["cause"] = cause
    if remediation:
        payload["remediation"] = remediation
    return payload
