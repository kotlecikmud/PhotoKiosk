"""FastAPI Application Factory for PhotoKiosk."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_database, get_db_engine
from backend.orders import recover_incomplete_orders
from backend.api import api_router
from backend.library import PhotoLibraryWatcher
from admin.routes import admin_router
from sqlalchemy.orm import sessionmaker

watcher: PhotoLibraryWatcher = None


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

    # Start filesystem watcher on storage/shared directory
    global watcher
    shared_dir = os.path.abspath("storage/shared")
    os.makedirs(shared_dir, exist_ok=True)
    watcher = PhotoLibraryWatcher(shared_dir)
    watcher.start()

    yield

    # Shutdown: stop filesystem observer
    if watcher:
        watcher.stop()


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

    app.include_router(api_router)
    app.include_router(admin_router)

    return app


app = create_app()
