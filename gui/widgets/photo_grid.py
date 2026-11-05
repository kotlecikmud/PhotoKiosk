"""Virtualised, flicker-free thumbnail grid that keeps every photo's aspect ratio.

Rendering model
---------------
* One ``tk.Canvas``; each visible photo is a handful of canvas items (placeholder,
  image, active outline, selection badge, quantity chip).
* Only tiles near the viewport exist as canvas items (virtualisation), so galleries
  with thousands of photos stay responsive.
* State changes (selected / active / quantity) only ``itemconfigure`` the affected
  items -- nothing is destroyed or rebuilt, hence no flicker.
* Re-layouts (density, resize, filter) rebuild items inside a single event-loop
  callback, so Tk repaints once with the final result.
* Decoded thumbnails are kept in LRU caches, so a re-layout re-uses them instantly
  instead of showing blank tiles.
"""
import tkinter as tk
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import customtkinter as ctk
from PIL import Image, ImageTk

from backend.thumbnails import ThumbnailCache
from gui.layout import DEFAULT_ASPECT, Layout, compute_justified_layout
from gui.services.thumbnail_loader import ThumbnailLoader
from gui.services.ui_bridge import UiBridge
from gui.theme import COLORS, FONTS, GALLERY

_DRAG_THRESHOLD_PX = 8
_BASE_CACHE_SIZE = 600      # decoded thumbnails (PIL) kept in memory
_PHOTO_CACHE_SIZE = 400     # ready-to-draw PhotoImages


