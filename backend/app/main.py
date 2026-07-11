"""FastAPI application for cached cafe map reads."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import FRONTEND_CORS_ORIGINS, TAILNET_CORS_ORIGIN_REGEX


def create_app() -> FastAPI:
    app = FastAPI(title="cafe-crowd API", version="0.1.0")
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
