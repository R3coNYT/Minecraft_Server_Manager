"""Point d'entrée de l'application FastAPI.

Une note importante sur le déploiement : MSM doit tourner en **un seul processus
applicatif** (`uvicorn --workers 1`). Le superviseur détient les tubes d'entrée et
de sortie des serveurs Minecraft ; avec plusieurs workers, chaque processus aurait
sa propre vision partielle et personne ne saurait quel worker possède quel serveur.
La montée en charge repose sur l'asynchrone, et plus tard sur des agents distants.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from msm import __version__
from msm.api.errors import register_error_handlers
from msm.api.v1 import api_router
from msm.bus import get_event_bus
from msm.bus import topics as bus_topics
from msm.config import Settings, get_settings
from msm.db.session import dispose_engine, init_engine, session_scope
from msm.logging_conf import configure_logging, get_logger
from msm.runtime.agent import LocalAgent
from msm.runtime.backends import get_backend
from msm.runtime.stats import system_stats
from msm.runtime.supervisor import Supervisor
from msm.services.server_service import ServerService
from msm.ws import websocket_router

logger = get_logger(__name__)


async def _publish_system_stats(settings: Settings) -> None:
    """Publie périodiquement les ressources de la machine, si quelqu'un écoute.

    Sans abonné, rien n'est mesuré ni sérialisé : un panel ouvert sur aucune page
    ne doit rien coûter.
    """
    bus = get_event_bus()
    topic = bus_topics.system_topic(bus_topics.SYSTEM_STATS)
    interval = max(settings.stats_interval_s, 1.0)
    while True:
        await asyncio.sleep(interval)
        if bus.has_subscribers(topic):
            bus.publish(topic, system_stats())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Démarrage et arrêt ordonnés de l'application."""
    settings: Settings = app.state.settings

    init_engine(settings)
    supervisor = Supervisor(bus=get_event_bus())
    app.state.supervisor = supervisor
    app.state.agent = LocalAgent(supervisor)

    # Les serveurs configurés sont mis sous supervision au démarrage ; aucun n'est
    # lancé automatiquement à ce stade (le démarrage au boot arrivera en phase 5).
    try:
        async with session_scope() as session:
            await ServerService(session, settings, supervisor).register_all()
    except Exception:  # pragma: no cover - base absente ou migrations non appliquées
        logger.exception(
            "server_registration_skipped",
            hint="Vérifier que les migrations ont été appliquées (alembic upgrade head).",
        )

    stats_task = asyncio.create_task(_publish_system_stats(settings), name="msm-system-stats")

    logger.info(
        "msm_started",
        version=__version__,
        environment=settings.environment,
        process_backend=get_backend().name,
        servers=len(supervisor),
        python=sys.version.split()[0],
    )

    try:
        yield
    finally:
        stats_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stats_task
        # Les serveurs Minecraft ne sont PAS arrêtés : redémarrer le panel ne doit
        # pas déconnecter les joueurs. Ils seront réadoptés au prochain démarrage.
        await supervisor.shutdown(stop_servers=False)
        get_event_bus().close()
        await dispose_engine()
        logger.info("msm_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construit l'application. Paramétrable pour les tests."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Minecraft Server Manager",
        description="Panneau de contrôle multi-serveurs Minecraft.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,  # nécessaire au cookie de session
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    register_error_handlers(app)
    app.include_router(api_router)
    app.include_router(websocket_router)
    return app


app = create_app()


def main() -> None:  # pragma: no cover - point d'entrée console
    """Démarre le serveur (`msm` en ligne de commande)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "msm.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,  # contrainte structurelle, voir l'en-tête du module
        log_config=None,  # la journalisation est déjà configurée par MSM
    )


if __name__ == "__main__":  # pragma: no cover
    main()
