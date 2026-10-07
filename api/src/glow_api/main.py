import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles

from glow_api.data import get_datastore
from glow_api.database import run_migrations
from glow_api.logging_config import configure_logging
from glow_api.request_logging import RequestLoggingMiddleware
from glow_api.routers import admin, auth, dimensions, export, me, query, schools
from glow_api.settings import settings

configure_logging()

logger = structlog.get_logger("glow_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application lifespan...")
    settings.warn_insecure_defaults()
    settings.validate_auth_config()

    logger.info("Running migrations...")
    run_migrations()
    logger.info("Migrations complete")

    # Skip datastore initialization in test mode
    if not os.getenv("GLOW_TESTING"):
        logger.info("Initializing datastore...")
        ds = get_datastore()
        logger.info("Datastore created, starting up...")
        ds.startup()
        logger.info("Datastore startup complete")
        yield
        logger.info("Shutting down datastore...")
        ds.shutdown()
    else:
        logger.info("Test mode - skipping datastore initialization")
        yield


app = FastAPI(
    title="GLOW API",
    description="Read-only API for GLOW longitudinal questionnaire data",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    openapi_url="/openapi.json",
    root_path="/api",
)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)


@app.get("/docs", include_in_schema=False)
def swagger_docs():
    return get_swagger_ui_html(
        openapi_url=app.root_path + app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_favicon_url=app.root_path + "/static/favicon.png",
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added after CORSMiddleware so it becomes the outermost user middleware,
# landing inside Starlette's ServerErrorMiddleware (always outermost) but
# outside CORS — it sees unhandled exceptions before ServerErrorMiddleware
# converts them to the generic 500 response.
app.add_middleware(RequestLoggingMiddleware)

# Include routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(me.router)
app.include_router(dimensions.router)
app.include_router(schools.router)
app.include_router(query.router)
app.include_router(export.router)

if settings.DEMO_MODE:
    # Only mounted in demo mode - kept out of the route table entirely
    # (not just guarded at request time) when it's off, so /demo/login
    # 404s rather than existing as a disabled handler.
    from glow_api.routers import demo

    app.include_router(demo.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/")
def root() -> dict:
    return {"title": app.title, "description": app.description, "version": app.version}
