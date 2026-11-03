"""CustomTkinter PhotoKiosk Touchscreen Application Controller."""
import os
import customtkinter as ctk
from typing import Optional, List, Dict, Any

from gui.theme import COLORS, FONTS, set_active_theme
from gui.screens.home import HomeScreen
from gui.screens.gallery import GalleryScreen
from gui.screens.print_config import PrintConfigScreen
from gui.screens.order_summary import OrderSummaryScreen
from gui.screens.processing import ProcessingScreen, CompletionScreen
from devices.monitor import DeviceMonitorDaemon
from devices.session_manager import clean_temp_session_dir, clean_all_temp_sessions
from backend.database import get_db_engine, SystemSetting
from sqlalchemy.orm import sessionmaker


class PhotoKioskApp(ctk.CTk):
    def __init__(self, fullscreen: bool = False):
        super().__init__()
        self._load_active_theme()

        self.title("PhotoKiosk")
        self.geometry("1024x768")
        ctk.set_appearance_mode("dark")

        if fullscreen:
            self.attributes("-fullscreen", True)

        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"])
        self.container.pack(fill="both", expand=True)

        # State storage across navigation
        self.current_session_id: int = 1
        self.current_source_name: str = ""
        self.current_source_path: str = ""
        self.selected_photos: List[Dict[str, Any]] = []
        self.configured_items: List[Dict[str, Any]] = []

        # Clean lingering temp sessions on startup
        clean_all_temp_sessions()

        # Start device monitor daemon
        self.device_daemon = DeviceMonitorDaemon(self._on_device_event)
        self.device_daemon.start()

        # Open initial screen
        self.show_home_screen()

    def _load_active_theme(self):
        try:
            engine = get_db_engine()
            SessionLocal = sessionmaker(bind=engine)
            with SessionLocal() as db:
                th_setting = db.query(SystemSetting).filter_by(key="ui_theme").first()
                if th_setting and th_setting.value:
                    set_active_theme(th_setting.value)
        except Exception:
            pass

    def _clear_container(self):
        for w in self.container.winfo_children():
            w.destroy()

    def show_home_screen(self):
        self._clear_container()
        screen = HomeScreen(self.container, on_source_selected=self._on_source_selected)
        screen.pack(fill="both", expand=True)

    def _on_source_selected(self, source_name: str, source_path: str):
        self.current_source_name = source_name
        self.current_source_path = source_path
        self.show_gallery_screen()

    def show_gallery_screen(self):
        self._clear_container()
        screen = GalleryScreen(
            self.container,
            source_name=self.current_source_name,
            source_path=self.current_source_path,
            on_back=self.show_home_screen,
            on_proceed_to_config=self._on_proceed_to_config
        )
        screen.pack(fill="both", expand=True)

    def _on_proceed_to_config(self, selected_photos: List[Dict[str, Any]]):
        self.selected_photos = selected_photos
        self.show_print_config_screen()

    def show_print_config_screen(self):
        self._clear_container()
        screen = PrintConfigScreen(
            self.container,
            selected_photos=self.selected_photos,
            on_back=self.show_gallery_screen,
            on_proceed_to_summary=self._on_proceed_to_summary
        )
        screen.pack(fill="both", expand=True)

    def _on_proceed_to_summary(self, configured_items: List[Dict[str, Any]]):
        self.configured_items = configured_items
        self.show_order_summary_screen()

    def show_order_summary_screen(self):
        self._clear_container()
        screen = OrderSummaryScreen(
            self.container,
            items=self.configured_items,
            on_back=self.show_print_config_screen,
            on_confirm_order=self._on_confirm_order
        )
        screen.pack(fill="both", expand=True)

    def _on_confirm_order(self, confirmed_items: List[Dict[str, Any]]):
        self._clear_container()
        screen = ProcessingScreen(
            self.container,
            items=confirmed_items,
            on_completed=self._on_order_completed,
            on_error=self._on_order_error
        )
        screen.pack(fill="both", expand=True)

    def _on_order_completed(self, order_id: str, total_prints: int):
        # Clean temp session folder after successful session
        clean_temp_session_dir(self.current_session_id)
        self.current_session_id += 1

        self._clear_container()
        screen = CompletionScreen(
            self.container,
            order_id=order_id,
            total_prints=total_prints,
            on_done=self.show_home_screen
        )
        screen.pack(fill="both", expand=True)

    def _on_order_error(self, error_message: str):
        # Return to summary with notification
        self.show_order_summary_screen()

    def _on_device_event(self, action: str, details: Dict[str, Any]):
        if action == "connected":
            # Post dialog to GUI thread
            self.after(0, lambda: self._show_new_device_dialog(details))

    def _show_new_device_dialog(self, details: Dict[str, Any]):
        # Modal dialog matching Section 4 specification
        dialog = ctk.CTkToplevel(self)
        dialog.title("Photo Source Detected")
        dialog.geometry("450x220")
        dialog.grab_set()

        lbl = ctk.CTkLabel(
            dialog,
            text="New photo source detected.\nWould you like to open it?",
            font=FONTS["subtitle"],
            text_color=COLORS["text"]
        )
        lbl.pack(pady=(30, 20))

        btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_box.pack(pady=10)

        yes_btn = ctk.CTkButton(
            btn_box,
            text="YES",
            font=FONTS["button"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            width=120,
            height=48,
            command=lambda: [dialog.destroy(), self.show_home_screen()]
        )
        yes_btn.pack(side="left", padx=12)

        no_btn = ctk.CTkButton(
            btn_box,
            text="NO",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=120,
            height=48,
            command=dialog.destroy
        )
        no_btn.pack(side="left", padx=12)


def run_gui(fullscreen: bool = False):
    app = PhotoKioskApp(fullscreen=fullscreen)
    app.mainloop()
