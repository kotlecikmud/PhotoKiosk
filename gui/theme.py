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

COLORS = {
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
}

TOUCH_SIZES = {
    "button_height": 56,
    "button_large_height": 64,
    "border_radius": 12,
    "icon_size": 28,
}
