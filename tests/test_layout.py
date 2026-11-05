"""Display-free tests for the justified gallery layout engine."""
from gui.layout import compute_justified_layout


def test_empty():
    lay = compute_justified_layout([], 800, 4)
    assert lay.tiles == [] and lay.total_height == 0


def test_rows_are_flush_and_keep_aspect():
    aspects = [1.5, 0.667, 1.5, 1.0, 1.78, 0.75, 1.5, 1.5, 0.667, 1.33] * 3
    width = 900
    lay = compute_justified_layout(aspects, width, 4, gap=4)
    assert len(lay.tiles) == len(aspects)
    for row in lay.rows[:-1]:
        tiles = lay.tiles[row.first:row.last]
        assert tiles[0].x == 0
        assert tiles[-1].x + tiles[-1].w == width          # justified edge
    for t in lay.tiles:
        ratio = t.w / t.h
        # tile ratio stays close to the photo ratio (no square cropping)
        assert abs(ratio - aspects[t.slot]) / aspects[t.slot] < 0.06 or t.w <= 3


def test_last_row_not_stretched():
    lay = compute_justified_layout([1.5, 1.5, 1.5, 1.5, 1.5], 800, 4)
    full_h = lay.rows[0].h
    assert lay.rows[-1].h <= full_h + 1


def test_density_changes_photos_per_row():
    aspects = [1.5] * 64
    per_row = {}
    for d in (4, 8, 16):
        lay = compute_justified_layout(aspects, 1000, d)
        per_row[d] = lay.rows[0].last - lay.rows[0].first
    assert per_row[4] < per_row[8] < per_row[16]


def test_hit_testing_and_visible_rows():
    lay = compute_justified_layout([1.5] * 20, 800, 4)
    t0 = lay.tiles[0]
    assert lay.tile_at(t0.x + 2, t0.y + 2) == 0
    assert lay.tile_at(-5, 3) == -1
    first, last = lay.rows_in_range(0, lay.rows[0].h)
    assert first == 0 and last >= 1
