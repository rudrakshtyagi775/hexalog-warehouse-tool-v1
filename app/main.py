from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.models  # registers all ORM models in the mapper registry
from app.config import settings
from app.logging_config import configure_logging
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import customer as customer_router
from app.routers import inward as inward_router
from app.routers import outward as outward_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(is_production=settings.is_production, log_level=settings.LOG_LEVEL)
    log.info("startup", env=settings.APP_ENV)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hexalog Warehouse Tool",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(customer_router.router)
    app.include_router(inward_router.router)
    app.include_router(outward_router.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", path=str(request.url), exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
