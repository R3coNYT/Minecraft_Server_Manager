"""Tampon circulaire d'historique de console.

Un serveur très actif peut produire des milliers de lignes par seconde. Conserver
l'intégralité en mémoire ferait gonfler le panel sans limite — c'est précisément ce
qu'il faut éviter avec une dizaine de serveurs.

Le tampon garde donc les *N* dernières lignes et **compte** celles qu'il a écartées.
Cette information n'est pas cosmétique : elle permet à l'interface d'afficher
« 12 340 lignes antérieures non conservées » plutôt que de laisser croire à un
historique complet.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Iterator

from msm.core.log_line import LogLine


class RingBuffer:
    """File bornée de :class:`LogLine`, indexée par numéro de séquence."""

    __slots__ = ("_dropped", "_lines", "_maxlen")

    def __init__(self, maxlen: int = 5000) -> None:
        if maxlen < 1:
            raise ValueError("maxlen doit être strictement positif")
        self._maxlen = maxlen
        self._lines: deque[LogLine] = deque(maxlen=maxlen)
        self._dropped = 0

    # ------------------------------------------------------------------ #
    #  Écriture
    # ------------------------------------------------------------------ #
    def append(self, line: LogLine) -> None:
        if len(self._lines) == self._maxlen:
            self._dropped += 1
        self._lines.append(line)

    def extend(self, lines: Iterable[LogLine]) -> None:
        for line in lines:
            self.append(line)

    def clear(self) -> None:
        """Vide le tampon (redémarrage du serveur), en oubliant le compteur."""
        self._lines.clear()
        self._dropped = 0

    # ------------------------------------------------------------------ #
    #  Lecture
    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._lines)

    def __iter__(self) -> Iterator[LogLine]:
        return iter(self._lines)

    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def dropped(self) -> int:
        """Nombre de lignes sorties du tampon depuis le dernier ``clear()``."""
        return self._dropped

    @property
    def first_seq(self) -> int | None:
        return self._lines[0].seq if self._lines else None

    @property
    def last_seq(self) -> int | None:
        return self._lines[-1].seq if self._lines else None

    def tail(self, limit: int = 500) -> list[LogLine]:
        """Les ``limit`` dernières lignes, dans l'ordre chronologique."""
        if limit <= 0:
            return []
        if limit >= len(self._lines):
            return list(self._lines)
        return list(self._lines)[-limit:]

    def since(self, seq: int, *, limit: int | None = None) -> list[LogLine]:
        """Lignes dont le numéro est strictement supérieur à ``seq``.

        C'est le mécanisme de reprise après coupure WebSocket : le client renvoie
        le dernier numéro reçu et récupère exactement la suite.
        """
        result = [line for line in self._lines if line.seq > seq]
        return result[:limit] if limit is not None else result

    def before(self, seq: int, *, limit: int = 500) -> list[LogLine]:
        """Lignes précédant ``seq`` — défilement vers le haut dans la console."""
        if limit <= 0:
            return []
        older = [line for line in self._lines if line.seq < seq]
        return older[-limit:]

    def search(
        self,
        query: str,
        *,
        limit: int = 200,
        use_regex: bool = False,
        case_sensitive: bool = False,
    ) -> list[LogLine]:
        """Recherche dans l'historique conservé.

        Une expression régulière invalide ne lève pas : elle est traitée comme du
        texte littéral. Une console n'a pas à refuser une recherche parce qu'un
        caractère spécial a été saisi.
        """
        if not query:
            return []

        if use_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                use_regex = False
            else:
                matches = [line for line in self._lines if pattern.search(line.text)]
                return matches[-limit:]

        needle = query if case_sensitive else query.casefold()
        matches = [
            line
            for line in self._lines
            if (needle in (line.text if case_sensitive else line.text.casefold()))
        ]
        return matches[-limit:]

    def resize(self, maxlen: int) -> None:
        """Change la capacité en conservant les lignes les plus récentes."""
        if maxlen < 1:
            raise ValueError("maxlen doit être strictement positif")
        if maxlen == self._maxlen:
            return
        kept = list(self._lines)[-maxlen:]
        self._dropped += max(0, len(self._lines) - len(kept))
        self._maxlen = maxlen
        self._lines = deque(kept, maxlen=maxlen)
