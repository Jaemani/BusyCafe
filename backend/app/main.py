"""FastAPI application for cached map reads and bounded user submissions."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    API_SEARCH_BROWSER_MAX_AGE_SEC,
    API_STATIC_BROWSER_MAX_AGE_SEC,
    API_STATIC_EDGE_MAX_AGE_SEC,
    API_STATIC_STALE_IF_ERROR_SEC,
    API_STATIC_STALE_WHILE_REVALIDATE_SEC,
    API_VERSIONED_MAP_EDGE_MAX_AGE_SEC,
    FRONTEND_CORS_ORIGINS,
    TAILNET_CORS_ORIGIN_REGEX,
    USER_CONTRIBUTION_MAX_BODY_BYTES,
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
VERSIONED_MAP_CACHE_CONTROL = _public_cache_control(
    browser_max_age=API_MAP_BROWSER_MAX_AGE_SEC,
    edge_max_age=API_VERSIONED_MAP_EDGE_MAX_AGE_SEC,
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
SEARCH_CACHE_CONTROL = f"private, max-age={API_SEARCH_BROWSER_MAX_AGE_SEC}"


def _cache_control_for_path(path: str) -> str | None:
    if path == "/api/health":
        return HEALTH_CACHE_CONTROL
    if path == "/api/sources":
        return STATIC_CACHE_CONTROL
    if path in {
        "/api/cafes",
        "/api/cafes/summary",
        "/api/cafes/search",
        "/api/hotspots",
    }:
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
        """Set strict cache boundaries for public reads and private writes.

        Shared caches include the full query string in their key. Canonical tile
        bboxes therefore reuse responses; arbitrary pan coordinates do not.
        Errors and requests carrying credentials are never marked public.
        """

        is_user_submission = (
            request.method == "POST"
            and request.url.path.startswith("/api/cafes/")
        )
        if is_user_submission:
            content_length = request.headers.get("content-length")
            try:
                body_size = int(content_length) if content_length else 0
            except ValueError:
                body_size = USER_CONTRIBUTION_MAX_BODY_BYTES + 1
            if body_size > USER_CONTRIBUTION_MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "submission payload too large"},
                    headers={"Cache-Control": "no-store"},
                )

        response = await call_next(request)
        if is_user_submission:
            response.headers["Cache-Control"] = "no-store"
            return response
        cache_control = _cache_control_for_path(request.url.path)
        if (
            request.url.path == "/api/cafes/search"
            and request.query_params.get("q") is not None
        ):
            cache_control = SEARCH_CACHE_CONTROL
        if (
            cache_control == MAP_CACHE_CONTROL
            and request.query_params.get("data_version")
        ):
            cache_control = VERSIONED_MAP_CACHE_CONTROL
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
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=[VIEWPORT_TRUNCATED_HEADER],
    )
    app.include_router(router)
    return app


app = create_app()