class _LRU(OrderedDict):
    def __init__(self, capacity: int):
        super().__init__()
        self.capacity = capacity

    def get_touch(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.capacity:
            self.popitem(last=False)


class PhotoGrid(ctk.CTkFrame):
    """Scrollable justified photo grid.

    Callbacks
    ---------
    on_activate(photo_index)      tile tapped -> open in editor
    on_toggle_select(photo_index) check badge tapped
    state_provider(photo_index) -> (selected: bool, prints: int)
    """

    def __init__(
        self,
        parent,
        thumb_cache: ThumbnailCache,
        bridge: UiBridge,
        on_activate: Callable[[int], None],
        on_toggle_select: Callable[[int], None],
        state_provider: Callable[[int], Tuple[bool, int]],
        on_background_tap: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent, fg_color="transparent")
        self._on_activate = on_activate
        self._on_toggle_select = on_toggle_select
        self._state_provider = state_provider
        self._on_background_tap = on_background_tap

        self._photos: List[dict] = []
        self._aspects: Dict[int, float] = {}          # photo index -> w/h
        self._visible: List[int] = []                 # photo indices shown (after filter)
        self._density = 4
        self._active: Optional[int] = None
        self._layout: Layout = Layout([], [], 0, 1)
        self._live: Dict[int, dict] = {}              # slot -> canvas item ids / refs
        self._base = _LRU(_BASE_CACHE_SIZE)           # path -> PIL thumbnail
        self._photo_cache = _LRU(_PHOTO_CACHE_SIZE)   # (path, w, h) -> PhotoImage
        self._relayout_job: Optional[str] = None
        self._visible_job: Optional[str] = None
        self._probe_job: Optional[str] = None
        self._dirty_aspects = False
        self._last_width = 0
        self._press: Optional[Tuple[float, float, float]] = None
        self._dragging = False

        self.canvas = tk.Canvas(
            self, bg=COLORS["bg_card"], highlightthickness=0, bd=0, takefocus=0, yscrollincrement=30,
        )
        self.scrollbar = ctk.CTkScrollbar(self, command=self.canvas.yview, width=14)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.scrollbar.pack(side="right", fill="y", padx=(4, 0))
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)           # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._scroll_units(-3))  # Linux
        self.canvas.bind("<Button-5>", lambda e: self._scroll_units(3))

        self._loader = ThumbnailLoader(thumb_cache, bridge, self._on_thumb_loaded)
        self._bridge = bridge

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_photos(self, photos: Sequence[dict]) -> None:
        self._photos = list(photos)
        self._aspects.clear()
        for i, p in enumerate(self._photos):
            if p.get("width") and p.get("height"):
                self._aspects[i] = p["width"] / p["height"]
        self._visible = list(range(len(self._photos)))
        self._loader.probe_sizes(
            [(i, p["filepath"]) for i, p in enumerate(self._photos) if i not in self._aspects],
            self._on_sizes_probed,
        )
        self._schedule_relayout(immediate=True)

    def set_visible(self, indices: Sequence[int]) -> None:
        self._visible = list(indices)
        self.canvas.yview_moveto(0)
        self._schedule_relayout(immediate=True)

    def set_density(self, density: int) -> None:
        if density != self._density:
            self._density = density
            self._schedule_relayout(immediate=True, keep_anchor=True)

    def set_active(self, idx: Optional[int]) -> None:
        old, self._active = self._active, idx
        for i in (old, idx):
            if i is not None:
                self.refresh_tile(i)

    def refresh_tile(self, idx: int) -> None:
        """Update selection badge / quantity chip / active outline of one tile in place."""
        slot = self._slot_for_photo(idx)
        live = self._live.get(slot) if slot is not None else None
        if not live:
            return
        self._apply_state(live, idx)

    def refresh_all_tiles(self) -> None:
        for slot, live in self._live.items():
            self._apply_state(live, self._visible[slot])

    def shutdown(self) -> None:
        self._loader.shutdown()
        for job in (self._relayout_job, self._visible_job, self._probe_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _slot_for_photo(self, idx: int) -> Optional[int]:
        try:
            return self._visible.index(idx)
        except ValueError:
            return None

    def _schedule_relayout(self, immediate: bool = False, keep_anchor: bool = False) -> None:
        if self._relayout_job:
            self.after_cancel(self._relayout_job)
        delay = 0 if immediate else 120
        self._relayout_job = self.after(delay, lambda: self._relayout(keep_anchor))

    def _on_canvas_configure(self, event) -> None:
        if abs(event.width - self._last_width) > 2:
            self._last_width = event.width
            self._schedule_relayout(keep_anchor=True)
        else:
            self._schedule_visible_update()

    def _on_sizes_probed(self, sizes: Dict[int, Tuple[int, int]]) -> None:
        for idx, (w, h) in sizes.items():
            self._aspects[idx] = w / h
            if idx < len(self._photos):
                self._photos[idx]["width"], self._photos[idx]["height"] = w, h
        self._dirty_aspects = True
        if self._probe_job is None:  # coalesce bursts of probe results
            self._probe_job = self.after(250, self._apply_probed_aspects)

    def _apply_probed_aspects(self) -> None:
        self._probe_job = None
        if self._dirty_aspects:
            self._dirty_aspects = False
            self._relayout(keep_anchor=True)

    def _relayout(self, keep_anchor: bool = False) -> None:
        self._relayout_job = None
        width = self.canvas.winfo_width()
        if width < 50:
            width = 480  # not mapped yet (or headless); real width arrives via <Configure>
        anchor = self._anchor_photo() if keep_anchor else None

        aspects = [self._aspects.get(i, DEFAULT_ASPECT) for i in self._visible]
        self._layout = compute_justified_layout(aspects, width, self._density, GALLERY["grid_gap"])
        self.canvas.configure(scrollregion=(0, 0, width, max(self._layout.total_height, 1)))

        self._clear_live()
        if anchor is not None and anchor in self._visible:
            slot = self._visible.index(anchor)
            if slot < len(self._layout.tiles):
                y = self._layout.tiles[slot].y
                total = max(self._layout.total_height, 1)
                self.canvas.yview_moveto(max(0.0, min(1.0, y / total)))
        self._update_visible()

    def _anchor_photo(self) -> Optional[int]:
        """Photo index of the first tile in view (to keep scroll position on relayout)."""
        if not self._layout.rows or not self._visible:
            return None
        top = self.canvas.canvasy(0)
        first, _ = self._layout.rows_in_range(top, top + 1)
        if first >= len(self._layout.rows):
            return None
        slot = self._layout.rows[first].first
        return self._visible[slot] if slot < len(self._visible) else None

    # ------------------------------------------------------------------
    # Virtualisation
    # ------------------------------------------------------------------
    def _on_yscroll(self, first, last) -> None:
        self.scrollbar.set(first, last)
        self._schedule_visible_update()

    def _schedule_visible_update(self) -> None:
        if self._visible_job is None:
            self._visible_job = self.after_idle(self._update_visible)

    def _clear_live(self) -> None:
        self.canvas.delete("tile")
        self._live.clear()

    def _update_visible(self) -> None:
        self._visible_job = None
        lay = self._layout
        if not lay.tiles:
            self._clear_live()
            self._loader.set_wanted([])
            return
        view_h = max(self.canvas.winfo_height(), 1)
        top = self.canvas.canvasy(0)
        margin = view_h * GALLERY["grid_prefetch_viewports"]
        first_row, last_row = lay.rows_in_range(top - margin, top + view_h + margin)
        if last_row <= first_row:
            return
        slots = set(range(lay.rows[first_row].first, lay.rows[last_row - 1].last))

        for slot in [s for s in self._live if s not in slots]:
            self.canvas.delete(f"t{slot}")
            del self._live[slot]

        for slot in sorted(slots):
            if slot not in self._live:
                self._create_tile(slot)

        self._loader.set_wanted(self._photos[self._visible[s]]["filepath"] for s in slots)
        self._request_missing_images(slots, top)

    # ------------------------------------------------------------------
    # Tile items
    # ------------------------------------------------------------------
    def _create_tile(self, slot: int) -> None:
        tile = self._layout.tiles[slot]
        idx = self._visible[slot]
        tag, c = f"t{slot}", self.canvas
        x0, y0, x1, y1 = tile.x, tile.y, tile.x + tile.w, tile.y + tile.h
        small = tile.w < GALLERY["small_tile_width"]
        r = GALLERY["badge_radius_small"] if small else GALLERY["badge_radius"]
        cx, cy = x1 - r - 3, y0 + r + 3

        ids = {
            "ph": c.create_rectangle(x0, y0, x1, y1, fill=COLORS["surface"], outline="", tags=("tile", tag)),
            "img": c.create_image(x0, y0, anchor="nw", tags=("tile", tag)),
            "outline": c.create_rectangle(
                x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline=COLORS["primary"], width=GALLERY["active_border"],
                state="hidden", tags=("tile", tag),
            ),
            "badge": c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=COLORS["bg_dark"],
                                   outline=COLORS["text_muted"], width=2, tags=("tile", tag)),
            "check": c.create_text(cx, cy, text="✓", fill=COLORS["text"], state="hidden",
                                   font=(FONTS["badge"][0], max(8, r - 2), "bold"), tags=("tile", tag)),
            "chip_bg": c.create_rectangle(0, 0, 0, 0, fill=COLORS["primary"], outline="",
                                          state="hidden", tags=("tile", tag)),
            "chip": c.create_text(x0 + 6, y1 - 5, anchor="sw", text="", fill=COLORS["text"],
                                  font=FONTS["badge"], state="hidden", tags=("tile", tag)),
            "photo": None,
            "geom": (x0, y0, x1, y1, cx, cy, r),
        }
        self._live[slot] = ids
        self._apply_state(ids, idx)
        self._attach_cached_image(slot, ids)

    def _apply_state(self, ids: dict, idx: int) -> None:
        c = self.canvas
        selected, prints = self._state_provider(idx)
        c.itemconfigure(ids["outline"], state="normal" if idx == self._active else "hidden")
        c.itemconfigure(ids["badge"], fill=COLORS["success"] if selected else COLORS["bg_dark"],
                        outline=COLORS["success"] if selected else COLORS["text_muted"])
        c.itemconfigure(ids["check"], state="normal" if selected else "hidden")
        if selected and prints > 0 and ids["geom"][2] - ids["geom"][0] >= GALLERY["small_tile_width"]:
            x0, _, _, y1 = ids["geom"][:4]
            c.itemconfigure(ids["chip"], text=f"×{prints}", state="normal")
            bx0, by0, bx1, by1 = c.bbox(ids["chip"])
            c.coords(ids["chip_bg"], bx0 - 4, by0 - 1, bx1 + 4, by1 + 1)
            c.itemconfigure(ids["chip_bg"], state="normal")
            c.tag_raise(ids["chip_bg"])
            c.tag_raise(ids["chip"])
        else:
            c.itemconfigure(ids["chip"], state="hidden")
            c.itemconfigure(ids["chip_bg"], state="hidden")

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def _photo_for(self, path: str, w: int, h: int) -> Optional[ImageTk.PhotoImage]:
        key = (path, w, h)
        photo = self._photo_cache.get_touch(key)
        if photo is not None:
            return photo
        base = self._base.get_touch(path)
        if base is None:
            return None
        resized = base.resize((w, h), Image.Resampling.BILINEAR if base.width < w * 2 else Image.Resampling.BOX)
        photo = ImageTk.PhotoImage(resized)
        self._photo_cache.put(key, photo)
        return photo

    def _attach_cached_image(self, slot: int, ids: dict) -> bool:
        tile = self._layout.tiles[slot]
        path = self._photos[self._visible[slot]]["filepath"]
        photo = self._photo_for(path, tile.w, tile.h)
        if photo is None:
            return False
        ids["photo"] = photo  # keep a reference or Tk drops the image
        self.canvas.itemconfigure(ids["img"], image=photo)
        self.canvas.itemconfigure(ids["ph"], state="hidden")
        return True

    def _request_missing_images(self, slots: Set[int], top: float) -> None:
        for slot in slots:
            ids = self._live.get(slot)
            if ids is None or ids["photo"] is not None:
                continue
            path = self._photos[self._visible[slot]]["filepath"]
            self._loader.request(path, priority=abs(self._layout.tiles[slot].y - top))

    def _on_thumb_loaded(self, path: str, img: Optional[Image.Image]) -> None:
        if img is None:
            return
        self._base.put(path, img)
        for slot, ids in list(self._live.items()):
            if self._photos[self._visible[slot]]["filepath"] == path and ids["photo"] is None:
                self._attach_cached_image(slot, ids)

    # ------------------------------------------------------------------
    # Input: tap vs. drag-to-scroll (touch friendly)
    # ------------------------------------------------------------------
    def _on_press(self, event) -> None:
        self._press = (event.x, event.y, self.canvas.yview()[0])
        self._dragging = False

    def _on_motion(self, event) -> None:
        if self._press is None:
            return
        px, py, start_frac = self._press
        if not self._dragging and abs(event.y - py) > _DRAG_THRESHOLD_PX:
            self._dragging = True
        if self._dragging:
            total = max(self._layout.total_height, 1)
            self.canvas.yview_moveto(max(0.0, start_frac + (py - event.y) / total))

    def _on_release(self, event) -> None:
        was_drag, self._press = self._dragging, None
        self._dragging = False
        if was_drag:
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        slot = self._layout.tile_at(cx, cy)
        if slot < 0:
            if self._on_background_tap:
                self._on_background_tap()
            return
        idx = self._visible[slot]
        ids = self._live.get(slot)
        if ids:
            _, _, _, _, bx, by, r = ids["geom"]
            hit = max(r + 8, 18)  # generous touch target around the badge
            if abs(cx - bx) <= hit and abs(cy - by) <= hit and self._layout.tiles[slot].w >= 40:
                self._on_toggle_select(idx)
                return
        self._on_activate(idx)

    def _on_wheel(self, event) -> None:
        step = -1 if event.delta > 0 else 1
        self._scroll_units(step * 3)

    def _scroll_units(self, units: int) -> None:
        self.canvas.yview_scroll(units, "units")
