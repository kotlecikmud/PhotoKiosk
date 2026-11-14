"""Serves the kiosk single-page app (``/``) and its static assets (``/static``)."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from backend.database import SystemSetting, get_db_engine
from backend.theme import get_theme_colors, resolve_theme_key

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(WEB_DIR))
web_router = APIRouter(include_in_schema=False)


class RevalidatingStaticFiles(StaticFiles):
    """Static files that are always revalidated (cheap 304s) so updates show up immediately."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def static_files() -> StaticFiles:
    return RevalidatingStaticFiles(directory=str(WEB_DIR / "static"))


@web_router.get("/", response_class=HTMLResponse)
def kiosk_index(request: Request):
    try:
        with sessionmaker(bind=get_db_engine())() as db:
            setting = db.query(SystemSetting).filter_by(key="ui_theme").first()
        theme_key = resolve_theme_key(setting.value if setting else None)
    except Exception:
        theme_key = resolve_theme_key(None)
    from backend.kiosk_api import APP_VERSION
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"colors": get_theme_colors(theme_key), "version": APP_VERSION},
    )
    response.headers["Cache-Control"] = "no-cache"
    return response
