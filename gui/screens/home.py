"""Screen 1: Welcome & Device Selection screen with live iPhone WPD scanner status card."""
import os
import customtkinter as ctk
from typing import Callable, Dict, Any, List, Optional
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from devices.storage import StorageManager
from devices.scanner import authoritative_scanner, ScanStatus
from devices.session_manager import get_date_range_bounds, filter_photos_by_date_range, get_temp_session_dir, stage_session_thumbnails
from backend.i18n import get_i18n, t
from gui.language_toggle import LanguageToggle


class HomeScreen(ctk.CTkFrame):
    def __init__(self, parent, on_source_selected: Callable[[str, str], None]):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.on_source_selected = on_source_selected
        self._unsubscribe_scanner: Optional[Callable[[], None]] = None
        self._date_filter_dialog: Optional[ctk.CTkToplevel] = None
        self._last_completed_scan_id: int = -1
        self.i18n = get_i18n()

        self._build_ui()
        self.i18n.subscribe(self._on_language_changed)
        self._subscribe_to_scanner()

    def _on_language_changed(self, lang: str):
        if self.winfo_exists():
            self._update_localized_texts()
            self.refresh_sources()

    def destroy(self):
        self.i18n.unsubscribe(self._on_language_changed)
        if self._unsubscribe_scanner:
            try:
                self._unsubscribe_scanner()
            except Exception:
                pass
            self._unsubscribe_scanner = None
        super().destroy()

    def _build_ui(self):
        # Top banner
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=40, pady=(30, 10))

        # Language toggle on top right
        self.lang_toggle = LanguageToggle(header)
        self.lang_toggle.pack(side="right", anchor="n")

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="both", expand=True)

        self.title_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.title"),
            font=FONTS["title_hero"],
            text_color=COLORS["primary"]
        )
        self.title_lbl.pack(anchor="w")

        self.subtitle_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.subtitle"),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        self.subtitle_lbl.pack(anchor="w", pady=(4, 0))

        # iPhone Scanner Status Banner / Card (Visible when connected)
        self.scanner_card = ctk.CTkFrame(self, fg_color=COLORS["surface"], corner_radius=14)
        self._build_scanner_card_ui()

        # Scanning Modal Overlay (Visible while scan_status == SCANNING)
        self.scanning_overlay = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=18, border_width=2, border_color=COLORS["primary"])
        self._build_scanning_overlay_ui()

        # Main action card / device selection area
        self.center_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16)
        self.center_card.pack(fill="both", expand=True, padx=40, pady=(10, 20))

        self.card_title = ctk.CTkLabel(
            self.center_card,
            text=t("app.prompt_sources"),
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.card_title.pack(anchor="w", padx=30, pady=(20, 10))

        # Sources container
        self.sources_container = ctk.CTkScrollableFrame(
            self.center_card,
            fg_color="transparent"
        )
        self.sources_container.pack(fill="both", expand=True, padx=30, pady=(0, 15))

        self.refresh_sources()

        # Bottom manual fallback / start buttons
        bottom_bar = ctk.CTkFrame(self.center_card, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=30, pady=(0, 20))

        self.refresh_btn = ctk.CTkButton(
            bottom_bar,
            text=t("app.scan_devices"),
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self.refresh_sources
        )
        self.refresh_btn.pack(side="left", padx=(0, 12))

    def _update_localized_texts(self):
        if hasattr(self, "title_lbl") and self.title_lbl.winfo_exists():
            self.title_lbl.configure(text=t("app.title"))
        if hasattr(self, "subtitle_lbl") and self.subtitle_lbl.winfo_exists():
            self.subtitle_lbl.configure(text=t("app.subtitle"))
        if hasattr(self, "card_title") and self.card_title.winfo_exists():
            self.card_title.configure(text=t("app.prompt_sources"))
        if hasattr(self, "refresh_btn") and self.refresh_btn.winfo_exists():
            self.refresh_btn.configure(text=t("app.scan_devices"))
        if hasattr(self, "scanner_title_lbl") and self.scanner_title_lbl.winfo_exists():
            self.scanner_title_lbl.configure(text=t("scanner.iphone_title"))
        if hasattr(self, "scanner_rescan_btn") and self.scanner_rescan_btn.winfo_exists():
            self.scanner_rescan_btn.configure(text=t("scanner.btn_rescan"))
        if hasattr(self, "overlay_title_lbl") and self.overlay_title_lbl.winfo_exists():
            self.overlay_title_lbl.configure(text=t("scanner.modal_scanning"))

    def _build_scanner_card_ui(self):
        # Header row inside scanner card
        top_row = ctk.CTkFrame(self.scanner_card, fg_color="transparent")
        top_row.pack(fill="x", padx=20, pady=(12, 4))

        self.scanner_title_lbl = ctk.CTkLabel(
            top_row,
            text="📱 Apple iPhone — Scanner",
            font=FONTS["subtitle"],
            text_color=COLORS["text"]
        )
        self.scanner_title_lbl.pack(side="left")

        self.scanner_status_badge = ctk.CTkLabel(
            top_row,
            text=t("scanner.connected"),
            font=FONTS["body_bold"],
            text_color=COLORS["primary"]
        )
        self.scanner_status_badge.pack(side="right")

        # Path row
        self.scanner_path_lbl = ctk.CTkLabel(
            self.scanner_card,
            text=t("scanner.path_prefix", path="Internal Storage"),
            font=FONTS["small"],
            text_color=COLORS["text_muted"]
        )
        self.scanner_path_lbl.pack(anchor="w", padx=20, pady=(0, 4))

        # Counters row
        stats_frame = ctk.CTkFrame(self.scanner_card, fg_color="transparent")
        stats_frame.pack(fill="x", padx=20, pady=(0, 10))

        self.images_count_lbl = ctk.CTkLabel(
            stats_frame,
            text=t("scanner.images_count", count=0),
            font=FONTS["body_bold"],
            text_color=COLORS["primary"]
        )
        self.images_count_lbl.pack(side="left", padx=(0, 16))

        self.videos_count_lbl = ctk.CTkLabel(
            stats_frame,
            text=t("scanner.videos_count", count=0),
            font=FONTS["body_bold"],
            text_color=COLORS["text"]
        )
        self.videos_count_lbl.pack(side="left", padx=(0, 16))

        self.files_count_lbl = ctk.CTkLabel(
            stats_frame,
            text=t("scanner.total_files", count=0),
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.files_count_lbl.pack(side="left", padx=(0, 16))

        self.folders_count_lbl = ctk.CTkLabel(
            stats_frame,
            text=t("scanner.folders_count", count=0),
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.folders_count_lbl.pack(side="left", padx=(0, 16))

        self.errors_count_lbl = ctk.CTkLabel(
            stats_frame,
            text=t("scanner.errors_count", count=0),
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.errors_count_lbl.pack(side="left")

        # Rescan action button
        self.scanner_rescan_btn = ctk.CTkButton(
            stats_frame,
            text=t("scanner.btn_rescan"),
            font=FONTS["body_bold"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            width=80,
            height=32,
            command=self._on_rescan_clicked
        )
        self.scanner_rescan_btn.pack(side="right")

        # Breakdown row for completion state
        self.scanner_breakdown_lbl = ctk.CTkLabel(
            self.scanner_card,
            text="",
            font=FONTS["small"],
            text_color=COLORS["text_muted"]
        )
        self.scanner_breakdown_lbl.pack(anchor="w", padx=20, pady=(0, 4))

        # Authorization / trust hint label when 0 images found
        self.scanner_auth_hint_lbl = ctk.CTkLabel(
            self.scanner_card,
            text="",
            font=FONTS["body_bold"],
            text_color="#FFA500",  # Amber warning color
            wraplength=700,
            justify="left"
        )
        self.scanner_auth_hint_lbl.pack(anchor="w", padx=20, pady=(0, 8))
        self.scanner_auth_hint_lbl.pack_forget()

    def _build_scanning_overlay_ui(self):
        """Constructs elements for the full-fidelity scanning progress overlay."""
        content = ctk.CTkFrame(self.scanning_overlay, fg_color="transparent")
        content.place(relx=0.5, rely=0.5, anchor="center")

        # Scanning text indicator
        self.overlay_title_lbl = ctk.CTkLabel(
            content,
            text=t("scanner.modal_scanning"),
            font=FONTS["title_hero"],
            text_color=COLORS["primary"]
        )
        self.overlay_title_lbl.pack(pady=(0, 16))

        # Photos found counter
        self.overlay_photos_lbl = ctk.CTkLabel(
            content,
            text=t("scanner.modal_progress", count=0),
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.overlay_photos_lbl.pack(pady=(0, 12))

        # Time taken counter
        self.overlay_time_lbl = ctk.CTkLabel(
            content,
            text=t("scanner.modal_time", elapsed=0.0),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        self.overlay_time_lbl.pack(pady=(0, 12))

        # Current folder path
        self.overlay_path_lbl = ctk.CTkLabel(
            content,
            text=t("scanner.modal_path", path="..."),
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
            wraplength=550
        )
        self.overlay_path_lbl.pack()

    def _subscribe_to_scanner(self):
        """Subscribes to authoritative scanner updates."""
        def on_scanner_event(event_name: str, state_snapshot: Dict[str, Any]):
            # Safely schedule update on UI thread
            try:
                self.after(0, lambda s=state_snapshot: self._update_scanner_ui(s))
            except Exception:
                pass

        self._unsubscribe_scanner = authoritative_scanner.subscribe(on_scanner_event)
        # Apply initial state
        self._update_scanner_ui(authoritative_scanner.get_state())

    def _update_scanner_ui(self, state: Dict[str, Any]):
        conn_status = state.get("connection_status", "disconnected")
        scan_status = state.get("scan_status", ScanStatus.DISCONNECTED.value)
        device = state.get("device", "Apple iPhone") or "Apple iPhone"

        # Refresh sources list dynamically when connection changes
        self.refresh_sources()

        if conn_status != "connected":
            self.scanner_card.pack_forget()
            self._hide_scanning_overlay()
            return

        # Show scanner card above center card
        if not self.scanner_card.winfo_ismapped():
            if hasattr(self, "center_card") and self.center_card.winfo_manager() == "pack":
                self.scanner_card.pack(fill="x", padx=40, pady=(0, 10), before=self.center_card)
            else:
                self.scanner_card.pack(fill="x", padx=40, pady=(0, 10))

        self.scanner_title_lbl.configure(text=f"📱 {device} — Photo Scanner")
        self.scanner_status_badge.configure(text=scan_status)

        current_path = state.get("current_path") or "Apple iPhone\\Internal Storage"
        self.scanner_path_lbl.configure(text=f"Current Directory: {current_path}")

        images = state.get("images_found", 0)
        videos = state.get("videos_found", 0)
        files = state.get("files_scanned", 0)
        folders = state.get("folders_scanned", 0)
        errors = state.get("errors", 0)
        elapsed = state.get("elapsed_seconds", 0.0)

        self.images_count_lbl.configure(text=t("scanner.images_count", count=images))
        self.videos_count_lbl.configure(text=t("scanner.videos_count", count=videos))
        self.files_count_lbl.configure(text=t("scanner.total_files", count=files))
        self.folders_count_lbl.configure(text=t("scanner.folders_count", count=folders))
        self.errors_count_lbl.configure(text=t("scanner.errors_count", count=errors))

        if scan_status == ScanStatus.SCANNING.value:
            self.scanner_rescan_btn.configure(state="disabled")
            self.scanner_breakdown_lbl.configure(text=t("scanner.scan_in_progress"))
            self.scanner_auth_hint_lbl.pack_forget()
            self._show_scanning_overlay(images, elapsed, current_path)
        elif scan_status == ScanStatus.COMPLETE.value:
            self._hide_scanning_overlay()
            self.scanner_rescan_btn.configure(state="normal")
            by_ext = state.get("images_by_extension", {})
            current_scan_id = state.get("scan_id", 0)

            if by_ext:
                parts = [f"{ext}: {count}" for ext, count in sorted(by_ext.items())]
                self.scanner_breakdown_lbl.configure(text=t("scanner.image_breakdown", breakdown=", ".join(parts)))
                self.scanner_auth_hint_lbl.pack_forget()

                # Trigger date range filter dialog once per completed scan
                if self._last_completed_scan_id != current_scan_id and images > 0:
                    self._last_completed_scan_id = current_scan_id
                    self._show_date_filter_dialog(state)
            else:
                self.scanner_breakdown_lbl.configure(text=t("scanner.scan_complete_none"))
                self.scanner_auth_hint_lbl.configure(
                    text=t("scanner.error_auth_hint")
                )
                self.scanner_auth_hint_lbl.pack(anchor="w", padx=20, pady=(0, 8))
        elif scan_status == ScanStatus.ERROR.value:
            self._hide_scanning_overlay()
            self.scanner_rescan_btn.configure(state="normal")
            self.scanner_breakdown_lbl.configure(text=t("scanner.scan_error"))
            self.scanner_auth_hint_lbl.configure(
                text=t("scanner.error_general_hint")
            )
            self.scanner_auth_hint_lbl.pack(anchor="w", padx=20, pady=(0, 8))
        else:
            self._hide_scanning_overlay()
            self.scanner_rescan_btn.configure(state="normal")
            self.scanner_breakdown_lbl.configure(text="")
            self.scanner_auth_hint_lbl.pack_forget()

    def _show_scanning_overlay(self, photos_count: int, elapsed_seconds: float, current_path: str):
        """Displays and updates the scanning overlay with photos count and time taken."""
        self.overlay_photos_lbl.configure(text=t("scanner.modal_progress", count=photos_count))
        self.overlay_time_lbl.configure(text=t("scanner.modal_time", elapsed=elapsed_seconds))
        self.overlay_path_lbl.configure(text=t("scanner.modal_path", path=current_path))

        if not self.scanning_overlay.winfo_ismapped():
            # Place over center screen
            self.scanning_overlay.place(relx=0.5, rely=0.5, relwidth=0.7, relheight=0.45, anchor="center")
            self.scanning_overlay.lift()

    def _hide_scanning_overlay(self):
        """Hides the scanning overlay."""
        if hasattr(self, "scanning_overlay") and self.scanning_overlay.winfo_ismapped():
            self.scanning_overlay.place_forget()

    def _show_date_filter_dialog(self, state: Dict[str, Any]):
        """Displays dialog asking user to choose a date range for photos."""
        if self._date_filter_dialog and self._date_filter_dialog.winfo_exists():
            return

        dialog = ctk.CTkToplevel(self)
        self._date_filter_dialog = dialog
        dialog.title(t("scanner.date_filter_title"))
        dialog.geometry("560x420")
        dialog.grab_set()

        header_lbl = ctk.CTkLabel(
            dialog,
            text=t("scanner.date_filter_heading"),
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        header_lbl.pack(pady=(24, 6))

        sub_lbl = ctk.CTkLabel(
            dialog,
            text=t("scanner.date_filter_subheading"),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        sub_lbl.pack(pady=(0, 20))

        bounds = get_date_range_bounds()
        buttons_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons_frame.pack(fill="both", expand=True, padx=40, pady=(0, 20))

        ranges = [
            ("1_month", t("scanner.filter_1_month"), bounds["1_month"]["range_str"]),
            ("3_months", t("scanner.filter_3_months"), bounds["3_months"]["range_str"]),
            ("6_months", t("scanner.filter_6_months"), bounds["6_months"]["range_str"]),
            ("all", t("scanner.filter_all", count=len(state.get("discovered_photos", []))), bounds["all"]["range_str"]),
        ]

        for key, title, date_str in ranges:
            btn_text = f"{title}\n({date_str})"
            btn = ctk.CTkButton(
                buttons_frame,
                text=btn_text,
                font=FONTS["body_bold"],
                fg_color=COLORS["surface"],
                hover_color=COLORS["primary"],
                height=52,
                corner_radius=10,
                command=lambda k=key: self._on_date_range_selected(dialog, state, k)
            )
            btn.pack(fill="x", pady=6)

    def _on_date_range_selected(self, dialog: ctk.CTkToplevel, state: Dict[str, Any], range_key: str):
        dialog.destroy()
        self._date_filter_dialog = None

        session_id = state.get("session_id", 1)
        temp_dir = get_temp_session_dir(session_id)
        photos = state.get("discovered_photos", [])

        # Stage thumbnails in session temp directory based on selected time range
        stage_session_thumbnails(photos, session_id=session_id, range_key=range_key)

        # Automatically take this chosen device as source for images
        device_name = state.get("device", "Apple iPhone") or "Apple iPhone"
        self.on_source_selected(f"{device_name} (Session {session_id})", str(temp_dir))

    def _on_rescan_clicked(self):
        try:
            authoritative_scanner.request_rescan()
        except Exception:
            pass

    def destroy(self):
        if self._unsubscribe_scanner:
            try:
                self._unsubscribe_scanner()
            except Exception:
                pass
            self._unsubscribe_scanner = None
        super().destroy()

    def refresh_sources(self):
        for widget in self.sources_container.winfo_children():
            widget.destroy()

        # Fetch enabled source types from system settings
        from backend.database import get_db_engine, SystemSetting
        from sqlalchemy.orm import sessionmaker
        enabled_sources = {
            "source_iphone": True,
            "source_android": True,
            "source_removable": True,
            "source_cddvd": True,
            "source_network": True,
        }
        try:
            engine = get_db_engine()
            SessionLocal = sessionmaker(bind=engine)
            with SessionLocal() as db:
                for k in enabled_sources.keys():
                    setting = db.query(SystemSetting).filter_by(key=k).first()
                    if setting:
                        enabled_sources[k] = setting.value.lower() == "true"
        except Exception:
            pass

        # 0. Connected Phone / iPhone device (if connected and enabled)
        if enabled_sources.get("source_iphone", True):
            scanner_state = authoritative_scanner.get_state()
            if scanner_state.get("connection_status") == "connected":
                dev_name = scanner_state.get("device") or "Apple iPhone"
                self._add_source_button(
                    name=f"📱 {dev_name}",
                    path="wpd_iphone",
                    type_label=t("app.connected_mobile"),
                    action_text=t("app.scan_device_btn"),
                    on_click=self._start_scanning_source
                )

        # 1. Shared / SMB Photos folder (if network enabled)
        if enabled_sources.get("source_network", True):
            shared_path = os.path.abspath("storage/shared")
            network_name = t("app.network_shared")
            self._add_source_button(
                name=network_name,
                path=shared_path,
                type_label=t("app.server_storage"),
                action_text=t("app.look_here"),
                on_click=lambda p=shared_path, n=network_name: self.on_source_selected(n, p)
            )

        # 2. Removable devices from /media, /mnt, or Windows drive letters E:, F: etc.
        if enabled_sources.get("source_removable", True):
            removable = StorageManager.get_mounted_media_paths()
            for dev in removable:
                p = dev["path"]
                n = t("app.usb_removable", name=dev['name'])
                self._add_source_button(
                    name=n,
                    path=p,
                    type_label=t("app.removable_media"),
                    action_text=t("app.look_here"),
                    on_click=lambda path=p, name=n: self.on_source_selected(name, path)
                )

        # 3. CD / DVD drive (optical discs)
        if enabled_sources.get("source_cddvd", True):
            optical_devices = StorageManager.get_optical_media_paths()
            for dev in optical_devices:
                p = dev["path"]
                n = dev["name"]
                self._add_source_button(
                    name=n,
                    path=p,
                    type_label=t("app.optical_media"),
                    action_text=t("app.look_here"),
                    on_click=lambda path=p, name=n: self.on_source_selected(name, path)
                )

    def _start_scanning_source(self):
        """Starts scanning the connected mobile device upon user selection."""
        try:
            authoritative_scanner.request_rescan()
        except RuntimeError:
            pass  # Already scanning

    def _add_source_button(self, name: str, path: str, type_label: str, action_text: Optional[str] = None, on_click: Optional[Callable[[], None]] = None):
        action_text = action_text or t("app.look_here")
        row = ctk.CTkFrame(self.sources_container, fg_color=COLORS["surface"], corner_radius=12)
        row.pack(fill="x", pady=8, ipady=6)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=20, pady=10)

        lbl_name = ctk.CTkLabel(info, text=name, font=FONTS["subtitle"], text_color=COLORS["text"])
        lbl_name.pack(anchor="w")

        lbl_path = ctk.CTkLabel(info, text=t("app.type_path_info", type_label=type_label, path=path), font=FONTS["small"], text_color=COLORS["text_muted"])
        lbl_path.pack(anchor="w")

        cmd = on_click if on_click else (lambda: self.on_source_selected(name, path))

        btn = ctk.CTkButton(
            row,
            text=action_text,
            font=FONTS["button"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=cmd
        )
        btn.pack(side="right", padx=20)
