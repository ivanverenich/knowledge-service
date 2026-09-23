"""Tests FastAPI application."""

import httpx
from fastapi import FastAPI

from knowledge_service.app import create_app


def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    )


async def test_liveness_endpoint_returns_ok() -> None:
    app = create_app()
    async with get_client(app) as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_readiness_endpoint_returns_failure() -> None:
    async def not_ready() -> bool:
        return False

    app = create_app(readiness_check=not_ready)
    async with get_client(app) as client:
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}


async def test_readiness_endpoint_returns_success() -> None:
    async def ready() -> bool:
        return True

    app = create_app(readiness_check=ready)
    async with get_client(app) as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
