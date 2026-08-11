"""Types de colonnes personnalisés.

SQLite ne stocke pas les fuseaux horaires : une valeur écrite avec un fuseau est
relue **sans**. Comparer une date relue à ``datetime.now(UTC)`` lève alors
« can't compare offset-naive and offset-aware datetimes », de façon imprévisible
selon le moteur utilisé.

Plutôt que de parsemer le code de gardes défensives, la normalisation est faite
une fois pour toutes au niveau du type : ce qui entre est converti en UTC, ce qui
sort porte toujours le fuseau UTC. PostgreSQL, qui gère nativement les fuseaux,
n'en est pas affecté.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Date et heure toujours manipulées en UTC, avec fuseau explicite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Une date naïve fournie par le code applicatif est réputée UTC :
            # MSM n'écrit jamais d'heure locale.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
