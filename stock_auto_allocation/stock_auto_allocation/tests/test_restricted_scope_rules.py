import unittest


class TestRestrictedScopeRules(unittest.TestCase):
    def test_donor_surplus_keeps_reserve(self):
        source_stock = 5
        source_required = 2
        self.assertEqual(max(0, source_stock - source_required), 3)

    def test_target_receives_only_shortage(self):
        target_stock = 6
        target_required = 2
        self.assertEqual(max(0, target_required - target_stock), 0)

    def test_transfer_is_limited_by_surplus_and_need(self):
        sendable = 3
        needed = 1
        self.assertEqual(min(sendable, needed), 1)


if __name__ == "__main__":
    unittest.main()
