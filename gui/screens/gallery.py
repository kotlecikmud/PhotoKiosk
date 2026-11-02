"""Screen 2: Asynchronous photo gallery browser and multi-selection."""
import threading
import customtkinter as ctk
from PIL import Image
from pathlib import Path
from typing import Callable, List, Dict, Any, Set
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from backend.library import scan_directory_for_photos
from backend.thumbnails import ThumbnailCache


class GalleryScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        source_name: str,
        source_path: str,
        on_back: Callable[[], None],
        on_proceed_to_config: Callable[[List[Dict[str, Any]]], None]
    ):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.source_name = source_name
        self.source_path = source_path
        self.on_back = on_back
        self.on_proceed_to_config = on_proceed_to_config

        self.photos: List[Dict[str, Any]] = []
        self.selected_indices: Set[int] = set()
        self.thumb_cache = ThumbnailCache()
        self.card_widgets: Dict[int, ctk.CTkFrame] = {}

        self._build_ui()
        self._load_photos_async()

    def _build_ui(self):
        # Header
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=30, pady=(20, 10))

        back_btn = ctk.CTkButton(
            top_bar,
            text="← Sources",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=120,
            height=TOUCH_SIZES["button_height"],
            command=self.on_back
        )
        back_btn.pack(side="left")

        self.title_lbl = ctk.CTkLabel(
            top_bar,
            text=f"Browsing: {self.source_name}",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.title_lbl.pack(side="left", padx=20)

        # Action buttons on right
        self.select_all_btn = ctk.CTkButton(
            top_bar,
            text="Select All",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            height=TOUCH_SIZES["button_height"],
            command=self._toggle_select_all
        )
        self.select_all_btn.pack(side="right", padx=(10, 0))

        # Sort selector
        self.sort_var = ctk.StringVar(value="Name")
        sort_menu = ctk.CTkOptionMenu(
            top_bar,
            values=["Name", "Date Modified", "File Size"],
            variable=self.sort_var,
            font=FONTS["body_bold"],
            height=TOUCH_SIZES["button_height"],
            command=self._on_sort_changed
        )
        sort_menu.pack(side="right")

        # Scrollable gallery container
        self.gallery_frame = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        self.gallery_frame.pack(fill="both", expand=True, padx=30, pady=10)

        # Loading banner
        self.status_lbl = ctk.CTkLabel(
            self.gallery_frame,
            text="Scanning files and generating thumbnails...",
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        self.status_lbl.pack(pady=40)

        # Bottom selection summary bar
        bottom_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        bottom_bar.pack(fill="x", padx=30, pady=(0, 20), ipady=8)

        self.summary_lbl = ctk.CTkLabel(
            bottom_bar,
            text="0 photographs selected",
            font=FONTS["subtitle"],
            text_color=COLORS["text"]
        )
        self.summary_lbl.pack(side="left", padx=20)

        self.proceed_btn = ctk.CTkButton(
            bottom_bar,
            text="Configure Prints →",
            font=FONTS["button"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            state="disabled",
            command=self._proceed
        )
        self.proceed_btn.pack(side="right", padx=20)

    def _load_photos_async(self):
        def worker():
            found = scan_directory_for_photos(self.source_path)
            self.after(0, lambda: self._on_photos_loaded(found))
        threading.Thread(target=worker, daemon=True).start()

    def _on_photos_loaded(self, found: List[Dict[str, Any]]):
        self.status_lbl.destroy()
        self.photos = found
        if not self.photos:
            empty_lbl = ctk.CTkLabel(
                self.gallery_frame,
                text="No supported photographs found in this source folder.",
                font=FONTS["subtitle"],
                text_color=COLORS["text_muted"]
            )
            empty_lbl.pack(pady=60)
            return

        self._render_grid()

    def _render_grid(self):
        for w in self.gallery_frame.winfo_children():
            w.destroy()
        self.card_widgets.clear()

        # Render 4 columns grid
        columns = 4
        for idx, photo in enumerate(self.photos):
            row = idx // columns
            col = idx % columns

            card = ctk.CTkFrame(self.gallery_frame, fg_color=COLORS["surface"], corner_radius=10)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            self.gallery_frame.grid_columnconfigure(col, weight=1)

            # Thumbnail container
            thumb_btn = ctk.CTkButton(
                card,
                text=photo["filename"][:20],
                font=FONTS["body_bold"],
                fg_color="transparent",
                hover_color=COLORS["border"],
                height=140,
                command=lambda i=idx: self._toggle_selection(i)
            )
            thumb_btn.pack(fill="both", expand=True, padx=8, pady=(8, 4))

            # Async load thumbnail into button
            self._load_thumbnail_async(photo["filepath"], thumb_btn)

            # Metadata label
            size_kb = round(photo["size_bytes"] / 1024, 1)
            lbl = ctk.CTkLabel(card, text=f"{size_kb} KB", font=FONTS["small"], text_color=COLORS["text_muted"])
            lbl.pack(pady=(0, 6))

            self.card_widgets[idx] = card

    def _load_thumbnail_async(self, filepath: str, btn: ctk.CTkButton):
        def worker():
            try:
                t_path = self.thumb_cache.generate_thumbnail(filepath)
                pil_img = Image.open(t_path)
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(120, 100))
                self.after(0, lambda: btn.configure(image=ctk_img, text=""))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_selection(self, idx: int):
        if idx in self.selected_indices:
            self.selected_indices.remove(idx)
        else:
            self.selected_indices.add(idx)
        self._update_card_visual(idx)
        self._update_bottom_bar()

    def _update_card_visual(self, idx: int):
        card = self.card_widgets.get(idx)
        if not card:
            return
        if idx in self.selected_indices:
            card.configure(fg_color=COLORS["bg_card_selected"])
        else:
            card.configure(fg_color=COLORS["surface"])

    def _toggle_select_all(self):
        if len(self.selected_indices) == len(self.photos):
            self.selected_indices.clear()
            self.select_all_btn.configure(text="Select All")
        else:
            self.selected_indices = set(range(len(self.photos)))
            self.select_all_btn.configure(text="Deselect All")

        for idx in range(len(self.photos)):
            self._update_card_visual(idx)
        self._update_bottom_bar()

    def _update_bottom_bar(self):
        count = len(self.selected_indices)
        self.summary_lbl.configure(text=f"{count} photograph{'s' if count != 1 else ''} selected")
        if count > 0:
            self.proceed_btn.configure(state="normal")
        else:
            self.proceed_btn.configure(state="disabled")

    def _on_sort_changed(self, choice: str):
        if choice == "Name":
            self.photos.sort(key=lambda x: x["filename"].lower())
        elif choice == "Date Modified":
            self.photos.sort(key=lambda x: x["modified_time"], reverse=True)
        elif choice == "File Size":
            self.photos.sort(key=lambda x: x["size_bytes"], reverse=True)
        self.selected_indices.clear()
        self._render_grid()
        self._update_bottom_bar()

    def _proceed(self):
        selected_photos = [self.photos[i] for i in sorted(self.selected_indices)]
        self.on_proceed_to_config(selected_photos)
