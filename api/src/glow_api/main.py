import os
from datetime import timezone
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from glow_api.counts_store import get_counts_store
from glow_api.data import ODKFetchError, get_datastore
from glow_api.database import run_migrations
from glow_api.logging_config import configure_logging
from glow_api.request_logging import RequestLoggingMiddleware
from glow_api.routers import (
    admin,
    api_keys,
    auth,
    dimensions,
    export,
    me,
    query,
    schools,
)
from glow_api.settings import settings
from glow_api.vintages import VintageStorageNotConfigured, scheduled_cut_vintage

configure_logging()

logger = structlog.get_logger("glow_api")


def build_vintage_scheduler(ds) -> BackgroundScheduler:
    """Monthly vintage cut, pinned to UTC so the host TZ never shifts it."""
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    scheduler.add_job(
        scheduled_cut_vintage,
        "cron",
        args=[ds],
        day=settings.VINTAGE_CADENCE_CRON_DAY,
        hour=settings.VINTAGE_CADENCE_CRON_HOUR,
    )
    return scheduler


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
        # No full fetch here: DataStore stays empty until the first vintage
        # cut. Only the slim counts poll runs from startup.
        ds = get_datastore()
        counts_store = get_counts_store()
        counts_store.startup()

        vintage_scheduler = None
        if settings.VINTAGES_DIR:
            vintage_scheduler = build_vintage_scheduler(ds)
            vintage_scheduler.start()
            logger.info("Vintage cut scheduler started")
        else:
            logger.warning(
                "GLOW_VINTAGES_DIR not set - /query and /export will "
                "503 until the vintages directory is configured and a vintage is cut"
            )

        yield

        counts_store.shutdown()
        if vintage_scheduler:
            vintage_scheduler.shutdown()
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


@app.exception_handler(VintageStorageNotConfigured)
def vintage_storage_not_configured(
    request: Request, exc: VintageStorageNotConfigured
) -> JSONResponse:
    # Handled (not left to ServerErrorMiddleware) so the response still gets
    # CORS headers and the dashboard can show why, not just "NetworkError".
    return JSONResponse(
        status_code=503, content={"detail": f"Vintage storage not configured: {exc}"}
    )


@app.exception_handler(ODKFetchError)
def odk_fetch_failed(request: Request, exc: ODKFetchError) -> JSONResponse:
    # Same CORS reasoning as above: a cut that couldn't reach ODK should say
    # so in the dashboard, not surface as "NetworkError".
    return JSONResponse(status_code=502, content={"detail": str(exc)})


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
app.include_router(api_keys.router)

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
