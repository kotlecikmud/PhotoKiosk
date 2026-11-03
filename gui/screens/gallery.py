"""Screen 2: Split-screen photo gallery browser & individual photo print editor."""
import os
import shutil
import threading
from pathlib import Path
from typing import Callable, List, Dict, Any, Set, Optional

import customtkinter as ctk
from PIL import Image
from sqlalchemy.orm import sessionmaker

from gui.theme import FONTS, COLORS, TOUCH_SIZES
from backend.library import scan_directory_for_photos
from backend.thumbnails import ThumbnailCache
from backend.database import get_db_engine, PrintSize, PaperFinish, get_all_service_prices_map
from imaging.preview import generate_preview
from devices.session_manager import get_temp_session_dir
from backend.i18n import t, get_i18n
from gui.language_toggle import LanguageToggle


class GalleryScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        source_name: str,
        source_path: str,
        session_id: int = 1,
        on_back: Callable[[], None] = None,
        on_proceed_to_summary: Callable[[List[Dict[str, Any]], Dict[str, Any], Set[int]], None] = None,
        initial_photo_configs: Optional[Dict[str, Dict[str, Any]]] = None,
        initial_selected_indices: Optional[Set[int]] = None,
        initial_session_card_sizes: Optional[List[str]] = None,
    ):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.source_name = source_name
        self.source_path = source_path
        self.session_id = session_id
        self.on_back = on_back
        self.on_proceed_to_summary = on_proceed_to_summary

        self.photos: List[Dict[str, Any]] = []
        self.selected_indices: Set[int] = set(initial_selected_indices) if initial_selected_indices else set()
        self.active_photo_idx: Optional[int] = None
        self.thumb_cache = ThumbnailCache()

        # Density: 4, 8, 16 per row
        self.density_var = ctk.IntVar(value=4)

        # Database print sizes & active finishes
        engine = get_db_engine()
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            self.active_sizes = db.query(PrintSize).filter_by(is_active=True).order_by(PrintSize.sort_order).all()
            self.active_finishes = db.query(PaperFinish).filter_by(is_active=True).order_by(PaperFinish.sort_order).all()
            self.service_prices = get_all_service_prices_map(db)
        finally:
            db.close()

        self.size_names = [s.name for s in self.active_sizes] or ["9x13", "10x15"]
        self.finish_names = [f.name for f in self.active_finishes] or ["GLOSSY", "MATTE"]

        # Default formats list across session: starts with first 2 active print sizes
        if initial_session_card_sizes:
            self.session_card_sizes = list(initial_session_card_sizes)
        else:
            self.session_card_sizes = self.size_names[:2] if len(self.size_names) >= 2 else list(self.size_names)

        # Photo configurations:
        # photo_filepath -> {
        #    "crop_mode": "fill_in",
        #    "cards": [ {"size_name": s, "paper_type": self.finish_names[0], "count": 0}, ... ]
        # }
        self.photo_configs: Dict[str, Dict[str, Any]] = dict(initial_photo_configs) if initial_photo_configs else {}

        # Track staged copies for background copy / cleanup
        # original_filepath -> staged_filepath
        self.staged_files: Dict[str, str] = {}

        # UI references
        self.tile_cards: Dict[int, ctk.CTkFrame] = {}
        self.tile_checkboxes: Dict[int, ctk.CTkCheckBox] = {}
        self.tile_cb_vars: Dict[int, ctk.BooleanVar] = {}

        # Active card index tracking per photo: filepath -> int
        self.active_card_indices: Dict[str, int] = {}

        # Dropdown popup reference & timer
        self.dropdown_popup: Optional[ctk.CTkFrame] = None
        self.dropdown_timer_id: Optional[str] = None

        self._build_ui()
        get_i18n().subscribe(self._on_locale_changed)
        self._load_photos_async()

    def destroy(self):
        try:
            get_i18n().unsubscribe(self._on_locale_changed)
        except Exception:
            pass
        super().destroy()

    def _on_locale_changed(self, lang: str):
        if self.winfo_exists():
            self._update_localized_texts()
            self._render_editor()

    def _get_price(self, size_name: str, paper_type: str) -> float:
        key = (size_name.strip().lower(), paper_type.strip().lower())
        return self.service_prices.get(key, 0.0)

    def _init_photo_config(self, filepath: str):
        if filepath not in self.photo_configs:
            cards = []
            default_finish = self.finish_names[0] if self.finish_names else "GLOSSY"
            for s in self.session_card_sizes:
                cards.append({
                    "size_name": s,
                    "paper_type": default_finish,
                    "count": 0
                })
            self.photo_configs[filepath] = {
                "crop_mode": "fill_in",
                "cards": cards
            }
        if filepath not in self.active_card_indices:
            self.active_card_indices[filepath] = 0

    def _get_active_card_idx(self, filepath: str) -> int:
        cfg = self.photo_configs.get(filepath, {})
        cards = cfg.get("cards", [])
        if not cards:
            return 0
        idx = self.active_card_indices.get(filepath, 0)
        if idx >= len(cards) or idx < 0:
            idx = 0
            self.active_card_indices[filepath] = 0
        return idx

    def _set_active_card_idx(self, filepath: str, idx: int):
        cfg = self.photo_configs.get(filepath, {})
        cards = cfg.get("cards", [])
        if not cards:
            self.active_card_indices[filepath] = 0
        else:
            self.active_card_indices[filepath] = max(0, min(idx, len(cards) - 1))

    def _select_active_card(self, card_idx: int):
        self._close_dropdown_popup()
        if self.active_photo_idx is None or not self.photos:
            return
        photo = self.photos[self.active_photo_idx]
        self._set_active_card_idx(photo["filepath"], card_idx)
        self._render_size_cards()
        self._update_editor_preview()

    def _build_ui(self):
        # 1. Top Bar spanning full width
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=20, pady=(15, 10))

        self.back_btn = ctk.CTkButton(
            self.top_bar,
            text=f"← {t('gallery.back_to_sources')}",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=110,
            height=TOUCH_SIZES["button_height"],
            command=self.on_back
        )
        self.back_btn.pack(side="left")

        self.title_lbl = ctk.CTkLabel(
            self.top_bar,
            text=f"{t('gallery.title')}: {self.source_name}",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.title_lbl.pack(side="left", padx=16)

        # Counter of selected images next to density controls
        self.selected_counter_lbl = ctk.CTkLabel(
            self.top_bar,
            text=f"{t('gallery.selected')}: 0",
            font=FONTS["subtitle"],
            text_color=COLORS["primary"]
        )
        self.selected_counter_lbl.pack(side="left", padx=15)

        # Density controls (4, 8, 16 per row)
        density_frame = ctk.CTkFrame(self.top_bar, fg_color=COLORS["surface"], corner_radius=8)
        density_frame.pack(side="left", padx=10)

        self.density_lbl = ctk.CTkLabel(density_frame, text=f"{t('gallery.density')}:", font=FONTS["body_bold"])
        self.density_lbl.pack(side="left", padx=(10, 6), pady=4)

        for d in [4, 8, 16]:
            btn = ctk.CTkButton(
                density_frame,
                text=str(d),
                width=36,
                height=32,
                font=FONTS["small"],
                fg_color=COLORS["primary"] if self.density_var.get() == d else "transparent",
                hover_color=COLORS["primary_hover"],
                command=lambda val=d: self._set_density(val)
            )
            btn.pack(side="left", padx=2, pady=4)

        # Persistent Language Toggle on top bar right
        self.lang_toggle = LanguageToggle(self.top_bar)
        self.lang_toggle.pack(side="right", padx=(10, 0))

        # Proceed to Order Summary button on right of top bar
        self.proceed_btn = ctk.CTkButton(
            self.top_bar,
            text=f"{t('gallery.proceed_to_summary')} →",
            font=FONTS["button"],
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            height=TOUCH_SIZES["button_height"],
            state="disabled",
            command=self._proceed_to_summary
        )
        self.proceed_btn.pack(side="right")

        # Select all toggle
        self.select_all_btn = ctk.CTkButton(
            self.top_bar,
            text=t("gallery.select_all"),
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            height=TOUCH_SIZES["button_height"],
            command=self._toggle_select_all
        )
        self.select_all_btn.pack(side="right", padx=10)

        # 2. Main split-view body: Left 50% gallery, Right 50% editor
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        self.body.grid_columnconfigure(0, weight=1, uniform="half")
        self.body.grid_columnconfigure(1, weight=1, uniform="half")
        self.body.grid_rowconfigure(0, weight=1)

        # Left: Gallery Scroll Container
        self.left_pane = ctk.CTkFrame(self.body, fg_color=COLORS["bg_card"], corner_radius=12)
        self.left_pane.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.gallery_frame = ctk.CTkScrollableFrame(self.left_pane, fg_color="transparent")
        self.gallery_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.gallery_frame.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self.status_lbl = ctk.CTkLabel(
            self.gallery_frame,
            text=t("gallery.scanning_msg"),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        self.status_lbl.pack(pady=60)

        # Right: Editor Pane
        self.right_pane = ctk.CTkFrame(self.body, fg_color=COLORS["bg_card"], corner_radius=12)
        self.right_pane.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.right_scroll = ctk.CTkScrollableFrame(self.right_pane, fg_color="transparent")
        self.right_scroll.pack(fill="both", expand=True, padx=14, pady=14)
        self.right_scroll.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self._build_editor_placeholder()

    def _build_editor_placeholder(self):
        for w in self.right_scroll.winfo_children():
            w.destroy()
        placeholder = ctk.CTkLabel(
            self.right_scroll,
            text=t("gallery.tap_image_prompt"),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        placeholder.pack(pady=100)

    def _set_density(self, density: int):
        self.density_var.set(density)
        # Update density buttons color
        for child in self.top_bar.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                for b in child.winfo_children():
                    if isinstance(b, ctk.CTkButton) and b.cget("text") in ["4", "8", "16"]:
                        b.configure(fg_color=COLORS["primary"] if b.cget("text") == str(density) else "transparent")
        self._render_grid()

    def _load_photos_async(self):
        def worker():
            found = scan_directory_for_photos(self.source_path)
            # In-process update
            self.after(0, lambda: self._on_photos_loaded(found))
        threading.Thread(target=worker, daemon=True).start()

    def _on_photos_loaded(self, found: List[Dict[str, Any]]):
        if self.status_lbl.winfo_exists():
            self.status_lbl.destroy()
        self.photos = found
        if not self.photos:
            empty_lbl = ctk.CTkLabel(
                self.gallery_frame,
                text=t("gallery.no_photos"),
                font=FONTS["subtitle"],
                text_color=COLORS["text_muted"]
            )
            empty_lbl.pack(pady=60)
            return

        for p in self.photos:
            self._init_photo_config(p["filepath"])

        # Auto-select the first photo in the editor
        self.active_photo_idx = 0
        self._render_grid()
        self._render_editor()

    def _render_grid(self):
        for w in self.gallery_frame.winfo_children():
            w.destroy()
        self.tile_cards.clear()
        self.tile_checkboxes.clear()
        self.tile_cb_vars.clear()

        columns = self.density_var.get()
        # Reset grid weights
        for c in range(16):
            self.gallery_frame.grid_columnconfigure(c, weight=0)
        for c in range(columns):
            self.gallery_frame.grid_columnconfigure(c, weight=1)

        # Scale thumbnail visual dimensions with density
        if columns == 4:
            thumb_size = (90, 75)
            card_pad = 6
        elif columns == 8:
            thumb_size = (50, 42)
            card_pad = 3
        else:
            thumb_size = (28, 24)
            card_pad = 2

        for idx, photo in enumerate(self.photos):
            row = idx // columns
            col = idx % columns

            is_active = (idx == self.active_photo_idx)
            card_color = COLORS["primary_hover"] if is_active else COLORS["surface"]

            card = ctk.CTkFrame(self.gallery_frame, fg_color=card_color, corner_radius=6)
            card.grid(row=row, column=col, padx=card_pad, pady=card_pad, sticky="nsew")
            card.bind("<Button-1>", lambda e, i=idx: self._select_photo_for_editing(i))

            # Tile Checkbox
            cb_var = ctk.BooleanVar(value=(idx in self.selected_indices))
            self.tile_cb_vars[idx] = cb_var

            cb = ctk.CTkCheckBox(
                card,
                text="",
                variable=cb_var,
                width=18,
                height=18,
                checkbox_width=18,
                checkbox_height=18,
                command=lambda i=idx: self._on_tile_checkbox_toggled(i)
            )
            cb.pack(anchor="ne", padx=3, pady=2)
            self.tile_checkboxes[idx] = cb

            # Thumbnail Button
            thumb_btn = ctk.CTkButton(
                card,
                text="",
                fg_color="transparent",
                hover_color=COLORS["border"],
                width=thumb_size[0],
                height=thumb_size[1],
                command=lambda i=idx: self._select_photo_for_editing(i)
            )
            thumb_btn.pack(padx=2, pady=(0, 3), fill="both", expand=True)

            self._load_thumbnail_async(photo["filepath"], thumb_btn, thumb_size)
            self.tile_cards[idx] = card

    def _load_thumbnail_async(self, filepath: str, btn: ctk.CTkButton, size: tuple):
        def worker():
            try:
                t_path = self.thumb_cache.generate_thumbnail(filepath)
                pil_img = Image.open(t_path)
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
                if btn.winfo_exists():
                    self.after(0, lambda: btn.configure(image=ctk_img, text=""))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _select_photo_for_editing(self, idx: int):
        self._close_dropdown_popup()
        if idx < 0 or idx >= len(self.photos):
            return

        old_idx = self.active_photo_idx
        self.active_photo_idx = idx

        # Update tile backgrounds
        if old_idx is not None and old_idx in self.tile_cards:
            self.tile_cards[old_idx].configure(fg_color=COLORS["surface"])
        if idx in self.tile_cards:
            self.tile_cards[idx].configure(fg_color=COLORS["primary_hover"])

        self._render_editor()

    def _on_tile_checkbox_toggled(self, idx: int):
        self._close_dropdown_popup()
        is_checked = self.tile_cb_vars[idx].get()
        if is_checked:
            self._select_image_for_order(idx)
        else:
            self._deselect_image_from_order(idx)

    def _select_image_for_order(self, idx: int):
        self.selected_indices.add(idx)
        if idx in self.tile_cb_vars:
            self.tile_cb_vars[idx].set(True)

        # If all counts are 0, set the first card's count to 1 by default
        p = self.photos[idx]
        cfg = self.photo_configs[p["filepath"]]
        total_counts = sum(c["count"] for c in cfg["cards"])
        if total_counts == 0 and cfg["cards"]:
            cfg["cards"][0]["count"] = 1

        self._background_copy_photo(p["filepath"])
        self._update_selection_states()
        if self.active_photo_idx == idx:
            self._render_editor()

    def _deselect_image_from_order(self, idx: int):
        self.selected_indices.discard(idx)
        if idx in self.tile_cb_vars:
            self.tile_cb_vars[idx].set(False)

        # Reset counts for this photo to 0
        p = self.photos[idx]
        if p["filepath"] in self.photo_configs:
            for c in self.photo_configs[p["filepath"]]["cards"]:
                c["count"] = 0

        self._update_selection_states()
        if self.active_photo_idx == idx:
            self._render_editor()

    def _toggle_select_all(self):
        self._close_dropdown_popup()
        if len(self.selected_indices) == len(self.photos):
            # Deselect all
            for i in range(len(self.photos)):
                self._deselect_image_from_order(i)
            self.select_all_btn.configure(text="Select All")
        else:
            # Select all
            for i in range(len(self.photos)):
                self._select_image_for_order(i)
            self.select_all_btn.configure(text="Deselect All")

    def _update_selection_states(self):
        count = len(self.selected_indices)
        self.selected_counter_lbl.configure(text=f"{t('gallery.selected')}: {count}")
        if count == len(self.photos) and len(self.photos) > 0:
            self.select_all_btn.configure(text=t("gallery.deselect_all"))
        else:
            self.select_all_btn.configure(text=t("gallery.select_all"))

        # Total prints count
        total_prints = 0
        for i in self.selected_indices:
            p = self.photos[i]
            cfg = self.photo_configs.get(p["filepath"], {})
            total_prints += sum(c.get("count", 0) for c in cfg.get("cards", []))

        if count > 0 and total_prints > 0:
            self.proceed_btn.configure(state="normal", text=f"{t('gallery.proceed_to_summary')} ({total_prints}) →")
        else:
            self.proceed_btn.configure(state="disabled", text=f"{t('gallery.proceed_to_summary')} →")

    def _update_localized_texts(self):
        self.back_btn.configure(text=f"← {t('gallery.back_to_sources')}")
        self.title_lbl.configure(text=f"{t('gallery.title')}: {self.source_name}")
        self.density_lbl.configure(text=f"{t('gallery.density')}:")
        self._update_selection_states()

    def _background_copy_photo(self, filepath: str):
        """Copies selected photo in background into temporary session folder."""
        def worker():
            try:
                temp_dir = get_temp_session_dir(self.session_id)
                src = Path(filepath)
                dest = temp_dir / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                self.staged_files[filepath] = str(dest)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------
    # Right-Half Photo Editor & Size Cards
    # -------------------------------------------------------------
    def _render_editor(self):
        self._close_dropdown_popup()
        for w in self.right_scroll.winfo_children():
            w.destroy()

        if self.active_photo_idx is None or not self.photos:
            self._build_editor_placeholder()
            return

        photo = self.photos[self.active_photo_idx]
        filepath = photo["filepath"]
        self._init_photo_config(filepath)
        cfg = self.photo_configs[filepath]
        is_selected = (self.active_photo_idx in self.selected_indices)

        # Header of editor: filename & "Add to order" checkbox
        hdr_frame = ctk.CTkFrame(self.right_scroll, fg_color="transparent")
        hdr_frame.pack(fill="x", pady=(0, 10))

        info_box = ctk.CTkFrame(hdr_frame, fg_color="transparent")
        info_box.pack(side="left", fill="both", expand=True)

        name_lbl = ctk.CTkLabel(info_box, text=photo["filename"], font=FONTS["subtitle"], text_color=COLORS["text"])
        name_lbl.pack(anchor="w")

        size_kb = round(photo["size_bytes"] / 1024, 1)
        meta_lbl = ctk.CTkLabel(info_box, text=f"Size: {size_kb} KB", font=FONTS["small"], text_color=COLORS["text_muted"])
        meta_lbl.pack(anchor="w")

        # Sidebar "Add to order" checkbox
        self.editor_cb_var = ctk.BooleanVar(value=is_selected)
        editor_cb = ctk.CTkCheckBox(
            hdr_frame,
            text=t("gallery.add_to_order"),
            font=FONTS["body_bold"],
            variable=self.editor_cb_var,
            command=self._on_editor_checkbox_toggled
        )
        editor_cb.pack(side="right", padx=10)

        # Image Viewport / Preview
        viewport_frame = ctk.CTkFrame(self.right_scroll, fg_color=COLORS["surface"], corner_radius=10)
        viewport_frame.pack(fill="x", pady=(0, 10))
        viewport_frame.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self.canvas_info_lbl = ctk.CTkLabel(
            viewport_frame,
            text="",
            font=FONTS["small"],
            text_color=COLORS["primary"]
        )
        self.canvas_info_lbl.pack(pady=(6, 2))
        self.canvas_info_lbl.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self.preview_lbl = ctk.CTkLabel(viewport_frame, text="Loading preview...", font=FONTS["small"])
        self.preview_lbl.pack(pady=(2, 10), fill="both", expand=True)
        self.preview_lbl.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        # Crop mode selector
        crop_frame = ctk.CTkFrame(self.right_scroll, fg_color="transparent")
        crop_frame.pack(fill="x", pady=(0, 10))

        crop_lbl = ctk.CTkLabel(crop_frame, text=f"{t('gallery.crop_mode')}:", font=FONTS["body_bold"], text_color=COLORS["primary"])
        crop_lbl.pack(side="left", padx=(0, 10))

        self.crop_mode_var = ctk.StringVar(value=cfg.get("crop_mode", "fill_in"))
        crop_options = [
            (t("crop_modes.fill_in"), "fill_in"),
            (t("crop_modes.fit_in"), "fit_in"),
            (t("crop_modes.border"), "border"),
            (t("crop_modes.no_resize"), "no_resize"),
        ]
        for label, val in crop_options:
            rb = ctk.CTkRadioButton(
                crop_frame,
                text=label,
                value=val,
                variable=self.crop_mode_var,
                font=FONTS["small"],
                command=self._on_crop_mode_changed
            )
            rb.pack(side="left", padx=6)

        # Horizontal size count cards container
        cards_title = ctk.CTkLabel(self.right_scroll, text=f"{t('gallery.sizes_and_quantities')}:", font=FONTS["body_bold"])
        cards_title.pack(anchor="w", pady=(0, 4))

        self.cards_row = ctk.CTkScrollableFrame(self.right_scroll, fg_color="transparent", orientation="horizontal", height=280)
        self.cards_row.pack(fill="x", pady=(0, 10))
        self.cards_row.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self._render_size_cards()
        self._update_editor_preview()

    def _on_editor_checkbox_toggled(self):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        if self.editor_cb_var.get():
            self._select_image_for_order(self.active_photo_idx)
        else:
            self._deselect_image_from_order(self.active_photo_idx)

    def _on_crop_mode_changed(self):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        photo = self.photos[self.active_photo_idx]
        self.photo_configs[photo["filepath"]]["crop_mode"] = self.crop_mode_var.get()
        self._update_editor_preview()

    def _render_size_cards(self):
        for w in self.cards_row.winfo_children():
            w.destroy()

        if self.active_photo_idx is None or not self.photos:
            return

        photo = self.photos[self.active_photo_idx]
        cfg = self.photo_configs[photo["filepath"]]
        cards = cfg.get("cards", [])
        active_idx = self._get_active_card_idx(photo["filepath"])

        card_width = 145
        card_height = 215

        if not cards:
            empty_box = ctk.CTkFrame(self.cards_row, fg_color="transparent")
            empty_box.pack(side="left", padx=10, pady=20)
            no_cards_lbl = ctk.CTkLabel(
                empty_box,
                text=t("gallery.no_sizes_added"),
                font=FONTS["body"],
                text_color=COLORS["text_muted"]
            )
            no_cards_lbl.pack(pady=10)

        # Render horizontal cards side-by-side
        for c_idx, card_data in enumerate(cards):
            is_active = (c_idx == active_idx)

            card_col = ctk.CTkFrame(self.cards_row, fg_color="transparent", width=card_width)
            card_col.pack(side="left", padx=8, pady=4)

            # The card itself with active border highlight
            card_border_color = COLORS["primary"] if is_active else COLORS["border"]
            card_border_width = 3 if is_active else 1
            card_bg_color = COLORS.get("surface_light", "#242d3d") if is_active else COLORS["surface"]

            card = ctk.CTkFrame(
                card_col,
                fg_color=card_bg_color,
                border_color=card_border_color,
                border_width=card_border_width,
                corner_radius=10,
                width=card_width,
                height=card_height
            )
            card.pack(fill="both", expand=True)

            # Header of card: size title + top-right remove button
            card_hdr = ctk.CTkFrame(card, fg_color="transparent")
            card_hdr.pack(fill="x", padx=8, pady=(6, 0))

            size_color = COLORS["primary"] if is_active else COLORS["text"]
            size_lbl = ctk.CTkLabel(
                card_hdr,
                text=card_data["size_name"],
                font=FONTS["body_bold"],
                text_color=size_color
            )
            size_lbl.pack(side="left", anchor="w")

            del_btn = ctk.CTkButton(
                card_hdr,
                text="✕",
                font=FONTS["small"],
                width=24,
                height=24,
                corner_radius=12,
                fg_color=COLORS["danger"],
                hover_color=COLORS["danger_hover"],
                text_color=COLORS["text"],
                command=lambda i=c_idx: self._remove_size_card(i)
            )
            del_btn.pack(side="right")

            # Active indicator badge / tap hint
            active_badge_text = f"● {t('gallery.active_badge')}" if is_active else f"○ {t('gallery.tap_to_preview')}"
            active_badge_color = COLORS["primary"] if is_active else COLORS["text_muted"]
            active_lbl = ctk.CTkLabel(
                card,
                text=active_badge_text,
                font=FONTS["small"],
                text_color=active_badge_color
            )
            active_lbl.pack(pady=(0, 2))

            # 1. "+" button on top
            plus_btn = ctk.CTkButton(
                card,
                text="+",
                font=FONTS["subtitle"],
                fg_color=COLORS["primary"],
                hover_color=COLORS["primary_hover"],
                height=28,
                command=lambda i=c_idx: self._increment_card_count(i)
            )
            plus_btn.pack(fill="x", padx=10, pady=(2, 2))

            # Count
            count_lbl = ctk.CTkLabel(
                card,
                text=f"{t('gallery.qty')}: {card_data['count']}",
                font=FONTS["subtitle"],
                text_color=COLORS["primary"] if card_data["count"] > 0 else COLORS["text"]
            )
            count_lbl.pack(pady=1)

            # 2. "-" button on bottom
            minus_btn = ctk.CTkButton(
                card,
                text="-",
                font=FONTS["subtitle"],
                fg_color=COLORS["border"],
                hover_color=COLORS["danger"],
                height=28,
                command=lambda i=c_idx: self._decrement_card_count(i)
            )
            minus_btn.pack(fill="x", padx=10, pady=(2, 2))

            # Paper dropdown menu
            finish_menu = ctk.CTkOptionMenu(
                card,
                values=self.finish_names,
                font=FONTS["small"],
                height=26,
                command=lambda val, i=c_idx: self._on_card_paper_changed(i, val)
            )
            finish_menu.set(card_data["paper_type"])
            finish_menu.pack(fill="x", padx=8, pady=2)

            # Price per print
            price = self._get_price(card_data["size_name"], card_data["paper_type"])
            price_str = f"{price:.2f} PLN" if price > 0 else "N/A"
            price_lbl = ctk.CTkLabel(card, text=price_str, font=FONTS["small"], text_color=COLORS["text_muted"])
            price_lbl.pack(pady=(0, 4))

            # Under-card remove button
            bottom_rem_btn = ctk.CTkButton(
                card_col,
                text=f"✕ {t('gallery.remove_card')}",
                font=FONTS["small"],
                fg_color=COLORS["danger"],
                hover_color=COLORS["danger_hover"],
                text_color=COLORS["text"],
                height=26,
                command=lambda i=c_idx: self._remove_size_card(i)
            )
            bottom_rem_btn.pack(fill="x", padx=8, pady=(4, 0))

            # Clicking anywhere on the card activates it
            for w in [card, card_col, size_lbl, active_lbl, count_lbl, price_lbl]:
                w.bind("<Button-1>", lambda e, i=c_idx: self._select_active_card(i))

        # "+" Add card button: card same size as default sizes
        add_col = ctk.CTkFrame(self.cards_row, fg_color="transparent", width=card_width)
        add_col.pack(side="left", padx=8, pady=4)

        add_card_btn = ctk.CTkButton(
            add_col,
            text=f"+\n{t('gallery.add_size')}",
            font=FONTS["title"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["primary_hover"],
            border_width=2,
            border_color=COLORS["primary"],
            width=card_width,
            height=card_height,
            corner_radius=10,
            command=self._on_add_card_clicked
        )
        add_card_btn.pack(fill="both", expand=True)

        # Empty spacer underneath add card to align with bottom remove buttons
        spacer = ctk.CTkLabel(add_col, text="", height=26)
        spacer.pack(fill="x", pady=(4, 0))

    def _increment_card_count(self, card_idx: int):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        photo = self.photos[self.active_photo_idx]
        cards = self.photo_configs[photo["filepath"]]["cards"]
        if card_idx < 0 or card_idx >= len(cards):
            return
        cards[card_idx]["count"] += 1
        self._set_active_card_idx(photo["filepath"], card_idx)

        # Automatically select photo for order
        self.selected_indices.add(self.active_photo_idx)
        if self.active_photo_idx in self.tile_cb_vars:
            self.tile_cb_vars[self.active_photo_idx].set(True)
        if hasattr(self, 'editor_cb_var'):
            self.editor_cb_var.set(True)

        self._background_copy_photo(photo["filepath"])
        self._update_selection_states()
        self._render_size_cards()
        self._update_editor_preview()

    def _decrement_card_count(self, card_idx: int):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        photo = self.photos[self.active_photo_idx]
        cards = self.photo_configs[photo["filepath"]]["cards"]
        if card_idx < 0 or card_idx >= len(cards):
            return
        cards[card_idx]["count"] = max(0, cards[card_idx]["count"] - 1)
        self._set_active_card_idx(photo["filepath"], card_idx)

        # If user decreases all counts to 0, automatically deselect photo
        total_counts = sum(c["count"] for c in cards)
        if total_counts == 0:
            self.selected_indices.discard(self.active_photo_idx)
            if self.active_photo_idx in self.tile_cb_vars:
                self.tile_cb_vars[self.active_photo_idx].set(False)
            if hasattr(self, 'editor_cb_var'):
                self.editor_cb_var.set(False)

        self._update_selection_states()
        self._render_size_cards()
        self._update_editor_preview()

    def _on_card_paper_changed(self, card_idx: int, paper_type: str):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        photo = self.photos[self.active_photo_idx]
        cards = self.photo_configs[photo["filepath"]]["cards"]
        if 0 <= card_idx < len(cards):
            cards[card_idx]["paper_type"] = paper_type
            self._set_active_card_idx(photo["filepath"], card_idx)
        self._render_size_cards()
        self._update_editor_preview()

    def _remove_size_card(self, card_idx: int):
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        photo = self.photos[self.active_photo_idx]
        cards = self.photo_configs[photo["filepath"]]["cards"]
        if card_idx < 0 or card_idx >= len(cards):
            return

        # Removing size in one image does NOT remove it from other images
        cards.pop(card_idx)

        # Adjust active card index
        active_idx = self._get_active_card_idx(photo["filepath"])
        if len(cards) == 0:
            self._set_active_card_idx(photo["filepath"], 0)
        else:
            if active_idx >= len(cards):
                self._set_active_card_idx(photo["filepath"], len(cards) - 1)
            elif active_idx > card_idx:
                self._set_active_card_idx(photo["filepath"], active_idx - 1)

        # If all counts are now 0, automatically deselect image
        total_counts = sum(c["count"] for c in cards)
        if total_counts == 0:
            self.selected_indices.discard(self.active_photo_idx)
            if self.active_photo_idx in self.tile_cb_vars:
                self.tile_cb_vars[self.active_photo_idx].set(False)
            if hasattr(self, 'editor_cb_var'):
                self.editor_cb_var.set(False)

        self._update_selection_states()
        self._render_size_cards()
        self._update_editor_preview()

    # -------------------------------------------------------------
    # Add Card Scrollable Dropdown with 2000ms Auto-Close
    # -------------------------------------------------------------
    def _on_add_card_clicked(self):
        if self.dropdown_popup is not None:
            self._close_dropdown_popup()
            return

        if self.active_photo_idx is None:
            return

        photo = self.photos[self.active_photo_idx]
        current_sizes = [c["size_name"] for c in self.photo_configs[photo["filepath"]]["cards"]]
        available = [s for s in self.size_names if s not in current_sizes]

        if not available:
            # If all sizes already added, offer all active sizes
            available = list(self.size_names)

        # Create floating popup dropdown over the cards row
        self.dropdown_popup = ctk.CTkFrame(self.right_scroll, fg_color=COLORS["surface"], border_width=2, border_color=COLORS["primary"], corner_radius=8)
        self.dropdown_popup.pack(pady=6)

        hdr = ctk.CTkLabel(self.dropdown_popup, text=t("gallery.select_size_to_add"), font=FONTS["body_bold"], text_color=COLORS["primary"])
        hdr.pack(padx=12, pady=(8, 4))

        list_scroll = ctk.CTkScrollableFrame(self.dropdown_popup, fg_color="transparent", height=130, width=180)
        list_scroll.pack(padx=8, pady=(0, 8))

        for s in available:
            btn = ctk.CTkButton(
                list_scroll,
                text=s,
                font=FONTS["body"],
                fg_color="transparent",
                hover_color=COLORS["primary_hover"],
                height=32,
                command=lambda sz=s: self._add_size_to_all(sz)
            )
            btn.pack(fill="x", pady=2)
            btn.bind("<Button-1>", lambda e: self._reset_dropdown_timer())

        self.dropdown_popup.bind("<Button-1>", lambda e: self._reset_dropdown_timer())
        self._reset_dropdown_timer()

    def _reset_dropdown_timer(self):
        if self.dropdown_timer_id is not None:
            self.after_cancel(self.dropdown_timer_id)
        # Auto-close after 2000ms of no activity
        self.dropdown_timer_id = self.after(2000, self._close_dropdown_popup)

    def _close_dropdown_popup(self):
        if self.dropdown_timer_id is not None:
            try:
                self.after_cancel(self.dropdown_timer_id)
            except Exception:
                pass
            self.dropdown_timer_id = None
        if self.dropdown_popup is not None:
            try:
                self.dropdown_popup.destroy()
            except Exception:
                pass
            self.dropdown_popup = None

    def _add_size_to_all(self, size_name: str):
        self._close_dropdown_popup()
        if size_name not in self.session_card_sizes:
            self.session_card_sizes.append(size_name)

        default_finish = self.finish_names[0] if self.finish_names else "GLOSSY"

        # Adding in one adds automatically to others (with 0 count)
        for p in self.photos:
            fp = p["filepath"]
            self._init_photo_config(fp)
            cards = self.photo_configs[fp]["cards"]
            # Only add if size_name not already in cards for this photo
            if not any(c["size_name"] == size_name for c in cards):
                cards.append({
                    "size_name": size_name,
                    "paper_type": default_finish,
                    "count": 0
                })

        if self.active_photo_idx is not None and self.photos:
            photo = self.photos[self.active_photo_idx]
            cards = self.photo_configs[photo["filepath"]]["cards"]
            for i, c in enumerate(cards):
                if c["size_name"] == size_name:
                    self._set_active_card_idx(photo["filepath"], i)
                    break

        self._render_size_cards()
        self._update_editor_preview()

    def _update_editor_preview(self):
        if self.active_photo_idx is None or not self.photos:
            return
        photo = self.photos[self.active_photo_idx]
        cfg = self.photo_configs[photo["filepath"]]

        # Determine active card size based on active chosen print size card
        cards = cfg.get("cards", [])
        active_idx = self._get_active_card_idx(photo["filepath"])

        if cards and 0 <= active_idx < len(cards):
            active_card_size = cards[active_idx]["size_name"]
            active_paper_type = cards[active_idx]["paper_type"]
        elif cards:
            active_card_size = cards[0]["size_name"]
            active_paper_type = cards[0]["paper_type"]
        else:
            active_card_size = self.size_names[0] if self.size_names else "10x15"
            active_paper_type = self.finish_names[0] if self.finish_names else "GLOSSY"

        size_obj = next((s for s in self.active_sizes if s.name == active_card_size), None)
        paper_w = size_obj.width_mm if size_obj else 102.0
        paper_h = size_obj.height_mm if size_obj else 152.0

        # Calculate a larger preview box based on right pane width
        try:
            pane_w = self.right_pane.winfo_width()
        except Exception:
            pane_w = 0

        if pane_w > 200:
            box_w = max(480, min(700, pane_w - 40))
            box_h = max(360, min(500, int(box_w * 0.72)))
        else:
            box_w = 480
            box_h = 360
        box_size = (box_w, box_h)

        def worker():
            try:
                with Image.open(photo["filepath"]) as img:
                    prev_result = generate_preview(
                        source_image=img,
                        crop_mode=cfg["crop_mode"],
                        paper_width_mm=paper_w,
                        paper_height_mm=paper_h,
                        border_width_mm=4.0,
                        preview_box_size=box_size,
                        auto_orientation=True
                    )
                prev_img = prev_result["preview_image"]
                disp_w, disp_h = prev_result["paper_physical_mm"]
                crop_mode_label = t(f"crop_modes.{cfg['crop_mode']}")

                ctk_img = ctk.CTkImage(light_image=prev_img, dark_image=prev_img, size=prev_img.size)
                if self.preview_lbl.winfo_exists():
                    self.after(0, lambda: self._apply_preview_ui(ctk_img, active_card_size, disp_w, disp_h, active_paper_type, crop_mode_label))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _apply_preview_ui(self, ctk_img: ctk.CTkImage, size_name: str, disp_w: float, disp_h: float, paper_finish: str, crop_mode_label: str):
        if not self.preview_lbl.winfo_exists():
            return
        self.preview_lbl.configure(image=ctk_img, text="")
        if hasattr(self, "canvas_info_lbl") and self.canvas_info_lbl.winfo_exists():
            self.canvas_info_lbl.configure(
                text=f"{t('gallery.canvas_format')}: {size_name} ({disp_w:.0f}×{disp_h:.0f} mm) • {paper_finish} • {crop_mode_label}"
            )

    # -------------------------------------------------------------
    # Proceed to Order Summary & File Cleanup
    # -------------------------------------------------------------
    def _proceed_to_summary(self):
        self._close_dropdown_popup()
        # Build list of configured order items without deleting unselected files
        configured_items: List[Dict[str, Any]] = []
        for idx in sorted(self.selected_indices):
            photo = self.photos[idx]
            cfg = self.photo_configs[photo["filepath"]]
            for card in cfg["cards"]:
                qty = card["count"]
                if qty > 0:
                    unit_price = self._get_price(card["size_name"], card["paper_type"])
                    configured_items.append({
                        "source_filepath": photo["filepath"],
                        "filename": photo["filename"],
                        "print_size_name": card["size_name"],
                        "paper_finish": card["paper_type"].upper(),
                        "crop_mode": cfg["crop_mode"],
                        "quantity": qty,
                        "unit_price": unit_price,
                        "subtotal": unit_price * qty
                    })

        if self.on_proceed_to_summary:
            import inspect
            sig = inspect.signature(self.on_proceed_to_summary)
            if len(sig.parameters) >= 3:
                self.on_proceed_to_summary(configured_items, self.photo_configs, self.selected_indices)
            else:
                self.on_proceed_to_summary(configured_items)
