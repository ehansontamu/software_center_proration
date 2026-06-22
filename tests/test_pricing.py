from decimal import Decimal
import unittest

from proration.pricing import build_variant_price_changes, reduced_price


class ReducedPriceTests(unittest.TestCase):
    def test_reduces_current_price_by_one_twelfth(self):
        self.assertEqual(
            reduced_price(Decimal("120.00"), Decimal(1) / Decimal(12), Decimal("0.01")),
            Decimal("110.00"),
        )

    def test_rounds_half_up_to_cents(self):
        self.assertEqual(
            reduced_price(Decimal("10.03"), Decimal(1) / Decimal(12), Decimal("0.01")),
            Decimal("9.19"),
        )

    def test_never_goes_below_minimum(self):
        self.assertEqual(
            reduced_price(Decimal("0.01"), Decimal(1) / Decimal(12), Decimal("0.01")),
            Decimal("0.01"),
        )

    def test_builds_change_from_api_variant(self):
        changes = build_variant_price_changes(
            [
                {
                    "id": 7,
                    "sku": "SOFTWARE-MY",
                    "price": 24,
                    "product_id": 3,
                    "product_name": "Test",
                    "is_visible": False,
                }
            ],
            Decimal(1) / Decimal(12),
            Decimal("0.01"),
        )
        self.assertEqual(changes[0].product_id, 3)
        self.assertEqual(changes[0].variant_id, 7)
        self.assertEqual(changes[0].new_price, Decimal("22.00"))


if __name__ == "__main__":
    unittest.main()
