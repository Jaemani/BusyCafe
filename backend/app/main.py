"""FastAPI application for cached cafe map reads."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.api.routes import VIEWPORT_TRUNCATED_HEADER, router
from app.config import (
    API_HEALTH_BROWSER_MAX_AGE_SEC,
    API_HEALTH_EDGE_MAX_AGE_SEC,
    API_HEALTH_STALE_IF_ERROR_SEC,
    API_HEALTH_STALE_WHILE_REVALIDATE_SEC,
    API_MAP_BROWSER_MAX_AGE_SEC,
    API_MAP_EDGE_MAX_AGE_SEC,
    API_MAP_STALE_IF_ERROR_SEC,
    API_MAP_STALE_WHILE_REVALIDATE_SEC,
    API_STATIC_BROWSER_MAX_AGE_SEC,
    API_STATIC_EDGE_MAX_AGE_SEC,
    API_STATIC_STALE_IF_ERROR_SEC,
    API_STATIC_STALE_WHILE_REVALIDATE_SEC,
    FRONTEND_CORS_ORIGINS,
    TAILNET_CORS_ORIGIN_REGEX,
)


def _public_cache_control(
    *,
    browser_max_age: int,
    edge_max_age: int,
    stale_while_revalidate: int,
    stale_if_error: int,
) -> str:
    return (
        f"public, max-age={browser_max_age}, s-maxage={edge_max_age}, "
        f"stale-while-revalidate={stale_while_revalidate}, "
        f"stale-if-error={stale_if_error}"
    )


MAP_CACHE_CONTROL = _public_cache_control(
    browser_max_age=API_MAP_BROWSER_MAX_AGE_SEC,
    edge_max_age=API_MAP_EDGE_MAX_AGE_SEC,
    stale_while_revalidate=API_MAP_STALE_WHILE_REVALIDATE_SEC,
    stale_if_error=API_MAP_STALE_IF_ERROR_SEC,
)
HEALTH_CACHE_CONTROL = _public_cache_control(
    browser_max_age=API_HEALTH_BROWSER_MAX_AGE_SEC,
    edge_max_age=API_HEALTH_EDGE_MAX_AGE_SEC,
    stale_while_revalidate=API_HEALTH_STALE_WHILE_REVALIDATE_SEC,
    stale_if_error=API_HEALTH_STALE_IF_ERROR_SEC,
)
STATIC_CACHE_CONTROL = _public_cache_control(
    browser_max_age=API_STATIC_BROWSER_MAX_AGE_SEC,
    edge_max_age=API_STATIC_EDGE_MAX_AGE_SEC,
    stale_while_revalidate=API_STATIC_STALE_WHILE_REVALIDATE_SEC,
    stale_if_error=API_STATIC_STALE_IF_ERROR_SEC,
)


def _cache_control_for_path(path: str) -> str | None:
    if path == "/api/health":
        return HEALTH_CACHE_CONTROL
    if path == "/api/sources":
        return STATIC_CACHE_CONTROL
    if path in {"/api/cafes", "/api/hotspots"}:
        return MAP_CACHE_CONTROL
    if (
        path.startswith("/api/cafes/")
        and path.removeprefix("/api/cafes/").isdigit()
    ):
        return MAP_CACHE_CONTROL
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="cafe-crowd API", version="0.1.0")

    @app.middleware("http")
    async def cache_read_models(request: Request, call_next):
        """Permit short browser/CDN reuse of public cache-only read models.

        Shared caches include the full query string in their key. Canonical tile
        bboxes therefore reuse responses; arbitrary pan coordinates do not.
        Errors and requests carrying credentials are never marked public.
        """

        response = await call_next(request)
        cache_control = _cache_control_for_path(request.url.path)
        if (
            request.method == "GET"
            and response.status_code == 200
            and "authorization" not in request.headers
            and "set-cookie" not in response.headers
            and cache_control is not None
        ):
            response.headers["Cache-Control"] = cache_control
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(FRONTEND_CORS_ORIGINS),
        allow_origin_regex=TAILNET_CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=[VIEWPORT_TRUNCATED_HEADER],
    )
    app.include_router(router)
    return app


app = create_app()
