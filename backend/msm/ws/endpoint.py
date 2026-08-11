"""Point d'entrée WebSocket.

L'authentification a lieu **avant** l'acceptation de la connexion : un client non
authentifié est refermé avec un code applicatif explicite plutôt qu'accepté puis
ignoré, ce qui laisserait croire à un problème réseau.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from msm.bus import get_event_bus
from msm.config import Settings, get_settings
from msm.db.session import session_scope
from msm.logging_conf import get_logger
from msm.services.auth_service import AuthService
from msm.ws.connection import WebSocketConnection

logger = get_logger(__name__)

router = APIRouter()

#: Codes de fermeture applicatifs (plage 4000-4999 réservée aux applications).
CLOSE_UNAUTHENTICATED = 4401
CLOSE_INTERNAL = 4500


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Flux temps réel : états, logs, statistiques et joueurs."""
    # Les réglages viennent de l'application, pas d'un accès global : les tests
    # et une éventuelle instance secondaire doivent voir leur propre configuration.
    settings: Settings = getattr(websocket.app.state, "settings", None) or get_settings()
    token = websocket.cookies.get(settings.session_cookie_name)

    if not token:
        await websocket.close(code=CLOSE_UNAUTHENTICATED, reason="Authentification requise")
        return

    try:
        async with session_scope() as session:
            resolved = await AuthService(session, settings).resolve_session(token)
            if resolved is None:
                await websocket.close(
                    code=CLOSE_UNAUTHENTICATED, reason="Session expirée ou invalide"
                )
                return
            user, _ = resolved
            # Les attributs sont copiés : l'objet ORM ne survit pas à la session,
            # alors que la connexion peut durer des heures.
            user_id, username = user.id, user.username
    except Exception:  # pragma: no cover - échec d'accès à la base
        logger.exception("websocket_auth_failed")
        await websocket.close(code=CLOSE_INTERNAL, reason="Erreur interne")
        return

    await websocket.accept()
    logger.info("websocket_connected", user=username)

    connection = WebSocketConnection(
        websocket,
        user_id=user_id,
        username=username,
        supervisor=websocket.app.state.supervisor,
        bus=get_event_bus(),
        settings=settings,
    )

    try:
        await connection.run()
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("websocket_disconnected", user=username)
