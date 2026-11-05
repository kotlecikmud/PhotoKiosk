import os, time, pytest
from PIL import Image
import customtkinter as ctk
from backend.database import init_database
from gui.screens.gallery import GalleryScreen

def pump(root, secs=1.0):
    end = time.time()+secs
    while time.time() < end:
        root.update(); time.sleep(0.01)

@pytest.fixture
def env(tmp_path):
    d = tmp_path/"ph"; d.mkdir()
    sizes = [(600,400),(400,600),(800,800),(1200,675),(500,700),(640,480)]*4
    for i,(w,h) in enumerate(sizes):
        Image.new("RGB",(w,h),(i*9%255,100,150)).save(d/f"img{i:02d}.jpg","JPEG")
    init_database(str(tmp_path/"k.db"), initial_admin_password="x")
    root = ctk.CTk(); root.geometry("1100x760")
    got = {}
    g = GalleryScreen(root, "Src", str(d), session_id=7, on_proceed_to_summary=lambda items: got.setdefault("items", items))
    g.pack(fill="both", expand=True)
    yield root, g, got
    root.destroy()

def test_grid_aspect_and_no_rebuild_on_state_change(env):
    root, g, got = env
    pump(root, 2.0)
    grid = g.photo_grid
    assert len(g.photos) == 24
    # aspects probed -> portrait and landscape tiles exist, none forced square
    ratios = [t.w/t.h for t in grid._layout.tiles]
    assert any(r < 0.9 for r in ratios) and any(r > 1.4 for r in ratios)
    # thumbnails attached
    live = grid._live
    assert live and all(v["photo"] is not None for v in live.values())
    # state change keeps the very same canvas items (no destroy/rebuild)
    ids_before = {s: dict((k, v) for k, v in d.items() if k in ("ph","img","badge")) for s, d in live.items()}
    g._increment_card_count(0)
    g._increment_card_count(0)
    g._select_photo_for_editing(3)
    g._increment_card_count(1)
    pump(root, 0.3)
    ids_after = {s: dict((k, v) for k, v in d.items() if k in ("ph","img","badge")) for s, d in grid._live.items()}
    assert ids_before == ids_after
    assert grid.canvas.itemcget(grid._live[0]["check"], "state") == "normal"
    assert grid.canvas.itemcget(grid._live[0]["chip"], "text") == "×2"

def test_editor_widgets_persist_across_count_changes(env):
    root, g, got = env
    pump(root, 1.0)
    row = g.cards_row
    before = list(row._row_pool)
    first_child_ids = [id(w) for w in before]
    for _ in range(5):
        g._increment_card_count(0)
    g._decrement_card_count(0)
    assert [id(w) for w in row._row_pool] == first_child_ids   # same widget objects
    assert row._row_pool[0]._diff_widgets["count"].cget("text").endswith("4")
    # preview not re-requested by quantity changes: shown request unchanged
    shown = g._shown_preview
    g._increment_card_count(0)
    pump(root, 0.3)
    assert g._shown_preview == shown

def test_density_and_filter_and_proceed(env):
    root, g, got = env
    pump(root, 1.5)
    n4 = g.photo_grid._layout.rows[0].last - g.photo_grid._layout.rows[0].first
    g._set_density(16); pump(root, 0.5)
    n16 = g.photo_grid._layout.rows[0].last - g.photo_grid._layout.rows[0].first
    assert n16 > n4
    assert g.density_var.get() == 16
    g._increment_card_count(0); g._increment_card_count(0)
    g._proceed_to_summary()
    assert got["items"][0]["quantity"] == 2
    g._toggle_select_all(); g._toggle_select_all()
    assert len(g.selected_indices) in (0, 24)

def test_language_switch_in_place(env):
    root, g, got = env
    pump(root, 1.0)
    pool = list(g.cards_row._row_pool)
    from backend.i18n import get_i18n
    get_i18n().set_language("pl"); pump(root, 0.2)
    assert list(g.cards_row._row_pool) == pool
