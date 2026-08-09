import unittest

from stock_auto_allocation.stock_auto_allocation.allocation_engine import (
    build_target_matrix,
    choose_selected_stores,
)


class TestAutomaticRangeAllocation(unittest.TestCase):
    def test_minimum_two_reduces_store_count(self):
        selected = choose_selected_stores(
            ["A", "B", "C", "D"],
            {"S": 10, "M": 8, "L": 6},
            2,
        )
        self.assertEqual(selected, ["A", "B", "C"])

    def test_minimum_three_uses_scarcest_variant(self):
        selected = choose_selected_stores(
            ["A", "B", "C"],
            {"S": 20, "M": 8, "L": 11},
            3,
        )
        self.assertEqual(selected, ["A", "B"])

    def test_target_starts_with_requested_minimum(self):
        target = build_target_matrix(
            variants=["S", "M"],
            selected_stores=["A", "B"],
            all_stores=["A", "B"],
            velocity={},
            coverage_days=14,
            available_by_variant={"S": 4, "M": 4},
            minimum_per_variant=2,
        )
        self.assertEqual(target[("A", "S")], 2)
        self.assertEqual(target[("B", "M")], 2)


if __name__ == "__main__":
    unittest.main()
