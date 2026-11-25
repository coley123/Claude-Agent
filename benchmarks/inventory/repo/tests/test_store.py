import pytest

from inventory.store import Inventory, OutOfStock


def test_add_accumulates():
    inv = Inventory()
    inv.add("a", 2)
    inv.add("a", 3)
    assert inv.quantity("a") == 5


def test_unknown_sku_is_zero():
    assert Inventory().quantity("nope") == 0


def test_remove_too_many_raises_and_keeps_stock():
    inv = Inventory()
    inv.add("a", 1)
    with pytest.raises(OutOfStock):
        inv.remove("a", 2)
    assert inv.quantity("a") == 1


def test_restock_report_lists_low_items_sorted():
    inv = Inventory()
    inv.add("b", 1)
    inv.add("a", 0)
    inv.add("c", 10)
    assert inv.restock_report(threshold=2) == ["a", "b"]
