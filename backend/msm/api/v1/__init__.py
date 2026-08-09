"""Routeurs de l'API v1."""

from fastapi import APIRouter

from msm.api.v1 import system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)

__all__ = ["api_router"]
