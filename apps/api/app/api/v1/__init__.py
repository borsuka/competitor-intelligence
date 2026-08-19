"""Version 1 of the HTTP API.

Everything is mounted under ``/api/v1``.  A future v2 gets its own package and its own
routers; existing clients keep working because the prefix is part of the contract.
"""

from fastapi import APIRouter

from app.api.v1 import alerts, auth, competitors, health, insights, organizations

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(competitors.router)
api_router.include_router(insights.router)
api_router.include_router(alerts.router)

__all__ = ["api_router", "health"]
