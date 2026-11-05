"""UI-independent order state for the gallery (selection, sizes, quantities, prices).

All gallery business rules live here so they can be unit tested without Tk:
* adding a size adds it (count 0) to every photo, removing only affects one photo
* raising a count auto-selects the photo, lowering all counts to 0 deselects it
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import sessionmaker

from backend.database import (
    PaperFinish,
    PrintSize,
    get_all_service_prices_map,
    get_db_engine,
)


@dataclass(frozen=True)
class SizeSpec:
    name: str
    width_mm: float
    height_mm: float


@dataclass
class PrintCatalog:
    sizes: List[SizeSpec] = field(default_factory=list)
    finish_names: List[str] = field(default_factory=list)
    prices: Dict[tuple, float] = field(default_factory=dict)

    @property
    def size_names(self) -> List[str]:
        return [s.name for s in self.sizes]

    @classmethod
    def load(cls) -> "PrintCatalog":
        Session = sessionmaker(bind=get_db_engine())
        with Session() as db:
            sizes = [
                SizeSpec(s.name, s.width_mm, s.height_mm)
                for s in db.query(PrintSize).filter_by(is_active=True).order_by(PrintSize.sort_order).all()
            ]
            finishes = [
                f.name for f in db.query(PaperFinish).filter_by(is_active=True).order_by(PaperFinish.sort_order).all()
            ]
            prices = get_all_service_prices_map(db)
        return cls(sizes=sizes, finish_names=finishes, prices=prices)

    def price(self, size_name: str, paper_type: str) -> float:
        return self.prices.get((size_name.strip().lower(), paper_type.strip().lower()), 0.0)

    def size(self, name: str) -> Optional[SizeSpec]:
        return next((s for s in self.sizes if s.name == name), None)


class OrderDraft:
    """Mutable draft of the order being composed in the gallery."""

    DEFAULT_CROP = "fill_in"

    def __init__(
        self,
        catalog: PrintCatalog,
        photos: Optional[List[Dict[str, Any]]] = None,
        photo_configs: Optional[Dict[str, Dict[str, Any]]] = None,
        selected: Optional[Set[int]] = None,
        session_card_sizes: Optional[List[str]] = None,
    ):
        self.catalog = catalog
        self.photos: List[Dict[str, Any]] = photos or []
        self.photo_configs: Dict[str, Dict[str, Any]] = dict(photo_configs) if photo_configs else {}
        self.selected: Set[int] = set(selected) if selected else set()
        self.active_card: Dict[str, int] = {}
        self.default_finish = catalog.finish_names[0] if catalog.finish_names else "GLOSSY"
        names = catalog.size_names or ["9x13", "10x15"]
        self.session_card_sizes: List[str] = (
            list(session_card_sizes) if session_card_sizes else names[:2]
        )

    # ---- lookups ----------------------------------------------------
    def path(self, idx: int) -> str:
        return self.photos[idx]["filepath"]

    def ensure(self, filepath: str) -> Dict[str, Any]:
        cfg = self.photo_configs.get(filepath)
        if cfg is None:
            cfg = {
                "crop_mode": self.DEFAULT_CROP,
                "cards": [
                    {"size_name": s, "paper_type": self.default_finish, "count": 0}
                    for s in self.session_card_sizes
                ],
            }
            self.photo_configs[filepath] = cfg
        self.active_card.setdefault(filepath, 0)
        return cfg

    def cards(self, idx: int) -> List[Dict[str, Any]]:
        return self.ensure(self.path(idx))["cards"]

    def active_card_idx(self, filepath: str) -> int:
        cards = self.photo_configs.get(filepath, {}).get("cards", [])
        if not cards:
            return 0
        idx = self.active_card.get(filepath, 0)
        if not 0 <= idx < len(cards):
            idx = 0
            self.active_card[filepath] = 0
        return idx

    def set_active_card(self, filepath: str, idx: int) -> None:
        cards = self.photo_configs.get(filepath, {}).get("cards", [])
        self.active_card[filepath] = max(0, min(idx, len(cards) - 1)) if cards else 0

    def prints_for(self, idx: int) -> int:
        cfg = self.photo_configs.get(self.path(idx), {})
        return sum(c.get("count", 0) for c in cfg.get("cards", []))

    def total_prints(self) -> int:
        return sum(self.prints_for(i) for i in self.selected if i < len(self.photos))

    # ---- selection --------------------------------------------------
    def select(self, idx: int) -> None:
        self.selected.add(idx)
        cards = self.cards(idx)
        if cards and sum(c["count"] for c in cards) == 0:
            cards[0]["count"] = 1

    def deselect(self, idx: int) -> None:
        self.selected.discard(idx)
        for c in self.photo_configs.get(self.path(idx), {}).get("cards", []):
            c["count"] = 0

    def select_all(self) -> None:
        for i in range(len(self.photos)):
            self.select(i)

    def deselect_all(self) -> None:
        for i in range(len(self.photos)):
            self.deselect(i)

    # ---- card operations -------------------------------------------
    def _auto_deselect_if_empty(self, idx: int) -> None:
        if sum(c["count"] for c in self.cards(idx)) == 0:
            self.selected.discard(idx)

    def increment(self, idx: int, card_idx: int) -> bool:
        cards = self.cards(idx)
        if not 0 <= card_idx < len(cards):
            return False
        cards[card_idx]["count"] += 1
        self.set_active_card(self.path(idx), card_idx)
        self.selected.add(idx)
        return True

    def decrement(self, idx: int, card_idx: int) -> bool:
        cards = self.cards(idx)
        if not 0 <= card_idx < len(cards):
            return False
        cards[card_idx]["count"] = max(0, cards[card_idx]["count"] - 1)
        self.set_active_card(self.path(idx), card_idx)
        self._auto_deselect_if_empty(idx)
        return True

    def set_paper(self, idx: int, card_idx: int, paper_type: str) -> bool:
        cards = self.cards(idx)
        if not 0 <= card_idx < len(cards):
            return False
        cards[card_idx]["paper_type"] = paper_type
        self.set_active_card(self.path(idx), card_idx)
        return True

    def remove_card(self, idx: int, card_idx: int) -> bool:
        fp = self.path(idx)
        cards = self.cards(idx)
        if not 0 <= card_idx < len(cards):
            return False
        active = self.active_card_idx(fp)
        cards.pop(card_idx)
        if not cards:
            self.set_active_card(fp, 0)
        elif active >= len(cards):
            self.set_active_card(fp, len(cards) - 1)
        elif active > card_idx:
            self.set_active_card(fp, active - 1)
        self._auto_deselect_if_empty(idx)
        return True

    def add_size_everywhere(self, size_name: str) -> None:
        """Add a print format to every photo with count 0 (skips photos that already have it)."""
        if size_name not in self.session_card_sizes:
            self.session_card_sizes.append(size_name)
        for p in self.photos:
            cards = self.ensure(p["filepath"])["cards"]
            if not any(c["size_name"] == size_name for c in cards):
                cards.append({"size_name": size_name, "paper_type": self.default_finish, "count": 0})

    def available_sizes_for(self, idx: int) -> List[str]:
        have = {c["size_name"] for c in self.cards(idx)}
        names = self.catalog.size_names
        return [s for s in names if s not in have] or list(names)

    # ---- output -----------------------------------------------------
    def build_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for idx in sorted(self.selected):
            photo = self.photos[idx]
            cfg = self.photo_configs[photo["filepath"]]
            for card in cfg["cards"]:
                qty = card["count"]
                if qty > 0:
                    unit = self.catalog.price(card["size_name"], card["paper_type"])
                    items.append({
                        "source_filepath": photo["filepath"],
                        "filename": photo["filename"],
                        "print_size_name": card["size_name"],
                        "paper_finish": card["paper_type"].upper(),
                        "crop_mode": cfg["crop_mode"],
                        "quantity": qty,
                        "unit_price": unit,
                        "subtotal": unit * qty,
                    })
        return items
