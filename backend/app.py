"""FastAPI Application Factory for PhotoKiosk (REST API, web kiosk UI and web admin)."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from backend.database import init_database
from backend.orders import recover_incomplete_orders
from backend.api import api_router
from backend.kiosk_api import KioskServices, router as kiosk_router
from backend.web import static_files, web_router
from admin.routes import admin_router
from sqlalchemy.orm import sessionmaker


class _GZip(GZipMiddleware):
    """GZip for JSON, but never for the SSE stream (compression would buffer live events)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith("/kiosk/events"):
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and verify schema
    engine = init_database()
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Reconcile interrupted orders upon startup
        recovered = recover_incomplete_orders(db)
        if recovered:
            print(f"[STARTUP] Reconciled {len(recovered)} interrupted orders to FAILED.")
    finally:
        db.close()

    # Device monitor, scanner events and the shared-folder watcher feed the browser via SSE
    app.state.kiosk.start(asyncio.get_running_loop())

    yield

    app.state.kiosk.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PhotoKiosk Backend & Administration Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Photo lists can be large JSON documents; text/event-stream is never compressed.
    app.add_middleware(_GZip, minimum_size=1024)

    app.state.kiosk = KioskServices()

    app.include_router(api_router)
    app.include_router(kiosk_router)
    app.include_router(admin_router)
    app.include_router(web_router)
    app.mount("/static", static_files(), name="static")

    return app


app = create_app()
