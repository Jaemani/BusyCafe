"""FastAPI application for cached cafe map reads."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.api.routes import router
from app.config import FRONTEND_CORS_ORIGINS, TAILNET_CORS_ORIGIN_REGEX


def create_app() -> FastAPI:
    app = FastAPI(title="cafe-crowd API", version="0.1.0")

    @app.middleware("http")
    async def cache_read_models(request: Request, call_next):
        """Permit short CDN reuse of immutable-in-practice map read models.

        A one-minute shared cache stays safely below the ten-minute ingest SLA,
        while preventing identical viewport requests from repeatedly querying the
        snapshot database under map pan/zoom traffic.
        """

        response = await call_next(request)
        if request.method == "GET" and request.url.path in {
            "/api/cafes",
            "/api/hotspots",
            "/api/health",
        }:
            response.headers["Cache-Control"] = "public, max-age=30, s-maxage=60, stale-while-revalidate=300"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(FRONTEND_CORS_ORIGINS),
        allow_origin_regex=TAILNET_CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
