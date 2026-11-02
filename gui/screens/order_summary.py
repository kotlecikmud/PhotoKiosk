"""Screen 4: Order Summary and final confirmation."""
import customtkinter as ctk
from typing import Callable, List, Dict, Any
from gui.theme import FONTS, COLORS, TOUCH_SIZES


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

        self._build_ui()

    def _build_ui(self):
        # Header
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=30, pady=(20, 10))

        back_btn = ctk.CTkButton(
            top_bar,
            text="← Edit Prints",
            font=FONTS["button"],
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            width=140,
            height=TOUCH_SIZES["button_height"],
            command=self.on_back
        )
        back_btn.pack(side="left")

        title = ctk.CTkLabel(
            top_bar,
            text="Order Summary & Review",
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        title.pack(side="left", padx=20)

        # Scrollable list of items
        self.items_container = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        self.items_container.pack(fill="both", expand=True, padx=30, pady=10)

        self._render_items()

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

        self.submit_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Submit Order & Print Files →",
            font=FONTS["button"],
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self._submit
        )
        self.submit_btn.pack(side="right", padx=20)

        self._update_totals()

    def _render_items(self):
        for w in self.items_container.winfo_children():
            w.destroy()

        for idx, item in enumerate(self.items):
            row = ctk.CTkFrame(self.items_container, fg_color=COLORS["surface"], corner_radius=10)
            row.pack(fill="x", pady=6, padx=10, ipady=4)

            # Details
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=16, pady=8)

            name_lbl = ctk.CTkLabel(info, text=item["filename"], font=FONTS["body_bold"], text_color=COLORS["text"])
            name_lbl.pack(anchor="w")

            detail_str = f"Format: {item['print_size_name']} | Finish: {item['paper_finish']} | Mode: {item['crop_mode']} | Qty: {item['quantity']}"
            desc_lbl = ctk.CTkLabel(info, text=detail_str, font=FONTS["body"], text_color=COLORS["text_muted"])
            desc_lbl.pack(anchor="w")

            # Remove item button
            del_btn = ctk.CTkButton(
                row,
                text="Remove",
                font=FONTS["small"],
                fg_color=COLORS["danger"],
                hover_color=COLORS["danger_hover"],
                width=80,
                height=40,
                command=lambda i=idx: self._remove_item(i)
            )
            del_btn.pack(side="right", padx=16)

    def _remove_item(self, idx: int):
        if 0 <= idx < len(self.items):
            self.items.pop(idx)
            self._render_items()
            self._update_totals()

    def _update_totals(self):
        total_items = len(self.items)
        total_prints = sum(item["quantity"] for item in self.items)
        self.total_lbl.configure(text=f"Total: {total_items} items ({total_prints} prints)")

        if total_items == 0:
            self.submit_btn.configure(state="disabled")
        else:
            self.submit_btn.configure(state="normal")

    def _submit(self):
        if self.items:
            self.on_confirm_order(self.items)
