"""Screen 1: Welcome & Device Selection screen."""
import os
import customtkinter as ctk
from typing import Callable, Dict, Any, List
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from devices.storage import StorageManager


class HomeScreen(ctk.CTkFrame):
    def __init__(self, parent, on_source_selected: Callable[[str, str], None]):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.on_source_selected = on_source_selected

        self._build_ui()

    def _build_ui(self):
        # Top banner
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=40, pady=(40, 20))

        title = ctk.CTkLabel(
            header,
            text="PHOTO KIOSK",
            font=FONTS["title_hero"],
            text_color=COLORS["primary"]
        )
        title.pack(anchor="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Touch to print your favorite photographs in studio quality",
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        subtitle.pack(anchor="w", pady=(6, 0))

        # Main action card / device selection area
        center_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16)
        center_card.pack(fill="both", expand=True, padx=40, pady=20)

        card_title = ctk.CTkLabel(
            center_card,
            text="Select Photo Source",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        card_title.pack(anchor="w", padx=30, pady=(30, 16))

        # Sources container
        self.sources_container = ctk.CTkScrollableFrame(
            center_card,
            fg_color="transparent"
        )
        self.sources_container.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        self.refresh_sources()

        # Bottom manual fallback / start buttons
        bottom_bar = ctk.CTkFrame(center_card, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=30, pady=(0, 30))

        refresh_btn = ctk.CTkButton(
            bottom_bar,
            text="Scan for Devices",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self.refresh_sources
        )
        refresh_btn.pack(side="left", padx=(0, 12))

    def refresh_sources(self):
        # Clear existing
        for widget in self.sources_container.winfo_children():
            widget.destroy()

        # 1. Shared / SMB Photos folder
        shared_path = os.path.abspath("storage/shared")
        self._add_source_button("Network / Shared Photos", shared_path, "Server Storage")

        # 2. Removable devices from /media or /mnt
        removable = StorageManager.get_mounted_media_paths()
        for dev in removable:
            self._add_source_button(f"USB / Removable ({dev['name']})", dev["path"], "Removable Media")

    def _add_source_button(self, name: str, path: str, type_label: str):
        row = ctk.CTkFrame(self.sources_container, fg_color=COLORS["surface"], corner_radius=12)
        row.pack(fill="x", pady=8, ipady=6)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=20, pady=10)

        lbl_name = ctk.CTkLabel(info, text=name, font=FONTS["subtitle"], text_color=COLORS["text"])
        lbl_name.pack(anchor="w")

        lbl_path = ctk.CTkLabel(info, text=f"Type: {type_label} | Path: {path}", font=FONTS["small"], text_color=COLORS["text_muted"])
        lbl_path.pack(anchor="w")

        btn = ctk.CTkButton(
            row,
            text="Open Source",
            font=FONTS["button"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=lambda p=path, n=name: self.on_source_selected(n, p)
        )
        btn.pack(side="right", padx=20)
