"""Screen 1: Welcome & Device Selection screen with live iPhone WPD scanner status card."""
import os
import customtkinter as ctk
from typing import Callable, Dict, Any, List, Optional
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from devices.storage import StorageManager
from devices.scanner import authoritative_scanner, ScanStatus


class HomeScreen(ctk.CTkFrame):
    def __init__(self, parent, on_source_selected: Callable[[str, str], None]):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.on_source_selected = on_source_selected
        self._unsubscribe_scanner: Optional[Callable[[], None]] = None

        self._build_ui()
        self._subscribe_to_scanner()

    def _build_ui(self):
        # Top banner
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=40, pady=(30, 10))

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
        subtitle.pack(anchor="w", pady=(4, 0))

        # iPhone Scanner Status Banner / Card (Visible when connected)
        self.scanner_card = ctk.CTkFrame(self, fg_color=COLORS["surface"], corner_radius=14)
        self._build_scanner_card_ui()

        # Main action card / device selection area
        self.center_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16)
        self.center_card.pack(fill="both", expand=True, padx=40, pady=(10, 20))

        card_title = ctk.CTkLabel(
            self.center_card,
            text="Select Photo Source",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        card_title.pack(anchor="w", padx=30, pady=(20, 10))

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
            text="CONNECTED",
            font=FONTS["body_bold"],
            text_color=COLORS["primary"]
        )
        self.scanner_status_badge.pack(side="right")

        # Path row
        self.scanner_path_lbl = ctk.CTkLabel(
            self.scanner_card,
            text="Path: Internal Storage",
            font=FONTS["small"],
            text_color=COLORS["text_muted"]
        )
        self.scanner_path_lbl.pack(anchor="w", padx=20, pady=(0, 4))

        # Counters row
        stats_frame = ctk.CTkFrame(self.scanner_card, fg_color="transparent")
        stats_frame.pack(fill="x", padx=20, pady=(0, 10))

        self.images_count_lbl = ctk.CTkLabel(
            stats_frame,
            text="Images: 0",
            font=FONTS["body_bold"],
            text_color=COLORS["primary"]
        )
        self.images_count_lbl.pack(side="left", padx=(0, 16))

        self.videos_count_lbl = ctk.CTkLabel(
            stats_frame,
            text="Videos: 0",
            font=FONTS["body_bold"],
            text_color=COLORS["text"]
        )
        self.videos_count_lbl.pack(side="left", padx=(0, 16))

        self.files_count_lbl = ctk.CTkLabel(
            stats_frame,
            text="Total Files: 0",
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.files_count_lbl.pack(side="left", padx=(0, 16))

        self.folders_count_lbl = ctk.CTkLabel(
            stats_frame,
            text="Folders: 0",
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.folders_count_lbl.pack(side="left", padx=(0, 16))

        self.errors_count_lbl = ctk.CTkLabel(
            stats_frame,
            text="Errors: 0",
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.errors_count_lbl.pack(side="left")

        # Rescan action button
        self.scanner_rescan_btn = ctk.CTkButton(
            stats_frame,
            text="Rescan",
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
        self.scanner_breakdown_lbl.pack(anchor="w", padx=20, pady=(0, 8))

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

        if conn_status != "connected":
            self.scanner_card.pack_forget()
            return

        # Show scanner card above center card
        if not self.scanner_card.winfo_ismapped():
            if hasattr(self, "center_card") and self.center_card.winfo_manager() == "pack":
                self.scanner_card.pack(fill="x", padx=40, pady=(0, 10), before=self.center_card)
            else:
                self.scanner_card.pack(fill="x", padx=40, pady=(0, 10))

        self.scanner_title_lbl.configure(text=f"📱 {device} — Photo Scanner")
        self.scanner_status_badge.configure(text=scan_status)

        current_path = state.get("current_path") or "Internal Storage"
        self.scanner_path_lbl.configure(text=f"Current Directory: {current_path}")

        images = state.get("images_found", 0)
        videos = state.get("videos_found", 0)
        files = state.get("files_scanned", 0)
        folders = state.get("folders_scanned", 0)
        errors = state.get("errors", 0)

        self.images_count_lbl.configure(text=f"Images: {images}")
        self.videos_count_lbl.configure(text=f"Videos: {videos}")
        self.files_count_lbl.configure(text=f"Total Files: {files}")
        self.folders_count_lbl.configure(text=f"Folders: {folders}")
        self.errors_count_lbl.configure(text=f"Errors: {errors}")

        if scan_status == ScanStatus.SCANNING.value:
            self.scanner_rescan_btn.configure(state="disabled")
            self.scanner_breakdown_lbl.configure(text="Scanning in progress...")
        elif scan_status == ScanStatus.COMPLETE.value:
            self.scanner_rescan_btn.configure(state="normal")
            by_ext = state.get("images_by_extension", {})
            if by_ext:
                parts = [f"{ext}: {count}" for ext, count in sorted(by_ext.items())]
                self.scanner_breakdown_lbl.configure(text=f"Image breakdown: {', '.join(parts)}")
            else:
                self.scanner_breakdown_lbl.configure(text="Scan complete. No images found.")
        elif scan_status == ScanStatus.ERROR.value:
            self.scanner_rescan_btn.configure(state="normal")
            self.scanner_breakdown_lbl.configure(text="Scan encountered an error.")
        else:
            self.scanner_rescan_btn.configure(state="normal")
            self.scanner_breakdown_lbl.configure(text="")

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
