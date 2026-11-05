"""Persistent print-size cards for the gallery editor.

Widgets are created once and then updated in place with diffed ``configure`` calls
(only properties that actually changed are touched). This is what keeps the
editor from flickering when a quantity, paper type or active card changes.
Rows only gain or lose widgets when the *number* of cards changes.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence

import customtkinter as ctk

from gui.theme import COLORS, FONTS, GALLERY, lighten


class _Diffed:
    """Mixin: configure widgets only when a value really changed."""

    def _init_diff(self) -> None:
        self._diff_widgets: Dict[str, Any] = {}
        self._diff_applied: Dict[str, Dict[str, Any]] = {}

    def _diff_reg(self, name: str, widget) -> Any:
        self._diff_widgets[name] = widget
        self._diff_applied[name] = {}
        return widget

    def _diff_set(self, name: str, **kwargs) -> None:
        applied = self._diff_applied[name]
        changed = {k: v for k, v in kwargs.items() if applied.get(k, _MISSING) != v}
        if changed:
            applied.update(changed)
            self._diff_widgets[name].configure(**changed)


_MISSING = object()


class SizeCard(ctk.CTkFrame, _Diffed):
    """One print-size card: header, active badge, +/- stepper, paper menu, price, remove."""

    def __init__(
        self,
        parent,
        index: int,
        finish_names: Sequence[str],
        on_select: Callable[[int], None],
        on_plus: Callable[[int], None],
        on_minus: Callable[[int], None],
        on_paper: Callable[[int, str], None],
        on_remove: Callable[[int], None],
    ):
        super().__init__(parent, fg_color="transparent", width=GALLERY["size_card_width"])
        self._init_diff()
        self.card_index = index
        w, h = GALLERY["size_card_width"], GALLERY["size_card_height"]

        card = self._diff_reg("card", ctk.CTkFrame(
            self, fg_color=COLORS["surface"], border_color=COLORS["border"], border_width=1,
            corner_radius=10, width=w, height=h,
        ))
        card.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 0))
        size_lbl = self._diff_reg("size", ctk.CTkLabel(hdr, text="", font=FONTS["body_bold"]))
        size_lbl.pack(side="left", anchor="w")
        del_btn = ctk.CTkButton(
            hdr, text="✕", font=FONTS["small"], width=24, height=24, corner_radius=12,
            fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"], text_color=COLORS["text"],
            command=lambda: on_remove(self.card_index),
        )
        del_btn.pack(side="right")

        badge = self._diff_reg("badge", ctk.CTkLabel(card, text="", font=FONTS["small"]))
        badge.pack(pady=(0, 2))

        ctk.CTkButton(
            card, text="+", font=FONTS["subtitle"], fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"], height=28,
            command=lambda: on_plus(self.card_index),
        ).pack(fill="x", padx=10, pady=(2, 2))

        count = self._diff_reg("count", ctk.CTkLabel(card, text="", font=FONTS["subtitle"]))
        count.pack(pady=1)

        ctk.CTkButton(
            card, text="-", font=FONTS["subtitle"], fg_color=COLORS["border"],
            hover_color=COLORS["danger"], height=28,
            command=lambda: on_minus(self.card_index),
        ).pack(fill="x", padx=10, pady=(2, 2))

        menu = self._diff_reg("paper", ctk.CTkOptionMenu(
            card, values=list(finish_names), font=FONTS["small"], height=26,
            command=lambda val: on_paper(self.card_index, val),
        ))
        menu.pack(fill="x", padx=8, pady=2)

        price = self._diff_reg("price", ctk.CTkLabel(card, text="", font=FONTS["small"], text_color=COLORS["text_muted"]))
        price.pack(pady=(0, 4))

        rem = self._diff_reg("remove", ctk.CTkButton(
            self, text="", font=FONTS["small"], fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"],
            text_color=COLORS["text"], height=26, command=lambda: on_remove(self.card_index),
        ))
        rem.pack(fill="x", padx=8, pady=(4, 0))

        for widget in (self, card, size_lbl, badge, count, price):
            widget.bind("<Button-1>", lambda e: on_select(self.card_index))

    def apply(self, data: Dict[str, Any], active: bool, price: float, texts: Dict[str, str]) -> None:
        self._diff_set("card",
                  fg_color=lighten(COLORS["surface"], 0.06) if active else COLORS["surface"],
                  border_color=COLORS["primary"] if active else COLORS["border"],
                  border_width=GALLERY["active_border"] if active else 1)
        self._diff_set("size", text=data["size_name"], text_color=COLORS["primary"] if active else COLORS["text"])
        self._diff_set("badge",
                  text=f"● {texts['active']}" if active else f"○ {texts['tap']}",
                  text_color=COLORS["primary"] if active else COLORS["text_muted"])
        self._diff_set("count", text=f"{texts['qty']}: {data['count']}",
                  text_color=COLORS["primary"] if data["count"] > 0 else COLORS["text"])
        if self._diff_applied["paper"].get("value") != data["paper_type"]:
            self._diff_applied["paper"]["value"] = data["paper_type"]
            self._diff_widgets["paper"].set(data["paper_type"])
        self._diff_set("price", text=f"{price:.2f} PLN" if price > 0 else "N/A")
        self._diff_set("remove", text=f"✕ {texts['remove']}")


class SizeCardsRow(ctk.CTkScrollableFrame):
    """Horizontal strip of SizeCard widgets plus the trailing '+ Add Size' tile."""

    def __init__(
        self,
        parent,
        finish_names: Sequence[str],
        on_select: Callable[[int], None],
        on_plus: Callable[[int], None],
        on_minus: Callable[[int], None],
        on_paper: Callable[[int, str], None],
        on_remove: Callable[[int], None],
        on_add: Callable[[], None],
        on_background_tap: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent, fg_color="transparent", orientation="horizontal",
                         height=GALLERY["cards_row_height"])
        self._row_finishes = list(finish_names)
        self._row_cbs = (on_select, on_plus, on_minus, on_paper, on_remove)
        self._row_pool: List[SizeCard] = []
        self._row_laid = -1
        self._row_add_text = None

        self.empty_lbl = ctk.CTkLabel(self, text="", font=FONTS["body"], text_color=COLORS["text_muted"])
        self.add_btn = ctk.CTkButton(
            self, text="", font=FONTS["title"], fg_color=COLORS["surface"], hover_color=COLORS["primary_hover"],
            border_width=2, border_color=COLORS["primary"], corner_radius=10,
            width=GALLERY["size_card_width"], height=GALLERY["size_card_height"], command=on_add,
        )
        if on_background_tap:
            self.bind("<Button-1>", lambda e: on_background_tap())

    def sync(self, cards: List[Dict[str, Any]], active_idx: int,
             price_fn: Callable[[str, str], float], texts: Dict[str, str]) -> None:
        """Bring the strip in line with ``cards`` touching only what changed."""
        while len(self._row_pool) < len(cards):
            self._row_pool.append(SizeCard(self, len(self._row_pool), self._row_finishes, *self._row_cbs))
        while len(self._row_pool) > len(cards):
            self._row_pool.pop().destroy()

        if self._row_laid != len(cards):
            self._regrid(len(cards))

        for i, (widget, data) in enumerate(zip(self._row_pool, cards)):
            widget.apply(data, i == active_idx, price_fn(data["size_name"], data["paper_type"]), texts)

        if self._row_add_text != texts["add"]:
            self._row_add_text = texts["add"]
            self.add_btn.configure(text=f"+\n{texts['add']}")
        if self.empty_lbl.cget("text") != texts["empty"]:
            self.empty_lbl.configure(text=texts["empty"])

    def _regrid(self, n: int) -> None:
        self._row_laid = n
        for child in (self.empty_lbl, self.add_btn, *self._row_pool):
            child.grid_forget()
        col = 0
        if n == 0:
            self.empty_lbl.grid(row=0, column=col, padx=10, pady=20)
            col += 1
        for card in self._row_pool:
            card.grid(row=0, column=col, padx=8, pady=4, sticky="n")
            col += 1
        self.add_btn.grid(row=0, column=col, padx=8, pady=4, sticky="n")


class SizePickerPopup(ctk.CTkFrame):
    """Small scrollable list of sizes that closes itself after a period of inactivity."""

    def __init__(self, parent, options: Sequence[str], title: str,
                 on_pick: Callable[[str], None], on_close: Callable[[], None]):
        super().__init__(parent, fg_color=COLORS["surface"], border_width=2,
                         border_color=COLORS["primary"], corner_radius=8)
        self._on_close = on_close
        self._autoclose_job: Optional[str] = None

        ctk.CTkLabel(self, text=title, font=FONTS["body_bold"], text_color=COLORS["primary"]).pack(padx=12, pady=(8, 4))
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", height=130, width=180)
        scroll.pack(padx=8, pady=(0, 8))
        for name in options:
            btn = ctk.CTkButton(scroll, text=name, font=FONTS["body"], fg_color="transparent",
                                hover_color=COLORS["primary_hover"], height=32,
                                command=lambda n=name: on_pick(n))
            btn.pack(fill="x", pady=2)
            btn.bind("<Button-1>", lambda e: self.reset_timer(), add="+")
        self.bind("<Button-1>", lambda e: self.reset_timer())
        self.reset_timer()

    def reset_timer(self) -> None:
        self._cancel()
        self._autoclose_job = self.after(GALLERY["dropdown_autoclose_ms"], self._on_close)

    def _cancel(self) -> None:
        if self._autoclose_job is not None:
            try:
                self.after_cancel(self._autoclose_job)
            except Exception:
                pass
            self._autoclose_job = None

    def destroy(self) -> None:
        self._cancel()
        super().destroy()
