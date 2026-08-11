"""Journalisation interne de MSM (structlog).

Les logs de MSM sont **strictement séparés** des logs des serveurs Minecraft :

* ``logs/msm.log``  → activité du panel (démarrages, erreurs, audit technique) ;
* les logs Minecraft restent dans le dossier de chaque serveur et transitent par
  le pipeline temps réel, jamais par ce logger.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Any

import structlog

from msm.config import Settings

_configured = False


def configure_logging(settings: Settings) -> None:
    """Configure structlog + la stdlib. Idempotent."""
    global _configured
    if _configured:
        return

    settings.ensure_directories()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        settings.msm_log_file,
        when="midnight",
        backupCount=settings.log_retention_days,
        encoding="utf-8",
        delay=True,
    )
    # Le fichier est toujours en JSON : exploitable par un agrégateur de logs.
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # Uvicorn tient ses propres loggers : on les fait passer par le nôtre.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Logger structuré nommé (convention : ``__name__`` du module appelant)."""
    return structlog.stdlib.get_logger(name)  # type: ignore[no-any-return]


def reset_logging() -> None:
    """Réinitialise l'état — réservé aux tests."""
    global _configured
    _configured = False
