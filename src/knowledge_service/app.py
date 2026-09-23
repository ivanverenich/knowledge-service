from collections.abc import Awaitable, Callable

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], Awaitable[bool]]


def create_app(readiness_check: ReadinessCheck | None = None) -> FastAPI:
    app = FastAPI(title="knowledge-service")

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        ready = readiness_check is None or await readiness_check()
        if not ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not_ready"},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )

    return app
