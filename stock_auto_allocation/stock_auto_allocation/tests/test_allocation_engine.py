import unittest

from stock_auto_allocation.allocation_engine import (
    StoreMetric,
    build_target_matrix,
    choose_selected_stores,
    deficits_and_surpluses,
    rank_stores,
)


class TestAllocationEngine(unittest.TestCase):
    def test_rank_stores_uses_sales_first(self):
        ranked = rank_stores(
            [
                StoreMetric("B", 2, 10, 4),
                StoreMetric("A", 8, 1, 1),
            ]
        )
        self.assertEqual(ranked, ["A", "B"])

    def test_grouping_selects_fewer_stores(self):
        stores = ["A", "B", "C", "D"]
        spreading = choose_selected_stores("Spreading", stores, 16, 4)
        grouping = choose_selected_stores("Grouping", stores, 16, 4)
        self.assertEqual(len(spreading), 4)
        self.assertEqual(len(grouping), 2)

    def test_complete_ranges_are_created_first(self):
        variants = ["S", "M", "L"]
        target = build_target_matrix(
            "Spreading",
            variants,
            ["A", "B"],
            ["A", "B"],
            {},
            {},
            14,
            6,
        )
        for store in ["A", "B"]:
            for variant in variants:
                self.assertEqual(target[(store, variant)], 1)

    def test_surplus_never_breaks_target(self):
        variants = ["S", "M"]
        stores = ["A", "B"]
        current = {("A", "S"): 3, ("A", "M"): 1, ("B", "S"): 0, ("B", "M"): 1}
        target = {("A", "S"): 1, ("A", "M"): 1, ("B", "S"): 1, ("B", "M"): 1}
        deficits, surplus = deficits_and_surpluses(variants, stores, current, target)
        self.assertIn(("B", "S", 1), deficits)
        self.assertEqual(surplus[("A", "S")], 2)


if __name__ == "__main__":
    unittest.main()
