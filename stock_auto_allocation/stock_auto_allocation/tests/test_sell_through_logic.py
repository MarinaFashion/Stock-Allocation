import unittest

from stock_auto_allocation.stock_auto_allocation.sell_through_logic import (
    days_cover,
    donor_is_commercially_valid,
    protected_qty,
    required_qty,
)


class TestSellThroughLogic(unittest.TestCase):
    def test_target_requirement_uses_real_sales_depth(self):
        # 12 sold in 14 days with 7 days coverage = 6 pieces required.
        self.assertEqual(required_qty(12, 14, 7, 1), 6)

    def test_source_zero_sales_keeps_only_minimum(self):
        self.assertEqual(protected_qty(0, 14, 3, 1), 1)

    def test_source_sales_are_protected(self):
        # 8 sold in 14 days, 3 protection days => ceil(1.71) = 2.
        self.assertEqual(protected_qty(8, 14, 3, 1), 2)

    def test_zero_sales_source_can_donate_to_selling_target(self):
        self.assertTrue(
            donor_is_commercially_valid(
                source_variant_sales=0,
                target_variant_sales=10,
                source_style_sales=0,
                target_style_sales=20,
                source_stock=5,
                lookback_days=14,
                target_coverage_days=7,
                range_phase=False,
            )
        )

    def test_stronger_source_is_protected_when_cover_is_not_excessive(self):
        self.assertFalse(
            donor_is_commercially_valid(
                source_variant_sales=10,
                target_variant_sales=5,
                source_style_sales=20,
                target_style_sales=10,
                source_stock=5,
                lookback_days=14,
                target_coverage_days=7,
                range_phase=False,
            )
        )

    def test_excessive_cover_can_make_stronger_source_a_donor(self):
        self.assertTrue(
            donor_is_commercially_valid(
                source_variant_sales=10,
                target_variant_sales=5,
                source_style_sales=20,
                target_style_sales=10,
                source_stock=20,
                lookback_days=14,
                target_coverage_days=7,
                range_phase=False,
            )
        )

    def test_days_cover(self):
        self.assertAlmostEqual(days_cover(4, 8, 14), 7.0)


if __name__ == "__main__":
    unittest.main()
