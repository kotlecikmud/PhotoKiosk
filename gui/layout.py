"""Pure (Tk-free) justified-row layout engine for the photo gallery.

Photos keep their original aspect ratio. Each row is scaled so that it exactly
fills the available width; the last row keeps the target height instead of
being stretched. Because this module has no GUI dependencies it can be unit
tested without a display.
"""
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Sequence, Tuple

DEFAULT_ASPECT = 1.5  # used until the real image dimensions are known


@dataclass(frozen=True)
class Tile:
    """Pixel rectangle of one photo inside the scrollable canvas."""
    slot: int  # position in the list handed to compute_layout()
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Row:
    y: int
    h: int
    first: int  # index into Layout.tiles (inclusive)
    last: int   # index into Layout.tiles (exclusive)


@dataclass
class Layout:
    tiles: List[Tile]
    rows: List[Row]
    total_height: int
    width: int

    def rows_in_range(self, top: float, bottom: float) -> Tuple[int, int]:
        """Return (first_row, last_row_exclusive) intersecting [top, bottom]."""
        if not self.rows:
            return 0, 0
        ys = [r.y for r in self.rows]
        first = max(0, bisect_right(ys, top) - 1)
        # rows whose bottom edge is above `top` are skipped
        while first < len(self.rows) and self.rows[first].y + self.rows[first].h < top:
            first += 1
        last = bisect_right(ys, bottom)
        return first, max(first, last)

    def tile_at(self, x: float, y: float) -> int:
        """Return tile list index under (x, y) or -1."""
        if not self.rows or y < 0:
            return -1
        ys = [r.y for r in self.rows]
        ri = bisect_right(ys, y) - 1
        if ri < 0:
            return -1
        row = self.rows[ri]
        if y > row.y + row.h:
            return -1
        for ti in range(row.first, row.last):
            t = self.tiles[ti]
            if t.x <= x <= t.x + t.w:
                return ti
        return -1


def compute_justified_layout(
    aspects: Sequence[float],
    width: int,
    density: int,
    gap: int = 4,
) -> Layout:
    """Lay out photos in justified rows.

    ``density`` is the *target* number of photos per row (4 / 8 / 16 in the UI).
    The real count varies slightly with the aspect ratios so that portrait and
    landscape photos sit side by side without cropping or empty space.
    """
    width = max(int(width), 1)
    density = max(int(density), 1)
    n = len(aspects)
    if n == 0:
        return Layout([], [], 0, width)

    safe = [a if a and a > 0.05 else DEFAULT_ASPECT for a in aspects]
    mean_aspect = sum(safe) / n
    target_h = max(8.0, (width - gap * (density - 1)) / density / mean_aspect)

    def fit_height(first: int, last: int) -> float:
        total_a = sum(safe[first:last])
        return (width - gap * (last - first - 1)) / total_a

    # 1. Partition into rows (greedy, choosing the split closest to target_h)
    spans: List[Tuple[int, int]] = []
    start = 0
    i = 0
    while i < n:
        i += 1
        count = i - start
        natural = sum(safe[start:i]) * target_h + gap * (count - 1)
        if natural >= width:
            h_with = fit_height(start, i)
            if count > 1:
                h_without = fit_height(start, i - 1)
                if abs(h_without - target_h) < abs(h_with - target_h):
                    i -= 1
            spans.append((start, i))
            start = i
    if start < n:
        spans.append((start, n))

    # 2. Convert spans to pixel rectangles
    tiles: List[Tile] = []
    rows: List[Row] = []
    y = 0
    for ri, (a, b) in enumerate(spans):
        is_last = ri == len(spans) - 1
        h_fit = fit_height(a, b)
        if is_last and h_fit > target_h:
            row_h = int(round(target_h))
            justify = False
        else:
            row_h = int(round(h_fit))
            justify = True
        row_h = max(row_h, 4)

        first_tile = len(tiles)
        x = 0
        for slot in range(a, b):
            if justify and slot == b - 1:
                w = max(width - x, 2)  # absorb rounding error -> flush edge
            else:
                w = max(int(round(safe[slot] * row_h)), 2)
            tiles.append(Tile(slot, x, y, w, row_h))
            x += w + gap
        rows.append(Row(y, row_h, first_tile, len(tiles)))
        y += row_h + gap

    return Layout(tiles, rows, max(y - gap, 0), width)
