"""Screen 2: split-screen photo gallery (left) and per-photo print editor (right).

Architecture
------------
* ``OrderDraft``      - all selection / quantity / price rules (no Tk, unit tested)
* ``PhotoGrid``       - virtualised canvas grid, photos keep their aspect ratio
* ``SizeCardsRow``    - persistent size cards updated in place
* ``PreviewService``  - cached, debounced, off-thread print preview
* ``UiBridge``        - safe hand-off of background results to the Tk thread

This screen only wires those pieces together. Widgets are built once; state
changes update existing widgets (diffed) instead of destroying and rebuilding
them, which is what removes the flicker.
"""
import inspect
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import customtkinter as ctk

from backend.i18n import get_i18n, t
from backend.library import scan_directory_for_photos
from backend.thumbnails import ThumbnailCache
from gui.language_toggle import LanguageToggle
from gui.services.order_draft import OrderDraft, PrintCatalog
from gui.services.preview_service import PreviewRequest, PreviewService
from gui.services.staging import SessionStager
from gui.services.ui_bridge import UiBridge
from gui.theme import COLORS, FONTS, GALLERY, TOUCH_SIZES
from gui.widgets.photo_grid import PhotoGrid
from gui.widgets.size_cards import SizeCardsRow, SizePickerPopup

logger = logging.getLogger(__name__)

DENSITIES = (4, 8, 16)
CATEGORY_ORDER = (
    "all", "camera", "whatsapp", "messenger", "screenshots",
    "instagram", "telegram", "downloads", "snapchat", "twitter", "other",
)
CROP_MODES = ("fill_in", "fit_in", "border", "no_resize")


def _set_text(widget, text: str) -> None:
    """configure(text=...) only if it differs (avoids needless redraws)."""
    if widget.cget("text") != text:
        widget.configure(text=text)


class GalleryScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        source_name: str,
        source_path: str,
        session_id: int = 1,
        on_back: Optional[Callable[[], None]] = None,
        on_proceed_to_summary: Optional[Callable[..., None]] = None,
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

        self.catalog = PrintCatalog.load()
        self.active_sizes = self.catalog.sizes
        self.size_names = self.catalog.size_names or ["9x13", "10x15"]
        self.finish_names = self.catalog.finish_names or ["GLOSSY", "MATTE"]
        self.draft = OrderDraft(
            self.catalog,
            photo_configs=initial_photo_configs,
            selected=initial_selected_indices,
            session_card_sizes=initial_session_card_sizes,
        )
        self.photos: List[Dict[str, Any]] = self.draft.photos
        self.active_photo_idx: Optional[int] = None
        self.active_category = "all"
        self.density_var = ctk.IntVar(value=DENSITIES[0])

        self.bridge = UiBridge(self)
        self.thumb_cache = ThumbnailCache()
        self.stager = SessionStager(session_id)
        self.preview = PreviewService(self, self.bridge, self._on_preview_ready)

        self.dropdown_popup: Optional[SizePickerPopup] = None
        self.density_buttons: Dict[int, ctk.CTkButton] = {}
        self.category_buttons: Dict[str, ctk.CTkButton] = {}
        self._category_counts: Dict[str, int] = {}
        self._editor_visible = False
        self._shown_preview: Optional[PreviewRequest] = None
        self._preview_image: Optional[ctk.CTkImage] = None  # keep a reference (Tk would drop it)
        self._preview_box_cache: Tuple[int, int] = (GALLERY["preview_min_w"], GALLERY["preview_min_h"])
        self._pane_job: Optional[str] = None
        self._last_caption: Optional[Tuple[str, float, float, str]] = None

        self._build_ui()
        get_i18n().subscribe(self._on_locale_changed)
        self._load_photos_async()

    # ------------------------------------------------------------------
    # Compatibility views onto the draft (used by app.py and tests)
    # ------------------------------------------------------------------
    @property
    def photo_configs(self) -> Dict[str, Dict[str, Any]]:
        return self.draft.photo_configs

    @property
    def selected_indices(self) -> Set[int]:
        return self.draft.selected

    @property
    def active_card_indices(self) -> Dict[str, int]:
        return self.draft.active_card

    @property
    def session_card_sizes(self) -> List[str]:
        return self.draft.session_card_sizes

    @property
    def staged_files(self) -> Dict[str, str]:
        return self.stager.staged_files

    def destroy(self) -> None:
        try:
            get_i18n().unsubscribe(self._on_locale_changed)
        except Exception:
            pass
        for closer in (self.bridge.close, self.photo_grid.shutdown, self.preview.close, self.stager.shutdown):
            try:
                closer()
            except Exception:
                logger.debug("cleanup failed", exc_info=True)
        super().destroy()

    # ------------------------------------------------------------------
    # UI construction (done once)
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_top_bar()
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        self.body.grid_columnconfigure(0, weight=1, uniform="half")
        self.body.grid_columnconfigure(1, weight=1, uniform="half")
        self.body.grid_rowconfigure(0, weight=1)
        self._build_left_pane()
        self._build_right_pane()

    def _build_top_bar(self) -> None:
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=20, pady=(15, 10))

        self.back_btn = ctk.CTkButton(
            self.top_bar, text=f"← {t('gallery.back_to_sources')}", font=FONTS["button"],
            fg_color=COLORS["surface"], hover_color=COLORS["border"], width=110,
            height=TOUCH_SIZES["button_height"], command=lambda: self.on_back() if self.on_back else None,
        )
        self.back_btn.pack(side="left")

        self.title_lbl = ctk.CTkLabel(
            self.top_bar, text=f"{t('gallery.title')}: {self.source_name}",
            font=FONTS["title"], text_color=COLORS["text"],
        )
        self.title_lbl.pack(side="left", padx=16)

        self.selected_counter_lbl = ctk.CTkLabel(
            self.top_bar, text=f"{t('gallery.selected')}: 0", font=FONTS["subtitle"], text_color=COLORS["primary"],
        )
        self.selected_counter_lbl.pack(side="left", padx=15)

        density_frame = ctk.CTkFrame(self.top_bar, fg_color=COLORS["surface"], corner_radius=8)
        density_frame.pack(side="left", padx=10)
        self.density_lbl = ctk.CTkLabel(density_frame, text=f"{t('gallery.density')}:", font=FONTS["body_bold"])
        self.density_lbl.pack(side="left", padx=(10, 6), pady=4)
        for d in DENSITIES:
            btn = ctk.CTkButton(
                density_frame, text=str(d), width=36, height=32, font=FONTS["small"],
                fg_color=COLORS["primary"] if self.density_var.get() == d else "transparent",
                hover_color=COLORS["primary_hover"], command=lambda val=d: self._set_density(val),
            )
            btn.pack(side="left", padx=2, pady=4)
            self.density_buttons[d] = btn

        self.lang_toggle = LanguageToggle(self.top_bar)
        self.lang_toggle.pack(side="right", padx=(10, 0))

        self.proceed_btn = ctk.CTkButton(
            self.top_bar, text=f"{t('gallery.proceed_to_summary')} →", font=FONTS["button"],
            fg_color=COLORS["success"], hover_color=COLORS["success_hover"],
            height=TOUCH_SIZES["button_height"], state="disabled", command=self._proceed_to_summary,
        )
        self.proceed_btn.pack(side="right")

        self.select_all_btn = ctk.CTkButton(
            self.top_bar, text=t("gallery.select_all"), font=FONTS["button"],
            fg_color=COLORS["surface"], hover_color=COLORS["border"],
            height=TOUCH_SIZES["button_height"], command=self._toggle_select_all,
        )
        self.select_all_btn.pack(side="right", padx=10)

    def _build_left_pane(self) -> None:
        self.left_pane = ctk.CTkFrame(self.body, fg_color=COLORS["bg_card"], corner_radius=12)
        self.left_pane.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.category_bar = ctk.CTkScrollableFrame(
            self.left_pane, fg_color="transparent", orientation="horizontal", height=44,
        )
        self.category_bar.pack(fill="x", padx=10, pady=(8, 2))

        self.grid_host = ctk.CTkFrame(self.left_pane, fg_color="transparent")
        self.grid_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.photo_grid = PhotoGrid(
            self.grid_host, self.thumb_cache, self.bridge,
            on_activate=self._select_photo_for_editing,
            on_toggle_select=self._toggle_photo_selection,
            state_provider=self._tile_state,
            on_background_tap=self._close_dropdown_popup,
        )
        self.photo_grid.pack(fill="both", expand=True)

        self.status_lbl = ctk.CTkLabel(
            self.grid_host, text=t("gallery.scanning_msg"), font=FONTS["subtitle"],
            text_color=COLORS["text_muted"], fg_color=COLORS["bg_card"],
        )
        self.status_lbl.place(relx=0.5, rely=0.45, anchor="center")

    def _build_right_pane(self) -> None:
        self.right_pane = ctk.CTkFrame(self.body, fg_color=COLORS["bg_card"], corner_radius=12)
        self.right_pane.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.right_pane.bind("<Configure>", self._on_right_pane_configure)

        self.right_scroll = ctk.CTkScrollableFrame(self.right_pane, fg_color="transparent")
        self.right_scroll.pack(fill="both", expand=True, padx=14, pady=14)
        self.right_scroll.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        self.editor_placeholder = ctk.CTkLabel(
            self.right_scroll, text=t("gallery.tap_image_prompt"), font=FONTS["subtitle"],
            text_color=COLORS["text_muted"],
        )
        self.editor_placeholder.pack(pady=100)

        # The editor itself is created once and only updated afterwards.
        self.editor_content = ctk.CTkFrame(self.right_scroll, fg_color="transparent")

        hdr = ctk.CTkFrame(self.editor_content, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 10))
        info_box = ctk.CTkFrame(hdr, fg_color="transparent")
        info_box.pack(side="left", fill="both", expand=True)
        self.name_lbl = ctk.CTkLabel(info_box, text="", font=FONTS["subtitle"], text_color=COLORS["text"])
        self.name_lbl.pack(anchor="w")
        self.meta_lbl = ctk.CTkLabel(info_box, text="", font=FONTS["small"], text_color=COLORS["text_muted"])
        self.meta_lbl.pack(anchor="w")

        self.editor_cb_var = ctk.BooleanVar(value=False)
        self.editor_cb = ctk.CTkCheckBox(
            hdr, text=t("gallery.add_to_order"), font=FONTS["body_bold"],
            variable=self.editor_cb_var, command=self._on_editor_checkbox_toggled,
        )
        self.editor_cb.pack(side="right", padx=10)

        viewport = ctk.CTkFrame(self.editor_content, fg_color=COLORS["surface"], corner_radius=10)
        viewport.pack(fill="x", pady=(0, 10))
        self.canvas_info_lbl = ctk.CTkLabel(viewport, text="", font=FONTS["small"], text_color=COLORS["primary"])
        self.canvas_info_lbl.pack(pady=(6, 2))
        # Fixed-height area: the layout never jumps when the preview's aspect ratio changes.
        self.preview_area = ctk.CTkFrame(
            viewport, fg_color="transparent", height=self._preview_box_cache[1], width=10,
        )
        self.preview_area.pack(fill="x", pady=(2, 10))
        self.preview_area.pack_propagate(False)
        self.preview_lbl = ctk.CTkLabel(self.preview_area, text="…", font=FONTS["small"])
        self.preview_lbl.place(relx=0.5, rely=0.5, anchor="center")
        for w in (viewport, self.canvas_info_lbl, self.preview_area, self.preview_lbl):
            w.bind("<Button-1>", lambda e: self._close_dropdown_popup())

        crop_frame = ctk.CTkFrame(self.editor_content, fg_color="transparent")
        crop_frame.pack(fill="x", pady=(0, 10))
        self.crop_lbl = ctk.CTkLabel(
            crop_frame, text=f"{t('gallery.crop_mode')}:", font=FONTS["body_bold"], text_color=COLORS["primary"],
        )
        self.crop_lbl.pack(side="left", padx=(0, 10))
        self.crop_mode_var = ctk.StringVar(value="fill_in")
        self.crop_radios: Dict[str, ctk.CTkRadioButton] = {}
        for mode in CROP_MODES:
            rb = ctk.CTkRadioButton(
                crop_frame, text=t(f"crop_modes.{mode}"), value=mode, variable=self.crop_mode_var,
                font=FONTS["small"], command=self._on_crop_mode_changed,
            )
            rb.pack(side="left", padx=6)
            self.crop_radios[mode] = rb

        self.cards_title = ctk.CTkLabel(
            self.editor_content, text=f"{t('gallery.sizes_and_quantities')}:", font=FONTS["body_bold"],
        )
        self.cards_title.pack(anchor="w", pady=(0, 4))
        self.cards_row = SizeCardsRow(
            self.editor_content, self.finish_names,
            on_select=self._select_active_card, on_plus=self._increment_card_count,
            on_minus=self._decrement_card_count, on_paper=self._on_card_paper_changed,
            on_remove=self._remove_size_card, on_add=self._on_add_card_clicked,
            on_background_tap=self._close_dropdown_popup,
        )
        self.cards_row.pack(fill="x", pady=(0, 10))

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load_photos_async(self) -> None:
        def worker() -> None:
            found = scan_directory_for_photos(self.source_path)
            self.bridge.post(self._on_photos_loaded, found)

        threading.Thread(target=worker, name="gallery-scan", daemon=True).start()

    def _on_photos_loaded(self, found: List[Dict[str, Any]]) -> None:
        self.status_lbl.place_forget()
        self.photos = found
        self.draft.photos = found
        if not found:
            self.status_lbl.configure(text=t("gallery.no_photos"))
            self.status_lbl.place(relx=0.5, rely=0.45, anchor="center")
            return

        for p in found:
            self.draft.ensure(p["filepath"])

        self._render_category_bar()
        self.active_photo_idx = 0
        self.photo_grid.set_photos(found)
        self.photo_grid.set_active(0)
        self._show_active_photo()
        self._update_selection_states()

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------
    def _render_category_bar(self) -> None:
        """Create category chips once per photo set; later calls only relabel them."""
        counts: Dict[str, int] = {}
        for p in self.photos:
            cat = p.get("category", "other")
            counts[cat] = counts.get(cat, 0) + 1
        existing = ["all"] + [c for c in CATEGORY_ORDER if c != "all" and counts.get(c, 0) > 0]
        existing += [c for c in counts if c not in existing]

        if list(self.category_buttons) != existing or counts != self._category_counts:
            for w in self.category_bar.winfo_children():
                w.destroy()
            self.category_buttons.clear()
            self._category_counts = counts
            for c in existing:
                btn = ctk.CTkButton(
                    self.category_bar, text="", height=32, corner_radius=16, text_color=COLORS["text"],
                    command=lambda k=c: self._select_category(k),
                )
                btn.pack(side="left", padx=4, pady=2)
                self.category_buttons[c] = btn

        for c, btn in self.category_buttons.items():
            count = len(self.photos) if c == "all" else counts.get(c, 0)
            label = t("gallery.album_all") if c == "all" else t(f"gallery.category_{c}", default=c.title())
            _set_text(btn, f"{label} ({count})")
        self._style_category_buttons()

    def _style_category_buttons(self) -> None:
        for c, btn in self.category_buttons.items():
            active = c == self.active_category
            btn.configure(
                fg_color=COLORS["primary"] if active else COLORS["surface"],
                hover_color=COLORS["primary_hover"] if active else COLORS["border"],
                font=FONTS["body_bold"] if active else FONTS["small"],
            )

    def _get_visible_photos(self) -> List[Tuple[int, Dict[str, Any]]]:
        """(original_index, photo) pairs matching the active category/album."""
        if self.active_category == "all":
            return list(enumerate(self.photos))
        return [
            (i, p) for i, p in enumerate(self.photos)
            if p.get("category") == self.active_category or p.get("album") == self.active_category
        ]

    def _select_category(self, cat_key: str) -> None:
        self._close_dropdown_popup()
        if cat_key == self.active_category:
            return
        self.active_category = cat_key
        self._style_category_buttons()

        visible = self._get_visible_photos()
        self.photo_grid.set_visible([i for i, _ in visible])
        if visible:
            indices = [i for i, _ in visible]
            if self.active_photo_idx not in indices:
                self.active_photo_idx = indices[0]
                self.photo_grid.set_active(self.active_photo_idx)
                self._show_active_photo()
        else:
            self.active_photo_idx = None
            self.photo_grid.set_active(None)
            self._show_active_photo()

    # ------------------------------------------------------------------
    # Grid callbacks / selection
    # ------------------------------------------------------------------
    def _set_density(self, density: int) -> None:
        self.density_var.set(density)
        for d, btn in self.density_buttons.items():
            btn.configure(fg_color=COLORS["primary"] if d == density else "transparent")
        self.photo_grid.set_density(density)

    def _tile_state(self, idx: int) -> Tuple[bool, int]:
        return (idx in self.draft.selected, self.draft.prints_for(idx))

    def _select_photo_for_editing(self, idx: int) -> None:
        self._close_dropdown_popup()
        if not 0 <= idx < len(self.photos) or idx == self.active_photo_idx:
            return
        self.active_photo_idx = idx
        self.photo_grid.set_active(idx)
        self._show_active_photo()

    def _toggle_photo_selection(self, idx: int) -> None:
        self._close_dropdown_popup()
        if idx in self.draft.selected:
            self._deselect_image_from_order(idx)
        else:
            self._select_image_for_order(idx)

    def _select_image_for_order(self, idx: int) -> None:
        self.draft.select(idx)
        self.stager.stage(self.draft.path(idx))
        self._after_selection_change(idx)

    def _deselect_image_from_order(self, idx: int) -> None:
        self.draft.deselect(idx)
        self._after_selection_change(idx)

    def _after_selection_change(self, idx: int) -> None:
        self.photo_grid.refresh_tile(idx)
        self._update_selection_states()
        if idx == self.active_photo_idx:
            self.editor_cb_var.set(idx in self.draft.selected)
            self._render_size_cards()

    def _toggle_select_all(self) -> None:
        self._close_dropdown_popup()
        if not self.photos:
            return
        if len(self.draft.selected) == len(self.photos):
            self.draft.deselect_all()
        else:
            self.draft.select_all()
            for p in self.photos:
                self.stager.stage(p["filepath"])
        self.photo_grid.refresh_all_tiles()
        self._update_selection_states()
        if self.active_photo_idx is not None:
            self.editor_cb_var.set(self.active_photo_idx in self.draft.selected)
            self._render_size_cards()

    def _update_selection_states(self) -> None:
        count = len(self.draft.selected)
        _set_text(self.selected_counter_lbl, f"{t('gallery.selected')}: {count}")
        all_selected = count == len(self.photos) and len(self.photos) > 0
        _set_text(self.select_all_btn, t("gallery.deselect_all") if all_selected else t("gallery.select_all"))

        total = self.draft.total_prints()
        if count > 0 and total > 0:
            self.proceed_btn.configure(state="normal")
            _set_text(self.proceed_btn, f"{t('gallery.proceed_to_summary')} ({total}) →")
        else:
            self.proceed_btn.configure(state="disabled")
            _set_text(self.proceed_btn, f"{t('gallery.proceed_to_summary')} →")

    # ------------------------------------------------------------------
    # Editor (updates widgets in place)
    # ------------------------------------------------------------------
    def _show_editor(self, visible: bool) -> None:
        if visible == self._editor_visible:
            return
        self._editor_visible = visible
        if visible:
            self.editor_placeholder.pack_forget()
            self.editor_content.pack(fill="x")
        else:
            self.editor_content.pack_forget()
            self.editor_placeholder.pack(pady=100)

    def _show_active_photo(self) -> None:
        """Load the active photo's data into the persistent editor widgets."""
        self._close_dropdown_popup()
        if self.active_photo_idx is None or not self.photos:
            self._show_editor(False)
            return
        photo = self.photos[self.active_photo_idx]
        cfg = self.draft.ensure(photo["filepath"])
        self._show_editor(True)

        _set_text(self.name_lbl, photo["filename"])
        kb = round(photo.get("size_bytes", 0) / 1024, 1)
        dims = f"{photo['width']}×{photo['height']} px · " if photo.get("width") else ""
        _set_text(self.meta_lbl, f"{dims}{kb} KB")
        self.editor_cb_var.set(self.active_photo_idx in self.draft.selected)
        self.crop_mode_var.set(cfg.get("crop_mode", "fill_in"))
        self._render_size_cards()
        self._update_editor_preview()

    def _card_texts(self) -> Dict[str, str]:
        return {
            "active": t("gallery.active_badge"), "tap": t("gallery.tap_to_preview"),
            "qty": t("gallery.qty"), "remove": t("gallery.remove_card"),
            "add": t("gallery.add_size"), "empty": t("gallery.no_sizes_added"),
        }

    def _render_size_cards(self) -> None:
        """Sync the card strip with the draft (in place; no widget rebuild)."""
        if self.active_photo_idx is None or not self.photos:
            return
        fp = self.draft.path(self.active_photo_idx)
        self.cards_row.sync(
            self.draft.cards(self.active_photo_idx),
            self.draft.active_card_idx(fp),
            self.catalog.price,
            self._card_texts(),
        )

    def _get_active_card_idx(self, filepath: str) -> int:
        return self.draft.active_card_idx(filepath)

    def _select_active_card(self, card_idx: int) -> None:
        self._close_dropdown_popup()
        if self.active_photo_idx is None or not self.photos:
            return
        self.draft.set_active_card(self.draft.path(self.active_photo_idx), card_idx)
        self._render_size_cards()
        self._update_editor_preview()

    def _on_editor_checkbox_toggled(self) -> None:
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        if self.editor_cb_var.get():
            self._select_image_for_order(self.active_photo_idx)
        else:
            self._deselect_image_from_order(self.active_photo_idx)

    def _on_crop_mode_changed(self) -> None:
        self._close_dropdown_popup()
        if self.active_photo_idx is None:
            return
        self.draft.ensure(self.draft.path(self.active_photo_idx))["crop_mode"] = self.crop_mode_var.get()
        self._update_editor_preview()

    # ---- card operations (quantity changes never re-render the preview) ----
    def _after_card_change(self, idx: int, prev_active: int) -> None:
        self.photo_grid.refresh_tile(idx)
        self._update_selection_states()
        self.editor_cb_var.set(idx in self.draft.selected)
        self._render_size_cards()
        if self.draft.active_card_idx(self.draft.path(idx)) != prev_active:
            self._update_editor_preview()

    def _active_for(self, idx: int) -> int:
        return self.draft.active_card_idx(self.draft.path(idx))

    def _increment_card_count(self, card_idx: int) -> None:
        self._close_dropdown_popup()
        idx = self.active_photo_idx
        if idx is None:
            return
        prev = self._active_for(idx)
        if self.draft.increment(idx, card_idx):
            self.stager.stage(self.draft.path(idx))
            self._after_card_change(idx, prev)

    def _decrement_card_count(self, card_idx: int) -> None:
        self._close_dropdown_popup()
        idx = self.active_photo_idx
        if idx is None:
            return
        prev = self._active_for(idx)
        if self.draft.decrement(idx, card_idx):
            self._after_card_change(idx, prev)

    def _on_card_paper_changed(self, card_idx: int, paper_type: str) -> None:
        self._close_dropdown_popup()
        idx = self.active_photo_idx
        if idx is None:
            return
        prev = self._active_for(idx)
        if self.draft.set_paper(idx, card_idx, paper_type):
            self._after_card_change(idx, prev)
            self._refresh_preview_caption()

    def _remove_size_card(self, card_idx: int) -> None:
        self._close_dropdown_popup()
        idx = self.active_photo_idx
        if idx is None:
            return
        if self.draft.remove_card(idx, card_idx):
            self._after_card_change(idx, prev_active=-1)  # active card changed -> refresh preview

    # ---- "+ Add size" popup ------------------------------------------
    def _on_add_card_clicked(self) -> None:
        if self.dropdown_popup is not None:
            self._close_dropdown_popup()
            return
        if self.active_photo_idx is None:
            return
        self.dropdown_popup = SizePickerPopup(
            self.editor_content,
            options=self.draft.available_sizes_for(self.active_photo_idx),
            title=t("gallery.select_size_to_add"),
            on_pick=self._add_size_to_all,
            on_close=self._close_dropdown_popup,
        )
        self.dropdown_popup.pack(pady=6)

    def _close_dropdown_popup(self) -> None:
        popup, self.dropdown_popup = self.dropdown_popup, None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _add_size_to_all(self, size_name: str) -> None:
        self._close_dropdown_popup()
        self.draft.add_size_everywhere(size_name)
        if self.active_photo_idx is not None and self.photos:
            fp = self.draft.path(self.active_photo_idx)
            for i, c in enumerate(self.draft.cards(self.active_photo_idx)):
                if c["size_name"] == size_name:
                    self.draft.set_active_card(fp, i)
                    break
        self._render_size_cards()
        self._update_editor_preview()

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def _compute_preview_box(self) -> Tuple[int, int]:
        pane_w = self.right_pane.winfo_width()
        if pane_w <= 200:
            return (GALLERY["preview_min_w"], GALLERY["preview_min_h"])
        box_w = max(GALLERY["preview_min_w"], min(GALLERY["preview_max_w"], pane_w - 40))
        box_w = (box_w // 20) * 20  # quantise so tiny resizes keep hitting the preview cache
        box_h = max(GALLERY["preview_min_h"], min(GALLERY["preview_max_h"], int(box_w * 0.72)))
        return (box_w, box_h)

    def _on_right_pane_configure(self, _event) -> None:
        if self._pane_job is None:
            self._pane_job = self.after(150, self._apply_pane_size)

    def _apply_pane_size(self) -> None:
        self._pane_job = None
        box = self._compute_preview_box()
        if box != self._preview_box_cache:
            self._preview_box_cache = box
            self.preview_area.configure(height=box[1])
            self._update_editor_preview()

    def _active_card_data(self) -> Optional[Dict[str, Any]]:
        if self.active_photo_idx is None or not self.photos:
            return None
        cards = self.draft.cards(self.active_photo_idx)
        if not cards:
            return None
        return cards[self.draft.active_card_idx(self.draft.path(self.active_photo_idx))]

    def _update_editor_preview(self) -> None:
        """Request a preview for the active photo + active card (cached, debounced, async)."""
        if self.active_photo_idx is None or not self.photos:
            return
        photo = self.photos[self.active_photo_idx]
        cfg = self.draft.ensure(photo["filepath"])
        card = self._active_card_data()
        size_name = card["size_name"] if card else (self.size_names[0] if self.size_names else "10x15")
        spec = self.catalog.size(size_name)
        self.preview.request(PreviewRequest(
            filepath=photo["filepath"],
            crop_mode=cfg["crop_mode"],
            size_name=size_name,
            paper_w_mm=spec.width_mm if spec else 102.0,
            paper_h_mm=spec.height_mm if spec else 152.0,
            box=self._preview_box_cache,
        ))

    def _on_preview_ready(self, req: PreviewRequest, result: Dict[str, Any]) -> None:
        """Runs on the UI thread. Ignores stale results and unchanged previews."""
        photo = self.photos[self.active_photo_idx] if self.active_photo_idx is not None and self.photos else None
        if photo is None or photo["filepath"] != req.filepath or req == self._shown_preview:
            return
        self._shown_preview = req
        img = result["preview_image"]
        disp_w, disp_h = result["paper_physical_mm"]
        card = self._active_card_data()
        finish = card["paper_type"] if card else (self.finish_names[0] if self.finish_names else "GLOSSY")
        self._apply_preview_ui(
            ctk.CTkImage(light_image=img, dark_image=img, size=img.size),
            req.size_name, disp_w, disp_h, finish, t(f"crop_modes.{req.crop_mode}"),
        )

    def _apply_preview_ui(self, ctk_img: ctk.CTkImage, size_name: str, disp_w: float, disp_h: float,
                          paper_finish: str, crop_mode_label: str) -> None:
        if not self.preview_lbl.winfo_exists():
            return
        self._preview_image = ctk_img
        self.preview_lbl.configure(image=ctk_img, text="")
        self._last_caption = (size_name, disp_w, disp_h, crop_mode_label)
        _set_text(
            self.canvas_info_lbl,
            f"{t('gallery.canvas_format')}: {size_name} ({disp_w:.0f}×{disp_h:.0f} mm) • {paper_finish} • {crop_mode_label}",
        )

    def _refresh_preview_caption(self) -> None:
        """Paper finish changes only the caption, not the picture."""
        cap = self._last_caption
        card = self._active_card_data()
        if cap and card and self._preview_image is not None:
            size_name, w, h, mode = cap
            _set_text(self.canvas_info_lbl,
                      f"{t('gallery.canvas_format')}: {size_name} ({w:.0f}×{h:.0f} mm) • {card['paper_type']} • {mode}")

    # ------------------------------------------------------------------
    # Localisation (in place, no rebuild)
    # ------------------------------------------------------------------
    def _on_locale_changed(self, lang: str) -> None:
        if self.winfo_exists():
            self._update_localized_texts()

    def _update_localized_texts(self) -> None:
        _set_text(self.back_btn, f"← {t('gallery.back_to_sources')}")
        _set_text(self.title_lbl, f"{t('gallery.title')}: {self.source_name}")
        _set_text(self.density_lbl, f"{t('gallery.density')}:")
        _set_text(self.editor_placeholder, t("gallery.tap_image_prompt"))
        _set_text(self.editor_cb, t("gallery.add_to_order"))
        _set_text(self.crop_lbl, f"{t('gallery.crop_mode')}:")
        _set_text(self.cards_title, f"{t('gallery.sizes_and_quantities')}:")
        for mode, rb in self.crop_radios.items():
            _set_text(rb, t(f"crop_modes.{mode}"))
        self._update_selection_states()
        if self.photos:
            self._render_category_bar()
            self._render_size_cards()
            self._shown_preview = None  # force caption refresh with the new language
            self._update_editor_preview()

    # ------------------------------------------------------------------
    # Proceed
    # ------------------------------------------------------------------
    def _proceed_to_summary(self) -> None:
        self._close_dropdown_popup()
        items = self.draft.build_items()
        callback = self.on_proceed_to_summary
        if not callback:
            return
        try:
            wants_state = len(inspect.signature(callback).parameters) >= 3
        except (TypeError, ValueError):
            wants_state = False
        if wants_state:
            callback(items, self.draft.photo_configs, self.draft.selected)
        else:
            callback(items)
