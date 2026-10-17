"""FastAPI application factory."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from a0td.api.auth import AuthMiddleware, AuthService
from a0td.api.deps import Services
from a0td.api.errors import install_error_handlers
from a0td.api.routers import (
    app_settings,
    auth,
    catalog,
    documents,
    jobs,
    plan,
    presets,
    printers,
    printing,
    system,
)
from a0td.api.security import SecurityHeadersMiddleware
from a0td.automation.hotfolder import HotFolder
from a0td.config import AppConfig, get_config
from a0td.jobs.manager import JobManager
from a0td.storage.db import Database
from a0td.storage.files import Files
from a0td.storage.retention import retention_loop
from a0td.version import __version__

RETENTION_INTERVAL_S = 600.0


class SPAStaticFiles(StaticFiles):
    """Static files with an ``index.html`` fallback for client-side routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        path = path.replace("\\", "/")
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def build_services(config: AppConfig) -> Services:
    """Wire storage and the job manager. Touches nothing on disk until the lifespan runs."""
    files = Files(config)
    db = Database(files.db_path)
    return Services(config=config, db=db, files=files, manager=JobManager(config, db, files))


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or get_config()
    services = build_services(config)
    auth_service = AuthService(config)  # fails fast if the mode lacks its secret

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.files.ensure_dirs()
        services.db.migrate()  # idempotent: the entrypoint may have run it already
        app_settings.apply_stored_retention(services)
        await services.manager.start()  # startup recovery (SPEC 9.5)
        retention = asyncio.create_task(
            retention_loop(services.db, services.files, interval_s=RETENTION_INTERVAL_S),
            name="a0td-retention",
        )
        hotfolder = (
            asyncio.create_task(HotFolder(services).run(), name="a0td-hotfolder")
            if config.hotfolder
            else None
        )
        try:
            yield
        finally:
            if hotfolder is not None:
                hotfolder.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hotfolder
            retention.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention
            await services.manager.stop()
            services.db.dispose()

    app = FastAPI(
        title="a0-to-domestic",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.auth = auth_service
    app.add_middleware(AuthMiddleware, auth=auth_service)
    install_error_handlers(app)
    # Outermost layer: also wraps ServerErrorMiddleware, so 500 responses get the headers too.
    build_stack = app.build_middleware_stack
    app.build_middleware_stack = lambda: SecurityHeadersMiddleware(build_stack())  # type: ignore[method-assign]

    # printing before printers: GET /printers/discover must win over /printers/{printer_id}
    for module in (
        system,
        auth,
        catalog,
        presets,
        app_settings,
        printing,
        printers,
        documents,
        plan,
        jobs,
    ):
        app.include_router(module.router)

    if config.metrics:
        from a0td.metrics import install_metrics  # prometheus_client is only needed here

        install_metrics(app, services)

    static_dir = config.static_dir
    if static_dir is not None and Path(static_dir).is_dir():
        # Mounted last so it never shadows the API routes.
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="spa")

    return app
