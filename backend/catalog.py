"""Print catalog (sizes, paper finishes, prices) shared by the kiosk API and order pricing."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session, sessionmaker

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
    prices: Dict[Tuple[str, str], float] = field(default_factory=dict)

    @property
    def size_names(self) -> List[str]:
        return [s.name for s in self.sizes]

    @classmethod
    def load(cls, db: Optional[Session] = None) -> "PrintCatalog":
        """Loads active sizes, active finishes and the full price map from the database."""
        owns_session = db is None
        if owns_session:
            db = sessionmaker(bind=get_db_engine())()
        try:
            sizes = [
                SizeSpec(s.name, s.width_mm, s.height_mm)
                for s in db.query(PrintSize).filter_by(is_active=True).order_by(PrintSize.sort_order).all()
            ]
            finishes = [
                f.name for f in db.query(PaperFinish).filter_by(is_active=True).order_by(PaperFinish.sort_order).all()
            ]
            prices = get_all_service_prices_map(db)
        finally:
            if owns_session:
                db.close()
        return cls(sizes=sizes, finish_names=finishes, prices=prices)

    def price(self, size_name: str, paper_type: str) -> float:
        return self.prices.get((size_name.strip().lower(), paper_type.strip().lower()), 0.0)

    def size(self, name: str) -> Optional[SizeSpec]:
        return next((s for s in self.sizes if s.name == name), None)

    def has_finish(self, name: str) -> bool:
        return name.strip().upper() in {f.upper() for f in self.finish_names}

    def to_dict(self) -> Dict[str, object]:
        """JSON-friendly form for the browser (price keys are ``"<size>|<finish>"`` lower-cased)."""
        return {
            "sizes": [{"name": s.name, "width_mm": s.width_mm, "height_mm": s.height_mm} for s in self.sizes],
            "finishes": list(self.finish_names),
            "prices": {f"{size}|{paper}": price for (size, paper), price in self.prices.items()},
        }
