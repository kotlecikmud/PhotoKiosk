"""Screen 3: Touchscreen print configuration & live transformation preview."""
import customtkinter as ctk
from PIL import Image
from typing import Callable, List, Dict, Any, Optional
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from backend.database import get_db_engine, PrintSize, PaperFinish
from imaging.preview import generate_preview
from sqlalchemy.orm import sessionmaker


class PrintConfigScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        selected_photos: List[Dict[str, Any]],
        on_back: Callable[[], None],
        on_proceed_to_summary: Callable[[List[Dict[str, Any]]], None]
    ):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.selected_photos = selected_photos
        self.on_back = on_back
        self.on_proceed_to_summary = on_proceed_to_summary

        # Load print sizes & finishes from DB
        engine = get_db_engine()
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            self.print_sizes = db.query(PrintSize).filter_by(is_active=True).order_by(PrintSize.sort_order).all()
            self.finishes = db.query(PaperFinish).filter_by(is_active=True).order_by(PaperFinish.sort_order).all()
        finally:
            db.close()

        # State variables
        self.current_photo_idx = 0
        self.selected_size_var = ctk.StringVar(value=self.print_sizes[0].name if self.print_sizes else "10x15")
        self.selected_finish_var = ctk.StringVar(value=self.finishes[0].name if self.finishes else "MATTE")
        self.selected_mode_var = ctk.StringVar(value="fill_in")
        self.quantity_var = ctk.IntVar(value=1)

        # Config map for all photos: photo_filepath -> dict(size, finish, mode, qty)
        self.photo_configs: Dict[str, Dict[str, Any]] = {}
        for p in self.selected_photos:
            self.photo_configs[p["filepath"]] = {
                "print_size_name": self.selected_size_var.get(),
                "paper_finish": self.selected_finish_var.get(),
                "crop_mode": self.selected_mode_var.get(),
                "quantity": 1,
            }

        self._build_ui()
        self._update_preview()

    def _build_ui(self):
        # Header
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=30, pady=(20, 10))

        back_btn = ctk.CTkButton(
            top_bar,
            text="← Gallery",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=120,
            height=TOUCH_SIZES["button_height"],
            command=self.on_back
        )
        back_btn.pack(side="left")

        title = ctk.CTkLabel(
            top_bar,
            text="Configure Prints",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        title.pack(side="left", padx=20)

        # Batch apply toggle button
        batch_btn = ctk.CTkButton(
            top_bar,
            text="Apply Settings to All Photos",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["primary_hover"],
            height=TOUCH_SIZES["button_height"],
            command=self._apply_to_all
        )
        batch_btn.pack(side="right")

        # Two panel body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=30, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left controls panel
        controls_frame = ctk.CTkScrollableFrame(body, fg_color=COLORS["bg_card"], corner_radius=12)
        controls_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Photo navigation if multiple
        if len(self.selected_photos) > 1:
            nav_frame = ctk.CTkFrame(controls_frame, fg_color=COLORS["surface"], corner_radius=8)
            nav_frame.pack(fill="x", padx=16, pady=12)

            prev_btn = ctk.CTkButton(nav_frame, text="< Prev", width=80, height=40, font=FONTS["button"], command=self._prev_photo)
            prev_btn.pack(side="left", padx=8, pady=8)

            self.nav_lbl = ctk.CTkLabel(nav_frame, text=f"Photo 1 of {len(self.selected_photos)}", font=FONTS["body_bold"])
            self.nav_lbl.pack(side="left", expand=True)

            next_btn = ctk.CTkButton(nav_frame, text="Next >", width=80, height=40, font=FONTS["button"], command=self._next_photo)
            next_btn.pack(side="right", padx=8, pady=8)

        # 1. Print Format Selection
        lbl1 = ctk.CTkLabel(controls_frame, text="1. Print Size", font=FONTS["subtitle"], text_color=COLORS["primary"])
        lbl1.pack(anchor="w", padx=16, pady=(16, 8))

        sizes_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        sizes_frame.pack(fill="x", padx=16)
        for s in self.print_sizes:
            rb = ctk.CTkRadioButton(
                sizes_frame,
                text=f"{s.name} ({s.width_mm:.0f}×{s.height_mm:.0f} mm)",
                variable=self.selected_size_var,
                value=s.name,
                font=FONTS["body_bold"],
                command=self._on_param_changed
            )
            rb.pack(anchor="w", pady=4)

        # 2. Paper Finish
        lbl2 = ctk.CTkLabel(controls_frame, text="2. Paper Finish", font=FONTS["subtitle"], text_color=COLORS["primary"])
        lbl2.pack(anchor="w", padx=16, pady=(16, 8))

        finish_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        finish_frame.pack(fill="x", padx=16)
        for f in self.finishes:
            rb = ctk.CTkRadioButton(
                finish_frame,
                text=f.name,
                variable=self.selected_finish_var,
                value=f.name,
                font=FONTS["body_bold"],
                command=self._on_param_changed
            )
            rb.pack(side="left", padx=(0, 20))

        # 3. Crop Mode Selection
        lbl3 = ctk.CTkLabel(controls_frame, text="3. Crop Mode", font=FONTS["subtitle"], text_color=COLORS["primary"])
        lbl3.pack(anchor="w", padx=16, pady=(20, 8))

        modes = [
            ("fill_in", "Fill-in (Crop excess, fill paper full)"),
            ("fit_in", "Fit-in (Preserve full image, white margins)"),
            ("border", "Border 4mm (Classic white studio border)"),
            ("no_resize", "No-resize (Original pixels & embedded DPI)"),
        ]
        for mode_val, mode_text in modes:
            rb = ctk.CTkRadioButton(
                controls_frame,
                text=mode_text,
                variable=self.selected_mode_var,
                value=mode_val,
                font=FONTS["body"],
                command=self._on_param_changed
            )
            rb.pack(anchor="w", padx=16, pady=4)

        # 4. Quantity Stepper
        lbl4 = ctk.CTkLabel(controls_frame, text="4. Quantity (Copies)", font=FONTS["subtitle"], text_color=COLORS["primary"])
        lbl4.pack(anchor="w", padx=16, pady=(20, 8))

        qty_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        qty_frame.pack(anchor="w", padx=16, pady=(0, 20))

        minus_btn = ctk.CTkButton(qty_frame, text="-", width=56, height=56, font=FONTS["title"], command=self._decrement_qty)
        minus_btn.pack(side="left")

        self.qty_lbl = ctk.CTkLabel(qty_frame, text="1", font=FONTS["title"], width=80)
        self.qty_lbl.pack(side="left", padx=12)

        plus_btn = ctk.CTkButton(qty_frame, text="+", width=56, height=56, font=FONTS["title"], command=self._increment_qty)
        plus_btn.pack(side="left")

        # Right Preview Panel
        preview_frame = ctk.CTkFrame(body, fg_color=COLORS["bg_card"], corner_radius=12)
        preview_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        preview_title = ctk.CTkLabel(preview_frame, text="Visual Print Preview", font=FONTS["subtitle"], text_color=COLORS["text"])
        preview_title.pack(anchor="w", padx=20, pady=(20, 8))

        self.preview_canvas_label = ctk.CTkLabel(preview_frame, text="")
        self.preview_canvas_label.pack(fill="both", expand=True, padx=20, pady=10)

        self.preview_info_lbl = ctk.CTkLabel(
            preview_frame,
            text="",
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.preview_info_lbl.pack(padx=20, pady=(0, 20))

        # Bottom proceed button
        bottom_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        bottom_bar.pack(fill="x", padx=30, pady=(0, 20), ipady=8)

        order_sum_btn = ctk.CTkButton(
            bottom_bar,
            text="Review Order Summary →",
            font=FONTS["button"],
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self._proceed
        )
        order_sum_btn.pack(side="right", padx=20)

    def _on_param_changed(self):
        curr_p = self.selected_photos[self.current_photo_idx]
        self.photo_configs[curr_p["filepath"]] = {
            "print_size_name": self.selected_size_var.get(),
            "paper_finish": self.selected_finish_var.get(),
            "crop_mode": self.selected_mode_var.get(),
            "quantity": self.quantity_var.get(),
        }
        self._update_preview()

    def _increment_qty(self):
        val = self.quantity_var.get() + 1
        self.quantity_var.set(val)
        self.qty_lbl.configure(text=str(val))
        self._on_param_changed()

    def _decrement_qty(self):
        val = max(1, self.quantity_var.get() - 1)
        self.quantity_var.set(val)
        self.qty_lbl.configure(text=str(val))
        self._on_param_changed()

    def _apply_to_all(self):
        cfg = {
            "print_size_name": self.selected_size_var.get(),
            "paper_finish": self.selected_finish_var.get(),
            "crop_mode": self.selected_mode_var.get(),
            "quantity": self.quantity_var.get(),
        }
        for p in self.selected_photos:
            self.photo_configs[p["filepath"]] = dict(cfg)

    def _prev_photo(self):
        if self.current_photo_idx > 0:
            self.current_photo_idx -= 1
            self._load_active_photo_state()

    def _next_photo(self):
        if self.current_photo_idx < len(self.selected_photos) - 1:
            self.current_photo_idx += 1
            self._load_active_photo_state()

    def _load_active_photo_state(self):
        curr_p = self.selected_photos[self.current_photo_idx]
        cfg = self.photo_configs[curr_p["filepath"]]
        self.selected_size_var.set(cfg["print_size_name"])
        self.selected_finish_var.set(cfg["paper_finish"])
        self.selected_mode_var.set(cfg["crop_mode"])
        self.quantity_var.set(cfg["quantity"])
        self.qty_lbl.configure(text=str(cfg["quantity"]))
        if hasattr(self, 'nav_lbl'):
            self.nav_lbl.configure(text=f"Photo {self.current_photo_idx + 1} of {len(self.selected_photos)}")
        self._update_preview()

    def _update_preview(self):
        curr_p = self.selected_photos[self.current_photo_idx]
        size_name = self.selected_size_var.get()
        size_obj = next((s for s in self.print_sizes if s.name == size_name), None)
        if not size_obj:
            return

        with Image.open(curr_p["filepath"]) as img:
            prev_result = generate_preview(
                source_image=img,
                crop_mode=self.selected_mode_var.get(),
                paper_width_mm=size_obj.width_mm,
                paper_height_mm=size_obj.height_mm,
                border_width_mm=4.0,
                auto_orientation=True
            )

        prev_img = prev_result["preview_image"]
        ctk_img = ctk.CTkImage(light_image=prev_img, dark_image=prev_img, size=prev_img.size)
        self.preview_canvas_label.configure(image=ctk_img)

        # Update preview details
        pw, ph = prev_result["displayed_physical_mm"]
        info_lines = [
            f"Paper: {size_obj.name} | Calculated Print Dimensions: {pw:.1f} × {ph:.1f} mm",
            f"Resolution: 300 DPI | Paper Finish: {self.selected_finish_var.get()}"
        ]
        if prev_result["warnings"]:
            info_lines.append(f"⚠ Warning: {'; '.join(prev_result['warnings'])}")

        self.preview_info_lbl.configure(text="\n".join(info_lines))

    def _proceed(self):
        configured_items = []
        for p in self.selected_photos:
            cfg = self.photo_configs[p["filepath"]]
            configured_items.append({
                "source_filepath": p["filepath"],
                "filename": p["filename"],
                "print_size_name": cfg["print_size_name"],
                "paper_finish": cfg["paper_finish"],
                "crop_mode": cfg["crop_mode"],
                "quantity": cfg["quantity"],
            })
        self.on_proceed_to_summary(configured_items)
