"""Display-free tests for the gallery's order-draft business rules."""
import pytest

from gui.services.order_draft import OrderDraft, PrintCatalog, SizeSpec


@pytest.fixture
def draft():
    catalog = PrintCatalog(
        sizes=[SizeSpec("9x13", 89, 127), SizeSpec("10x15", 102, 152), SizeSpec("15x21", 152, 210)],
        finish_names=["GLOSSY", "MATTE"],
        prices={("9x13", "glossy"): 1.0, ("10x15", "glossy"): 1.1},
    )
    photos = [
        {"filename": "a.jpg", "filepath": "/p/a.jpg"},
        {"filename": "b.jpg", "filepath": "/p/b.jpg"},
    ]
    return OrderDraft(catalog, photos=photos)


def test_increment_autoselects_and_decrement_autodeselects(draft):
    draft.ensure("/p/a.jpg")
    assert draft.increment(0, 0)
    assert 0 in draft.selected and draft.prints_for(0) == 1
    draft.decrement(0, 0)
    assert 0 not in draft.selected


def test_select_sets_first_card_to_one(draft):
    draft.select(1)
    assert draft.cards(1)[0]["count"] == 1 and draft.total_prints() == 1


def test_add_size_everywhere_adds_zero_count(draft):
    draft.ensure("/p/a.jpg"); draft.ensure("/p/b.jpg")
    draft.add_size_everywhere("15x21")
    for i in (0, 1):
        assert any(c["size_name"] == "15x21" and c["count"] == 0 for c in draft.cards(i))


def test_remove_card_only_affects_one_photo_and_fixes_active(draft):
    draft.ensure("/p/a.jpg"); draft.ensure("/p/b.jpg")
    draft.add_size_everywhere("15x21")
    draft.set_active_card("/p/a.jpg", 1)
    draft.remove_card(0, 0)
    assert draft.active_card_idx("/p/a.jpg") == 0
    assert len(draft.cards(0)) == 2 and len(draft.cards(1)) == 3


def test_remove_all_cards_then_empty_is_safe(draft):
    for _ in range(2):
        draft.remove_card(0, 0)
    assert draft.cards(0) == []
    assert draft.active_card_idx("/p/a.jpg") == 0


def test_build_items_prices(draft):
    draft.increment(0, 0); draft.increment(0, 0); draft.increment(1, 1)
    items = draft.build_items()
    assert [i["quantity"] for i in items] == [2, 1]
    assert items[0]["unit_price"] == 1.0 and items[0]["subtotal"] == 2.0
    assert items[0]["paper_finish"] == "GLOSSY"
