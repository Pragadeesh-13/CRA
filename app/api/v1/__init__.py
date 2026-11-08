"""Versioned API routers."""

from app.api.v1.saas_routes import router, create_saas_router

__all__ = ["router", "create_saas_router"]
