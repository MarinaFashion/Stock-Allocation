import unittest

from stock_auto_allocation.stock_auto_allocation.allocation_engine import (
    build_target_matrix,
    choose_selected_stores,
    deficits_and_surpluses,
)


class TestAllocationEngine(unittest.TestCase):
    def test_scarcest_variant_limits_spreading(self):
        selected = choose_selected_stores(
            "Spreading",
            ["A", "B", "C", "D"],
            {"S": 10, "M": 8, "L": 2},
        )
        self.assertEqual(selected, ["A", "B"])

    def test_grouping_uses_fewer_stores(self):
        selected = choose_selected_stores(
            "Grouping",
            ["A", "B", "C", "D"],
            {"S": 10, "M": 8, "L": 6},
        )
        self.assertEqual(selected, ["A", "B", "C"])

    def test_target_never_exceeds_variant_budget(self):
        target = build_target_matrix(
            mode="Spreading",
            variants=["S", "M", "L"],
            selected_stores=["A", "B"],
            ranked_stores=["A", "B"],
            velocity={},
            coverage_days=14,
            available_by_variant={"S": 10, "M": 4, "L": 2},
        )
        for variant, budget in {"S": 10, "M": 4, "L": 2}.items():
            self.assertLessEqual(
                sum(target[(store, variant)] for store in ["A", "B"]),
                budget,
            )

    def test_destination_and_source_stock_are_separate(self):
        deficits, surplus = deficits_and_surpluses(
            ["L"],
            ["A", "B"],
            {("A", "L"): 0, ("B", "L"): 1},
            {("A", "L"): 0, ("B", "L"): 3},
            {("A", "L"): 1, ("B", "L"): 1},
        )
        self.assertEqual(deficits, [("A", "L", 1)])
        self.assertEqual(surplus[("B", "L")], 2)


if __name__ == "__main__":
    unittest.main()
