"""Pipeline de traitement des logs d'un serveur.

Chaîne complète, exécutée pour chaque ligne produite par le processus ::

    sortie du processus → analyse → numérotation → historique → détection → bus

La **numérotation** est le pivot du temps réel : chaque ligne reçoit un numéro
monotone propre au serveur. Un client WebSocket qui perd la connexion renvoie le
dernier numéro reçu et récupère exactement la suite, sans trou ni doublon — ce
qu'un simple flux poussé ne permettrait pas.
"""

from __future__ import annotations

from collections.abc import Callable

from msm.core.log_line import LineSource, LogLevel, LogLine, make_system_line, parse_line
from msm.core.patterns import MinecraftEvent, detect_events
from msm.runtime.ring_buffer import RingBuffer

LineCallback = Callable[[LogLine], None]
EventCallback = Callable[[MinecraftEvent], None]


class LogPipeline:
    """Transforme les lignes brutes d'un serveur en lignes exploitables.

    L'objet est purement synchrone : il ne lit rien lui-même et ne connaît ni
    asyncio ni les WebSockets. C'est le runtime qui l'alimente.
    """

    __slots__ = ("_buffer", "_on_event", "_on_line", "_seq")

    def __init__(
        self,
        buffer: RingBuffer,
        *,
        on_line: LineCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self._buffer = buffer
        self._on_line = on_line
        self._on_event = on_event
        self._seq = 0

    @property
    def buffer(self) -> RingBuffer:
        return self._buffer

    @property
    def last_seq(self) -> int:
        return self._seq

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ------------------------------------------------------------------ #
    def ingest(self, raw: str, *, source: LineSource = LineSource.STDOUT) -> LogLine:
        """Traite une ligne brute issue du processus."""
        line = parse_line(raw, seq=self.next_seq(), source=source)
        return self._dispatch(line, detect=True)

    def emit_system(
        self,
        text: str,
        *,
        level: LogLevel = LogLevel.INFO,
        source: LineSource = LineSource.MSM,
    ) -> LogLine:
        """Insère une ligne produite par MSM (annonce d'action, diagnostic).

        Ces lignes traversent l'historique et le bus comme les autres, mais ne
        sont **pas** soumises à la détection d'événements : MSM ne doit pas
        déclencher une arrivée de joueur en écrivant « Flavien joined the game ».
        """
        line = make_system_line(text, seq=self.next_seq(), source=source, level=level)
        return self._dispatch(line, detect=False)

    def echo_command(self, command: str, *, actor: str | None = None) -> LogLine:
        """Trace dans la console une commande envoyée depuis le panel."""
        suffix = f"  ({actor})" if actor else ""
        return self.emit_system(
            f"> {command}{suffix}", level=LogLevel.INFO, source=LineSource.COMMAND
        )

    # ------------------------------------------------------------------ #
    def _dispatch(self, line: LogLine, *, detect: bool) -> LogLine:
        self._buffer.append(line)

        if self._on_line is not None:
            self._on_line(line)

        if detect and self._on_event is not None:
            for event in detect_events(line):
                self._on_event(event)

        return line

    def reset(self) -> None:
        """Vide l'historique au redémarrage d'un serveur.

        Le compteur de séquence n'est **pas** remis à zéro : des numéros réutilisés
        feraient croire à un client reconnecté qu'il a déjà reçu les nouvelles
        lignes, et la console resterait figée.
        """
        self._buffer.clear()
