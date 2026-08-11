"""Nommage des sujets du bus d'événements.

Convention : ``<domaine>.<identifiant>.<événement>``. Les fonctions ci-dessous
évitent de disperser des chaînes littérales dans le code — une faute de frappe
dans un sujet produirait un abonnement silencieusement vide.
"""

from __future__ import annotations

from typing import Final

SERVER: Final = "server"
SYSTEM: Final = "system"

# --- Événements serveur ----------------------------------------------------
STATUS: Final = "status"
LOG: Final = "log"
STATS: Final = "stats"
PLAYERS: Final = "players"
PLAYER_JOIN: Final = "player_join"
PLAYER_LEAVE: Final = "player_leave"
CRASH: Final = "crash"
RESTART_SCHEDULED: Final = "restart_scheduled"
EVENT_RUN: Final = "event_run"

# --- Événements système ----------------------------------------------------
SYSTEM_STATS: Final = "stats"
NOTIFICATION: Final = "notification"


def server_topic(server_id: int, event: str) -> str:
    """Sujet d'un événement propre à un serveur."""
    return f"{SERVER}.{server_id}.{event}"


def server_pattern(server_id: int) -> str:
    """Préfixe couvrant tous les événements d'un serveur."""
    return f"{SERVER}.{server_id}."


def system_topic(event: str) -> str:
    """Sujet d'un événement global."""
    return f"{SYSTEM}.{event}"
