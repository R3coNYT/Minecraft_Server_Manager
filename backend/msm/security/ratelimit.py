"""Limitation de débit en mémoire, par clé (adresse IP en général).

Suffisant pour un MSM seul sur sa machine : la mémoire du processus est le seul
endroit où passent toutes les requêtes. Une fenêtre glissante, plutôt qu'un
compteur remis à zéro à heure fixe, évite qu'un robot n'enchaîne deux rafales
de part et d'autre de la remise à zéro.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Compte une tentative ; ``False`` si la clé a épuisé son quota."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window_s:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        # Pas d'accumulation infinie de clés oubliées : on purge les vides.
        if len(self._hits) > 10_000:
            self._hits = {key: value for key, value in self._hits.items() if value}
        return True

    def retry_after(self, key: str) -> int:
        """Secondes avant la prochaine tentative autorisée pour cette clé."""
        hits = self._hits.get(key)
        if not hits:
            return 0
        return max(1, int(hits[0] + self._window_s - self._clock()) + 1)

    def reset(self) -> None:
        self._hits.clear()
