"""Screen 5 & 6: Order Processing & Completion screens."""
import threading
import customtkinter as ctk
from typing import Callable, List, Dict, Any
from gui.theme import FONTS, COLORS, TOUCH_SIZES
from backend.orders import OrderProcessor
from backend.database import get_db_engine
from sqlalchemy.orm import sessionmaker
from backend.i18n import t


class ProcessingScreen(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        items: List[Dict[str, Any]],
        on_completed: Callable[[str, int], None],
        on_error: Callable[[str], None]
    ):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.items = items
        self.on_completed = on_completed
        self.on_error = on_error

        self._build_ui()
        self._start_processing()

    def _build_ui(self):
        center_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16)
        center_card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.6, relheight=0.45)

        self.title_lbl = ctk.CTkLabel(
            center_card,
            text=t("processing.title_preparing"),
            font=FONTS["title_hero"],
            text_color=COLORS["primary"]
        )
        self.title_lbl.pack(pady=(40, 16))

        self.status_lbl = ctk.CTkLabel(
            center_card,
            text=t("processing.subtitle_preparing"),
            font=FONTS["subtitle"],
            text_color=COLORS["text_muted"]
        )
        self.status_lbl.pack(pady=10)

        self.progressbar = ctk.CTkProgressBar(center_card, width=400, height=20, corner_radius=10)
        self.progressbar.pack(pady=30)
        self.progressbar.set(0.1)

    def _start_processing(self):
        def worker():
            try:
                engine = get_db_engine()
                Session = sessionmaker(bind=engine)
                db = Session()
                try:
                    processor = OrderProcessor()
                    self.after(0, lambda: self.progressbar.set(0.5))
                    order_id = processor.process_order(db, self.items)
                    total_prints = sum(it["quantity"] for it in self.items)
                    self.after(0, lambda: self.progressbar.set(1.0))
                    self.after(500, lambda: self.on_completed(order_id, total_prints))
                finally:
                    db.close()
            except Exception as e:
                self.after(0, lambda err=str(e): self.on_error(err))

        threading.Thread(target=worker, daemon=True).start()


class CompletionScreen(ctk.CTkFrame):
    def __init__(self, parent, order_id: str, total_prints: int, on_done: Callable[[], None]):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.order_id = order_id
        self.total_prints = total_prints
        self.on_done = on_done

        self._build_ui()

    def _build_ui(self):
        center_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16)
        center_card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.65, relheight=0.55)

        self.title_lbl = ctk.CTkLabel(
            center_card,
            text=t("processing.title_ready"),
            font=FONTS["title_hero"],
            text_color=COLORS["success"]
        )
        self.title_lbl.pack(pady=(40, 12))

        self.sub_lbl = ctk.CTkLabel(
            center_card,
            text=t("processing.subtitle_ready"),
            font=FONTS["body"],
            text_color=COLORS["text_muted"]
        )
        self.sub_lbl.pack(pady=(0, 24))

        id_frame = ctk.CTkFrame(center_card, fg_color=COLORS["surface"], corner_radius=12)
        id_frame.pack(padx=40, pady=10, fill="x")

        self.id_lbl = ctk.CTkLabel(
            id_frame,
            text=t("processing.order_id_lbl", order_id=self.order_id),
            font=FONTS["title"],
            text_color=COLORS["text"]
        )
        self.id_lbl.pack(pady=16)

        self.prints_lbl = ctk.CTkLabel(
            center_card,
            text=t("processing.total_prints_lbl", total_prints=self.total_prints),
            font=FONTS["subtitle"],
            text_color=COLORS["text"]
        )
        self.prints_lbl.pack(pady=12)

        self.finish_btn = ctk.CTkButton(
            center_card,
            text=t("processing.btn_finish"),
            font=FONTS["button"],
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            height=TOUCH_SIZES["button_height"],
            corner_radius=TOUCH_SIZES["border_radius"],
            command=self.on_done
        )
        self.finish_btn.pack(pady=(24, 40))
