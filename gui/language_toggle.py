"""Language toggle widget for CustomTkinter touchscreen screens."""
import customtkinter as ctk
from typing import Optional, List
from gui.theme import COLORS, FONTS
from backend.i18n import get_i18n


class LanguageToggle(ctk.CTkFrame):
    """Reusable, touch-friendly segmented language toggle button."""

    def __init__(self, parent, height: int = 40, corner_radius: int = 8, **kwargs):
        super().__init__(parent, fg_color=COLORS["surface"], corner_radius=corner_radius, **kwargs)
        self.i18n = get_i18n()
        self.height = height
        self.buttons: dict = {}
        self._build_buttons()
        self.i18n.subscribe(self._on_lang_changed)

    def _build_buttons(self):
        langs = self.i18n.get_available_languages()
        current = self.i18n.current_lang

        for lang in langs:
            display_txt = lang.upper()
            is_active = (lang == current)
            btn = ctk.CTkButton(
                self,
                text=display_txt,
                font=FONTS["body_bold"],
                width=54,
                height=self.height,
                fg_color=COLORS["primary"] if is_active else "transparent",
                hover_color=COLORS["primary_hover"] if is_active else COLORS["border"],
                text_color=COLORS["text"],
                corner_radius=6,
                command=lambda l=lang: self._select_language(l)
            )
            btn.pack(side="left", padx=3, pady=3)
            self.buttons[lang] = btn

    def _select_language(self, lang: str):
        if lang != self.i18n.current_lang:
            self.i18n.set_language(lang)

    def _on_lang_changed(self, lang: str):
        for l, btn in self.buttons.items():
            if btn.winfo_exists():
                is_active = (l == lang)
                btn.configure(
                    fg_color=COLORS["primary"] if is_active else "transparent",
                    hover_color=COLORS["primary_hover"] if is_active else COLORS["border"]
                )

    def destroy(self):
        self.i18n.unsubscribe(self._on_lang_changed)
        super().destroy()
