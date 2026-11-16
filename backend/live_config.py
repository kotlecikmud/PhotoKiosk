"""Tells every kiosk screen when the administrator saved something.

A pure ASGI middleware (not ``BaseHTTPMiddleware``, which interferes with streaming responses
such as the live-event stream). After a successful state-changing request below ``/admin`` it calls
``KioskServices.on_config_changed``, which refreshes caches, applies Bluetooth settings and pushes a
``config`` event; the kiosk then re-reads theme, language, prices and sources without a reload.
"""
from starlette.concurrency import run_in_threadpool

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_IGNORED_PATHS = ("/admin/login", "/admin/logout")


class AdminChangeNotifier:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] in _SAFE_METHODS
                or not scope["path"].startswith("/admin") or scope["path"] in _IGNORED_PATHS):
            await self.app(scope, receive, send)
            return

        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body") and status["code"] < 400:
                svc = getattr(scope["app"].state, "kiosk", None)
                if svc is not None:
                    await run_in_threadpool(svc.on_config_changed)

        await self.app(scope, receive, send_wrapper)
