"""Lecture de l'historique des ressources.

Les points bruts sont **agrégés en paliers** avant d'être renvoyés : sept jours
d'échantillons à 30 s font 20 160 points par serveur, que ni le réseau ni un
graphique de 600 pixels n'ont de raison de transporter. Le regroupement se fait
côté base, pas côté Python : ramener 20 000 lignes pour en produire 120 serait
exactement le gaspillage qu'on veut éviter.

L'agrégation retient le **maximum** de l'intervalle, pas la moyenne. Une pointe
à 100 % pendant une minute est précisément ce qu'on cherche quand on regarde
cette courbe ; une moyenne la lisserait jusqu'à la faire disparaître.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from msm.db.models.metrics import MetricSample
from msm.db.models.server import Server
from msm.exceptions import ValidationError

#: Fenêtres proposées, avec la largeur d'un palier d'agrégation.
RANGES: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=1)),
    "6h": (timedelta(hours=6), timedelta(minutes=5)),
    "24h": (timedelta(days=1), timedelta(minutes=15)),
    "7d": (timedelta(days=7), timedelta(hours=2)),
}


@dataclass(frozen=True, slots=True)
class MetricPoint:
    """Un palier agrégé."""

    ts: datetime
    cpu_percent: float
    memory_mb: float
    players_online: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "players_online": self.players_online,
        }


def _epoch_seconds(dialect: str) -> ColumnElement[Any]:
    """Nombre de secondes depuis 1970, dans le dialecte courant.

    Le calcul est fait par la base : c'est la seule façon de regrouper sans
    rapatrier tous les points. Aucune fonction n'est commune à SQLite et à
    PostgreSQL, d'où ces deux branches — explicites plutôt que déguisées.
    """
    if dialect == "postgresql":
        return func.extract("epoch", MetricSample.ts)
    # SQLite : `strftime('%s')` renvoie du texte, converti en entier.
    return cast(func.strftime("%s", MetricSample.ts), Integer)


class MetricsService:
    """Consultation de l'historique d'un serveur."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _dialect(self) -> str:
        bind = getattr(self._session, "bind", None)
        return getattr(getattr(bind, "dialect", None), "name", "sqlite")

    async def history(self, server: Server, *, range_key: str = "24h") -> dict[str, Any]:
        if range_key not in RANGES:
            raise ValidationError(
                "Période inconnue.",
                cause=f"« {range_key} » n'est pas une période reconnue.",
                remediation=f"Utiliser l'une de : {', '.join(RANGES)}.",
            )

        window, bucket = RANGES[range_key]
        since = datetime.now(UTC) - window
        seconds = int(bucket.total_seconds())

        slot = cast(_epoch_seconds(self._dialect()) / seconds, Integer)

        statement = (
            select(
                slot.label("slot"),
                func.max(MetricSample.cpu_percent),
                func.max(MetricSample.memory_mb),
                func.max(MetricSample.players_online),
            )
            .where(MetricSample.server_id == server.id, MetricSample.ts >= since)
            .group_by("slot")
            .order_by("slot")
        )
        rows = (await self._session.execute(statement)).all()

        points = [
            MetricPoint(
                ts=datetime.fromtimestamp(int(row[0]) * seconds, tz=UTC),
                cpu_percent=float(row[1] or 0.0),
                memory_mb=float(row[2] or 0.0),
                players_online=int(row[3] or 0),
            )
            for row in rows
        ]

        return {
            "range": range_key,
            "bucket_s": seconds,
            "points": [point.to_dict() for point in points],
            "peak_cpu_percent": round(max((p.cpu_percent for p in points), default=0.0), 1),
            "peak_memory_mb": round(max((p.memory_mb for p in points), default=0.0), 1),
            "peak_players": max((p.players_online for p in points), default=0),
        }
