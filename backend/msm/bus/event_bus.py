"""Bus d'événements interne (publication / abonnement en mémoire).

Il découple les producteurs (gestionnaire de processus, suivi des joueurs) des
consommateurs (diffuseur WebSocket, journal d'audit). Le runtime n'a ainsi aucune
connaissance de l'existence de WebSockets, et reste testable isolément.

Deux propriétés délibérées :

* **la publication ne bloque jamais**. Elle est appelée depuis le chemin critique
  de lecture des logs : un client lent ne doit pas ralentir la lecture de la
  sortie d'un serveur.
* **un abonné saturé perd les événements les plus anciens**, et le nombre de
  pertes est compté. Une console qui a pris du retard doit sauter des lignes et
  le dire, pas faire enfler la mémoire du panel jusqu'à l'étouffement.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Capacité par défaut de la file d'un abonné.
DEFAULT_QUEUE_SIZE = 2000


@dataclass(frozen=True, slots=True)
class Event:
    """Message circulant sur le bus."""

    topic: str
    payload: Any
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


class Subscription:
    """Abonnement à un ou plusieurs sujets.

    Un sujet se terminant par ``.`` agit comme préfixe : ``server.3.`` capte tous
    les événements du serveur 3.
    """

    __slots__ = ("_bus", "_closed", "_dropped", "_queue", "_topics")

    def __init__(self, bus: EventBus, topics: frozenset[str], maxsize: int) -> None:
        self._bus = bus
        self._topics = topics
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0
        self._closed = False

    @property
    def topics(self) -> frozenset[str]:
        return self._topics

    @property
    def dropped(self) -> int:
        """Événements écartés faute de place depuis la création de l'abonnement."""
        return self._dropped

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def matches(self, topic: str) -> bool:
        for subscribed in self._topics:
            if subscribed.endswith("."):
                if topic.startswith(subscribed):
                    return True
            elif topic == subscribed:
                return True
        return False

    def _deliver(self, event: Event) -> None:
        """Dépose un événement — jamais bloquant, évince le plus ancien si plein."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Évince le plus ancien pour faire place au plus récent.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._queue.task_done()
            self._dropped += 1
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                self._dropped += 1

    async def get(self) -> Event:
        """Attend le prochain événement."""
        return await self._queue.get()

    def take_dropped(self) -> int:
        """Lit et remet à zéro le compteur de pertes (pour le signaler au client)."""
        dropped, self._dropped = self._dropped, 0
        return dropped

    def close(self) -> None:
        """Se désabonne. Idempotent."""
        if not self._closed:
            self._closed = True
            self._bus.unsubscribe(self)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Event]:
        try:
            while not self._closed:
                yield await self._queue.get()
        finally:
            self.close()


class EventBus:
    """Bus en mémoire, propre au processus MSM."""

    __slots__ = ("_subscriptions",)

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def subscribe(self, *topics: str, maxsize: int = DEFAULT_QUEUE_SIZE) -> Subscription:
        """Crée un abonnement. Le fermer libère la ressource."""
        if not topics:
            raise ValueError("Au moins un sujet doit être fourni.")
        subscription = Subscription(self, frozenset(topics), maxsize)
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with contextlib.suppress(ValueError):
            self._subscriptions.remove(subscription)

    def publish(self, topic: str, payload: Any) -> None:
        """Publie un événement. Ne bloque pas, ne lève pas."""
        if not self._subscriptions:
            return
        event = Event(topic=topic, payload=payload)
        for subscription in self._subscriptions:
            if subscription.matches(topic):
                subscription._deliver(event)

    def has_subscribers(self, topic: str) -> bool:
        """Y a-t-il au moins un abonné à ce sujet ?

        Permet de ne pas produire de données coûteuses (statistiques, sérialisation)
        quand personne ne regarde.
        """
        return any(subscription.matches(topic) for subscription in self._subscriptions)

    def close(self) -> None:
        """Ferme tous les abonnements — utilisé à l'arrêt de l'application."""
        for subscription in list(self._subscriptions):
            subscription.close()
        self._subscriptions.clear()


#: Bus applicatif partagé. Injecté explicitement dans les tests.
_bus = EventBus()


def get_event_bus() -> EventBus:
    """Bus d'événements de l'application."""
    return _bus
