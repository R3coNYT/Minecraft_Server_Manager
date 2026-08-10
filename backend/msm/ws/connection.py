"""Une connexion WebSocket et sa boucle d'émission.

Points de conception :

* **une seule file par connexion**, aux sujets modifiables. Suivre trois serveurs
  ne crée pas trois files à surveiller en parallèle.
* **regroupement temporel** : les lignes de log qui arrivent dans la même fenêtre
  (100 ms par défaut) partent dans un seul message. Un serveur qui crache
  5 000 lignes au démarrage ne produit pas 5 000 trames.
* **reprise sans doublon** : après un `subscribe` avec ``resume_from``,
  l'historique manquant est envoyé, puis les lignes déjà transmises sont filtrées
  du flux en direct grâce à un curseur par serveur.
* **droits revérifiés à chaque abonnement**, pas seulement à la connexion : une
  session WebSocket peut durer des heures, pendant lesquelles un compte peut être
  désactivé ou changer de rôle.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from msm.bus import EventBus, Subscription
from msm.bus import topics as bus_topics
from msm.config import Settings
from msm.core.log_line import LogLine
from msm.core.permissions import Permission
from msm.db.repositories import ServerPermissionRepository, UserRepository
from msm.db.session import session_scope
from msm.exceptions import MsmError, PermissionDenied
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext, build_context
from msm.ws.messages import CHANNELS, TOPIC_TO_MESSAGE, MessageType, envelope, error_payload

logger = get_logger(__name__)

#: Lignes d'historique renvoyées par défaut à l'abonnement.
DEFAULT_BACKLOG = 300
MAX_BACKLOG = 2000


class WebSocketConnection:
    """Gère le dialogue avec un client connecté."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        user_id: int,
        username: str,
        supervisor: Supervisor,
        bus: EventBus,
        settings: Settings,
    ) -> None:
        self._ws = websocket
        self._user_id = user_id
        self._username = username
        self._supervisor = supervisor
        self._bus = bus
        self._settings = settings

        self._seq = 0
        self._closed = False
        self._subscription: Subscription | None = None
        #: Dernier numéro de ligne déjà transmis, par serveur.
        self._log_cursor: dict[int, int] = {}
        self._subscribed: set[int] = set()

    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Boucle principale : lecture des messages client, émission en parallèle."""
        # Les événements globaux sont toujours suivis ; les serveurs s'ajoutent
        # à la demande via `subscribe`.
        self._subscription = self._bus.subscribe("system.", maxsize=4000)
        writer = asyncio.create_task(self._writer(), name=f"ws-writer-{self._user_id}")

        await self._send(MessageType.READY, {"user": self._username})

        try:
            while True:
                message = await self._ws.receive_json()
                await self._handle(message)
        except WebSocketDisconnect:
            pass
        except (ValueError, TypeError):
            await self._send_error("INVALID_MESSAGE", "Message illisible.")
        except RuntimeError:
            # Connexion refermée pendant la lecture : sortie normale.
            pass
        finally:
            self._closed = True
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer
            if self._subscription is not None:
                self._subscription.close()

    # ------------------------------------------------------------------ #
    #  Réception
    # ------------------------------------------------------------------ #
    async def _handle(self, message: Any) -> None:
        if not isinstance(message, dict):
            await self._send_error("INVALID_MESSAGE", "Le message doit être un objet JSON.")
            return

        message_type = message.get("t")
        data = message.get("d") or {}

        try:
            if message_type == MessageType.PING.value:
                await self._send(MessageType.PONG, {})
            elif message_type == MessageType.SUBSCRIBE.value:
                await self._subscribe(data)
            elif message_type == MessageType.UNSUBSCRIBE.value:
                await self._unsubscribe(data)
            else:
                await self._send_error(
                    "UNKNOWN_MESSAGE",
                    f"Type de message inconnu : {message_type!r}.",
                    remediation="Types acceptés : subscribe, unsubscribe, ping.",
                )
        except MsmError as exc:
            await self._send_error(
                exc.code, exc.message, cause=exc.cause, remediation=exc.remediation
            )

    async def _subscribe(self, data: dict[str, Any]) -> None:
        server_id = data.get("server_id")
        if not isinstance(server_id, int):
            await self._send_error(
                "INVALID_SUBSCRIPTION",
                "Identifiant de serveur manquant.",
                remediation="Fournir `server_id` dans la charge utile.",
            )
            return

        context = await self._authorize(server_id)
        context.require(Permission.SERVER_VIEW, action="suivre ce serveur")

        # Un serveur inexistant ou hors supervision doit être signalé tout de
        # suite : sans cela, le client attendrait indéfiniment des événements
        # que personne ne publiera jamais.
        runtime = self._supervisor.find(server_id)
        if runtime is None:
            await self._send_error(
                "NOT_FOUND",
                "Serveur introuvable.",
                cause=f"Aucun serveur supervisé ne porte l'identifiant {server_id}.",
                remediation="Rafraîchir la liste des serveurs.",
            )
            return

        requested = data.get("channels") or list(CHANNELS)
        channels = [channel for channel in requested if channel in CHANNELS]
        if "logs" in channels:
            context.require(Permission.CONSOLE_READ, action="suivre la console")

        assert self._subscription is not None
        for channel in channels:
            for event in CHANNELS[channel]:
                self._subscription.add_topic(bus_topics.server_topic(server_id, event))
        self._subscribed.add(server_id)

        await self._send(
            MessageType.SUBSCRIBED,
            {"server_id": server_id, "channels": channels},
            server_id=server_id,
        )

        if "status" in channels:
            await self._send(MessageType.SERVER_STATUS, runtime.snapshot(), server_id=server_id)

        if "logs" in channels:
            await self._send_backlog(server_id, data.get("resume_from"))

    async def _send_backlog(self, server_id: int, resume_from: Any) -> None:
        """Envoie l'historique manquant et positionne le curseur anti-doublon."""
        runtime = self._supervisor.get(server_id)
        if isinstance(resume_from, int) and resume_from >= 0:
            lines = runtime.logs_since(resume_from, limit=MAX_BACKLOG)
        else:
            lines = runtime.logs_tail(DEFAULT_BACKLOG)

        snapshot = runtime.snapshot()
        if lines:
            self._log_cursor[server_id] = lines[-1].seq
            await self._send(
                MessageType.SERVER_LOG,
                {"lines": [line.to_dict() for line in lines], "backlog": True},
                server_id=server_id,
            )
        else:
            self._log_cursor[server_id] = snapshot["log_seq"]

        # Une reprise trop tardive ne peut pas être complète : le dire vaut mieux
        # que de laisser croire à un historique continu.
        first_available = runtime.logs_tail(1)
        if (
            isinstance(resume_from, int)
            and first_available
            and first_available[0].seq > resume_from + 1
        ):
            await self._send(
                MessageType.LOG_TRUNCATED,
                {
                    "missed": first_available[0].seq - resume_from - 1,
                    "reason": "Lignes sorties du tampon d'historique.",
                },
                server_id=server_id,
            )

    async def _unsubscribe(self, data: dict[str, Any]) -> None:
        server_id = data.get("server_id")
        if not isinstance(server_id, int) or self._subscription is None:
            return
        for events in CHANNELS.values():
            for event in events:
                self._subscription.remove_topic(bus_topics.server_topic(server_id, event))
        self._subscribed.discard(server_id)
        self._log_cursor.pop(server_id, None)
        await self._send(MessageType.UNSUBSCRIBED, {"server_id": server_id}, server_id=server_id)

    async def _authorize(self, server_id: int) -> AccessContext:
        """Recalcule les droits à partir de la base, à chaque abonnement."""
        async with session_scope() as session:
            user = await UserRepository(session).get(self._user_id)
            if user is None or not user.is_active:
                raise PermissionDenied(
                    "Compte désactivé.",
                    cause="Le compte associé à cette connexion n'est plus actif.",
                    remediation="Se reconnecter au panel.",
                )
            override = await ServerPermissionRepository(session).get(user.id, server_id)
            return build_context(user, server_id=server_id, override=override)

    # ------------------------------------------------------------------ #
    #  Émission
    # ------------------------------------------------------------------ #
    async def _writer(self) -> None:
        """Consomme le bus et regroupe les lignes de log avant émission."""
        assert self._subscription is not None
        subscription = self._subscription
        interval = self._settings.log_flush_interval_s
        max_lines = self._settings.log_flush_max_lines
        loop = asyncio.get_running_loop()

        while not self._closed:
            event = await subscription.get()
            batch = [event]
            deadline = loop.time() + interval

            # Fenêtre de regroupement : on accumule ce qui arrive juste après.
            while len(batch) < max_lines:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(subscription.get(), remaining))
                except TimeoutError:
                    break

            await self._flush(batch)

            if dropped := subscription.take_dropped():
                await self._send(
                    MessageType.LOG_TRUNCATED,
                    {"missed": dropped, "reason": "Client trop lent : événements écartés."},
                )

    async def _flush(self, batch: list[Any]) -> None:
        """Émet un lot d'événements, logs regroupés par serveur."""
        grouped_logs: dict[int, list[dict[str, Any]]] = {}

        for event in batch:
            server_id, suffix = _split_topic(event.topic)
            message_type = TOPIC_TO_MESSAGE.get(suffix)
            if message_type is None:
                continue

            if message_type is MessageType.SERVER_LOG and server_id is not None:
                line: LogLine = event.payload
                cursor = self._log_cursor.get(server_id, 0)
                if line.seq <= cursor:
                    continue  # déjà transmis dans l'historique de reprise
                self._log_cursor[server_id] = line.seq
                grouped_logs.setdefault(server_id, []).append(line.to_dict())
            else:
                await self._send(message_type, event.payload, server_id=server_id)

        for server_id, lines in grouped_logs.items():
            await self._send(MessageType.SERVER_LOG, {"lines": lines}, server_id=server_id)

    async def _send(
        self,
        message_type: MessageType,
        payload: Any,
        *,
        server_id: int | None = None,
    ) -> None:
        if self._closed:
            return
        self._seq += 1
        try:
            await self._ws.send_json(
                envelope(message_type, payload, seq=self._seq, server_id=server_id)
            )
        except (WebSocketDisconnect, RuntimeError):
            self._closed = True

    async def _send_error(
        self,
        code: str,
        message: str,
        *,
        cause: str | None = None,
        remediation: str | None = None,
    ) -> None:
        await self._send(
            MessageType.ERROR,
            error_payload(code, message, cause=cause, remediation=remediation),
        )


def _split_topic(topic: str) -> tuple[int | None, str]:
    """``server.3.log`` → ``(3, "log")`` ; ``system.stats`` → ``(None, "stats")``."""
    parts = topic.split(".")
    if len(parts) == 3 and parts[0] == "server":
        try:
            return int(parts[1]), parts[2]
        except ValueError:  # pragma: no cover - sujet malformé
            return None, parts[2]
    return None, parts[-1]
