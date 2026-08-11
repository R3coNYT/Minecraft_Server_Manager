"""Routeurs de l'API v1."""

from fastapi import APIRouter

from msm.api.v1 import (
    audit,
    auth,
    console,
    events,
    files,
    players,
    servers,
    system,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(servers.router)
api_router.include_router(console.router)
api_router.include_router(players.router)
api_router.include_router(files.router)
api_router.include_router(events.router)
api_router.include_router(audit.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
