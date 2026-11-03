"""Touchscreen styling constants and theme configuration for PhotoKiosk."""

FONT_FAMILY = "Roboto"

FONTS = {
    "title_hero": (FONT_FAMILY, 32, "bold"),
    "title": (FONT_FAMILY, 24, "bold"),
    "subtitle": (FONT_FAMILY, 18, "bold"),
    "body": (FONT_FAMILY, 15, "normal"),
    "body_bold": (FONT_FAMILY, 15, "bold"),
    "button": (FONT_FAMILY, 16, "bold"),
    "badge": (FONT_FAMILY, 12, "bold"),
    "small": (FONT_FAMILY, 13, "normal"),
}

# Predefined cross-UI themes applied across admin web and user touchscreen interfaces
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

CURRENT_THEME_KEY = "midnight_blue"
COLORS = dict(THEMES[CURRENT_THEME_KEY])


def set_active_theme(theme_key: str):
    """Sets active theme and updates global COLORS dictionary."""
    global CURRENT_THEME_KEY, COLORS
    if theme_key in THEMES:
        CURRENT_THEME_KEY = theme_key
        COLORS.clear()
        COLORS.update(THEMES[theme_key])


def get_theme_colors(theme_key: str = None) -> dict:
    """Returns colors dictionary for specified theme or active theme."""
    key = theme_key or CURRENT_THEME_KEY
    return THEMES.get(key, THEMES["midnight_blue"])

TOUCH_SIZES = {
    "button_height": 56,
    "button_large_height": 64,
    "border_radius": 12,
    "icon_size": 28,
}
