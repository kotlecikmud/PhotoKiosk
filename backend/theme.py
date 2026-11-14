"""Colour themes shared by the kiosk web UI and the admin panel.

The active theme is stored in the ``ui_theme`` system setting and is injected as CSS
variables into every page, so a change in the admin panel is visible on the next page load.
"""
from typing import Dict

# Predefined cross-UI themes applied across the admin web and the touchscreen kiosk UI
THEMES = {
    "midnight_blue": {
        "name": "Midnight Blue (Default)",
        "bg_dark": "#0f172a",
        "bg_card": "#1e293b",
        "bg_card_selected": "#2563eb",
        "border": "#334155",
        "primary": "#3b82f6",
        "primary_hover": "#2563eb",
        "success": "#10b981",
        "success_hover": "#059669",
        "danger": "#ef4444",
        "danger_hover": "#dc2626",
        "text": "#f8fafc",
        "text_muted": "#94a3b8",
        "surface": "#1e293b",
    },
    "emerald_green": {
        "name": "Emerald Studio",
        "bg_dark": "#06221c",
        "bg_card": "#0c3b31",
        "bg_card_selected": "#059669",
        "border": "#165849",
        "primary": "#10b981",
        "primary_hover": "#059669",
        "success": "#10b981",
        "success_hover": "#047857",
        "danger": "#ef4444",
        "danger_hover": "#dc2626",
        "text": "#f0fdf4",
        "text_muted": "#a7f3d0",
        "surface": "#0c3b31",
    },
    "sunset_amber": {
        "name": "Sunset Amber",
        "bg_dark": "#1c140a",
        "bg_card": "#2d2011",
        "bg_card_selected": "#d97706",
        "border": "#4d381e",
        "primary": "#f59e0b",
        "primary_hover": "#d97706",
        "success": "#10b981",
        "success_hover": "#059669",
        "danger": "#ef4444",
        "danger_hover": "#dc2626",
        "text": "#fffbeb",
        "text_muted": "#fde68a",
        "surface": "#2d2011",
    },
    "graphite_dark": {
        "name": "Graphite Monochrome",
        "bg_dark": "#121212",
        "bg_card": "#1e1e1e",
        "bg_card_selected": "#4b5563",
        "border": "#2d2d2d",
        "primary": "#9ca3af",
        "primary_hover": "#6b7280",
        "success": "#10b981",
        "success_hover": "#059669",
        "danger": "#ef4444",
        "danger_hover": "#dc2626",
        "text": "#f9fafb",
        "text_muted": "#9ca3af",
        "surface": "#1e1e1e",
    },
}

DEFAULT_THEME_KEY = "midnight_blue"


def get_theme_colors(theme_key: str = None) -> Dict[str, str]:
    """Returns the colour dictionary for ``theme_key`` (falls back to the default theme)."""
    return THEMES.get(theme_key or DEFAULT_THEME_KEY, THEMES[DEFAULT_THEME_KEY])


def resolve_theme_key(theme_key: str = None) -> str:
    """Returns ``theme_key`` if it is a known theme, otherwise the default theme key."""
    return theme_key if theme_key in THEMES else DEFAULT_THEME_KEY
