"""Point d'entrée de l'application FastAPI.

Une note importante sur le déploiement : MSM doit tourner en **un seul processus
applicatif** (`uvicorn --workers 1`). Le superviseur détient les tubes d'entrée et
de sortie des serveurs Minecraft ; avec plusieurs workers, chaque processus aurait
sa propre vision partielle et personne ne saurait quel worker possède quel serveur.
La montée en charge repose sur l'asynchrone, et plus tard sur des agents distants.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from msm import __version__
from msm.api.errors import register_error_handlers
from msm.api.v1 import api_router
from msm.bus import get_event_bus
from msm.config import Settings, get_settings
from msm.db.session import dispose_engine, init_engine
from msm.logging_conf import configure_logging, get_logger
from msm.runtime.agent import LocalAgent
from msm.runtime.backends import get_backend
from msm.runtime.supervisor import Supervisor

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Démarrage et arrêt ordonnés de l'application."""
    settings: Settings = app.state.settings

    init_engine(settings)
    supervisor = Supervisor(bus=get_event_bus())
    app.state.supervisor = supervisor
    app.state.agent = LocalAgent(supervisor)

    logger.info(
        "msm_started",
        version=__version__,
        environment=settings.environment,
        process_backend=get_backend().name,
        python=sys.version.split()[0],
    )

    try:
        yield
    finally:
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
    return app


app = create_app()


def main() -> None:  # pragma: no cover - point d'entrée console
    """Démarre le serveur de développement (`msm` en ligne de commande)."""
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
