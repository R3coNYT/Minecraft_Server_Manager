"""Conversion des exceptions en réponses HTTP structurées.

Toute erreur sortante a la même forme, ce qui permet au frontend d'afficher un
bloc « Cause / Action » sans connaître le détail de chaque cas ::

    {
      "code": "SERVER_START_FAILED",
      "message": "Impossible de démarrer le serveur.",
      "cause": "run.sh n'est pas exécutable.",
      "remediation": "chmod +x /data/minecraft/modded/run.sh",
      "trace_id": "a3f9…"
    }

Une exception imprévue ne fuit jamais son détail au client : le message reste
générique et la trace complète part dans ``logs/msm.log``, retrouvable par son
``trace_id``.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from msm.exceptions import MsmError
from msm.logging_conf import get_logger

logger = get_logger(__name__)


def _trace_id() -> str:
    return uuid.uuid4().hex[:12]


async def msm_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MsmError)
    trace_id = _trace_id()
    logger.warning(
        "request_failed",
        trace_id=trace_id,
        code=exc.code,
        path=request.url.path,
        cause=exc.cause,
        **exc.context,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload(trace_id))


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
        },
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    first = exc.errors()[0] if exc.errors() else {}
    field = " → ".join(str(part) for part in first.get("loc", ())[1:]) or "requête"
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Les données envoyées sont invalides.",
            "cause": f"Champ « {field} » : {first.get('msg', 'valeur incorrecte')}.",
            "remediation": "Corriger le champ indiqué puis réessayer.",
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = _trace_id()
    logger.exception("unhandled_error", trace_id=trace_id, path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "Une erreur interne est survenue.",
            "remediation": (
                f"Consulter logs/msm.log en recherchant l'identifiant de trace « {trace_id} »."
            ),
            "trace_id": trace_id,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MsmError, msm_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
