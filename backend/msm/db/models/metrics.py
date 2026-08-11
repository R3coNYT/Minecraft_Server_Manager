"""Historique des ressources consommées par chaque serveur.

Les statistiques temps réel ne vivent qu'à l'instant présent : elles répondent à
« combien maintenant », jamais à « pourquoi ça ramait cette nuit ». Cette table
conserve un point par serveur et par intervalle, et se purge d'elle-même.

Le stockage reste volontairement plat — une ligne, cinq nombres. Une base de
séries temporelles serait la bonne réponse à un million de serveurs ; pour
quelques dizaines, une table SQLite indexée l'est tout autant, sans dépendance
supplémentaire à installer sur la machine de l'utilisateur.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base
from msm.db.types import UtcDateTime


class MetricSample(Base):
    """Un relevé de ressources pour un serveur, à un instant donné."""

    __tablename__ = "metric_samples"
    __table_args__ = (
        # Toutes les lectures filtrent sur (serveur, période) : l'index composite
        # évite un parcours complet dès quelques dizaines de milliers de points.
        Index("ix_metric_samples_server_ts", "server_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    memory_mb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    players_online: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Conservé pour distinguer « 0 % parce qu'inactif » de « 0 % parce qu'arrêté ».
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
