"""Screen 3: Order Summary and final confirmation grouped by paper-size/size/count."""
from collections import defaultdict
from typing import Callable, List, Dict, Any, Optional

import customtkinter as ctk

from gui.theme import FONTS, COLORS, TOUCH_SIZES
from backend.database import get_db_engine, get_all_service_prices_map
from backend.i18n import get_i18n, t
from gui.language_toggle import LanguageToggle
from sqlalchemy.orm import sessionmaker


class OrderSummaryScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        items: List[Dict[str, Any]],
        on_back: Callable[[], None],
        on_confirm_order: Callable[[List[Dict[str, Any]]], None]
    ):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.items = list(items)
        self.on_back = on_back
        self.on_confirm_order = on_confirm_order
        self.i18n = get_i18n()

        # Load price map
        try:
            self.service_prices = get_all_service_prices_map()
        except Exception:
            self.service_prices = {}

        # Fill unit_price and subtotal if missing
        for item in self.items:
            if "unit_price" not in item:
                key = (item["print_size_name"].strip().lower(), item["paper_finish"].strip().lower())
                item["unit_price"] = self.service_prices.get(key, 0.0)
            item["subtotal"] = item["unit_price"] * item["quantity"]

        self._build_ui()
        self.i18n.subscribe(self._on_language_changed)

    def _on_language_changed(self, lang: str):
        if self.winfo_exists():
            self._update_localized_texts()
            self._render_view()

    def destroy(self):
        self.i18n.unsubscribe(self._on_language_changed)
        super().destroy()

    def _build_ui(self):
        # Header
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=30, pady=(20, 10))

        self.back_btn = ctk.CTkButton(
            top_bar,
            text=t("order_summary.btn_back"),
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=140,
            height=TOUCH_SIZES["button_height"],
            command=self.on_back
        )
        self.back_btn.pack(side="left")

        self.title_lbl = ctk.CTkLabel(
            top_bar,
            text=t("order_summary.title"),
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.title_lbl.pack(side="left", padx=20)

        # Language toggle on top right
        self.lang_toggle = LanguageToggle(top_bar)
        self.lang_toggle.pack(side="right")

        # Main body: Left groups summary, Right individual items list
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=30, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left: Grouped by paper finish / size / count
        grouped_container = ctk.CTkFrame(body, fg_color=COLORS["bg_card"], corner_radius=12)
        grouped_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.g_title = ctk.CTkLabel(
            grouped_container,
            text=t("order_summary.section_grouped"),
            font=FONTS["subtitle"],
            text_color=COLORS["primary"]
        )
        self.g_title.pack(anchor="w", padx=16, pady=(16, 8))

        self.grouped_scroll = ctk.CTkScrollableFrame(grouped_container, fg_color="transparent")
        self.grouped_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Right: Individual items scrollable list
        items_box = ctk.CTkFrame(body, fg_color=COLORS["bg_card"], corner_radius=12)
        items_box.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.i_title = ctk.CTkLabel(
            items_box,
            text=t("order_summary.section_individual"),
            font=FONTS["subtitle"],
            text_color=COLORS["primary"]
        )
        self.i_title.pack(anchor="w", padx=16, pady=(16, 8))

        self.items_container = ctk.CTkScrollableFrame(items_box, fg_color="transparent")
        self.items_container.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Bottom confirmation bar
        self.bottom_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        self.bottom_bar.pack(fill="x", padx=30, pady=(0, 20), ipady=8)

        self.total_lbl = ctk.CTkLabel(
            self.bottom_bar,
            text="",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.total_lbl.pack(side="left", padx=20)

        self.price_total_lbl = ctk.CTkLabel(
            self.bottom_bar,
            text="",
            font=FONTS["title"],
            text_color=COLORS["success"]
        )
        self.price_total_lbl.pack(side="left", padx=20)

        self.submit_btn = ctk.CTkButton(
            self.bottom_bar,
            text=t("order_summary.btn_submit"),
            font=FONTS["button"],
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self._submit
        )
        self.submit_btn.pack(side="right", padx=20)

        self._render_view()

    def _update_localized_texts(self):
        if hasattr(self, "back_btn") and self.back_btn.winfo_exists():
            self.back_btn.configure(text=t("order_summary.btn_back"))
        if hasattr(self, "title_lbl") and self.title_lbl.winfo_exists():
            self.title_lbl.configure(text=t("order_summary.title"))
        if hasattr(self, "g_title") and self.g_title.winfo_exists():
            self.g_title.configure(text=t("order_summary.section_grouped"))
        if hasattr(self, "i_title") and self.i_title.winfo_exists():
            self.i_title.configure(text=t("order_summary.section_individual"))
        if hasattr(self, "submit_btn") and self.submit_btn.winfo_exists():
            self.submit_btn.configure(text=t("order_summary.btn_submit"))

    def _render_view(self):
        self._render_grouped_summary()
        self._render_individual_items()
        self._update_totals()

    def _render_grouped_summary(self):
        for w in self.grouped_scroll.winfo_children():
            w.destroy()

        # Group items by (paper_finish, size_name)
        groups = defaultdict(lambda: {"count": 0, "unit_price": 0.0, "subtotal": 0.0, "items_count": 0})
        for item in self.items:
            key = (item["paper_finish"].upper(), item["print_size_name"])
            qty = item["quantity"]
            unit_p = item.get("unit_price", 0.0)
            groups[key]["count"] += qty
            groups[key]["unit_price"] = unit_p
            groups[key]["subtotal"] += (unit_p * qty)
            groups[key]["items_count"] += 1

        if not groups:
            empty_lbl = ctk.CTkLabel(self.grouped_scroll, text=t("order_summary.empty_items"), font=FONTS["body"])
            empty_lbl.pack(pady=30)
            return

        for (finish, size), data in sorted(groups.items()):
            row = ctk.CTkFrame(self.grouped_scroll, fg_color=COLORS["surface"], corner_radius=8)
            row.pack(fill="x", pady=5, padx=4, ipady=6)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=12, pady=4)

            finish_localized = t(f"paper_finishes.{finish.lower()}")
            if finish_localized.startswith("paper_finishes."):
                finish_localized = finish
            title_txt = f"{size} {finish_localized}"
            lbl_title = ctk.CTkLabel(info, text=title_txt, font=FONTS["body_bold"], text_color=COLORS["text"])
            lbl_title.pack(anchor="w")

            detail_txt = t("order_summary.total_prints_group", count=data['count'], items=data['items_count'])
            if data["unit_price"] > 0:
                detail_txt += f" @ {data['unit_price']:.2f} PLN"
            lbl_desc = ctk.CTkLabel(info, text=detail_txt, font=FONTS["small"], text_color=COLORS["text_muted"])
            lbl_desc.pack(anchor="w")

            # Subtotal on right
            subtotal_box = ctk.CTkFrame(row, fg_color="transparent")
            subtotal_box.pack(side="right", padx=12)

            price_txt = f"{data['subtotal']:.2f} PLN" if data["subtotal"] > 0 else "-"
            lbl_price = ctk.CTkLabel(subtotal_box, text=price_txt, font=FONTS["subtitle"], text_color=COLORS["primary"])
            lbl_price.pack(anchor="e")

    def _render_individual_items(self):
        for w in self.items_container.winfo_children():
            w.destroy()

        for idx, item in enumerate(self.items):
            row = ctk.CTkFrame(self.items_container, fg_color=COLORS["surface"], corner_radius=8)
            row.pack(fill="x", pady=4, padx=4, ipady=4)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=12, pady=4)

            name_lbl = ctk.CTkLabel(info, text=item["filename"], font=FONTS["body_bold"], text_color=COLORS["text"])
            name_lbl.pack(anchor="w")

            detail_str = f"{item['print_size_name']} | {item['paper_finish']} | {item['crop_mode']} | Qty: {item['quantity']}"
            if item.get("unit_price", 0) > 0:
                detail_str += f" | {item['subtotal']:.2f} PLN"
            desc_lbl = ctk.CTkLabel(info, text=detail_str, font=FONTS["small"], text_color=COLORS["text_muted"])
            desc_lbl.pack(anchor="w")

            # Remove item button
            del_btn = ctk.CTkButton(
                row,
                text="✕",
                font=FONTS["small"],
                fg_color=COLORS["danger"],
                hover_color=COLORS["danger_hover"],
                width=36,
                height=32,
                command=lambda i=idx: self._remove_item(i)
            )
            del_btn.pack(side="right", padx=10)

    def _remove_item(self, idx: int):
        if 0 <= idx < len(self.items):
            self.items.pop(idx)
            self._render_view()

    def _update_totals(self):
        total_items = len(self.items)
        total_prints = sum(item["quantity"] for item in self.items)
        grand_total_price = sum(item.get("subtotal", 0.0) for item in self.items)

        self.total_lbl.configure(text=f"Total: {total_items} items ({total_prints} prints)")
        if grand_total_price > 0:
            self.price_total_lbl.configure(text=f"{grand_total_price:.2f} PLN")
        else:
            self.price_total_lbl.configure(text="")

        if total_items == 0 or total_prints == 0:
            self.submit_btn.configure(state="disabled")
        else:
            self.submit_btn.configure(state="normal")

    def _submit(self):
        if self.items:
            self.on_confirm_order(self.items)
