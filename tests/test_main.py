from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from proration.main import Config, find_matching_variants, run, validate_scope


class FakeClient:
    def __init__(self, products, variants=None):
        self.products = products
        self.variants = variants or {}
        self.updates = []

    def get_products_in_category(self, category_id):
        return self.products

    def get_product_variants(self, product_id):
        return self.variants.get(product_id, [])

    def update_variant_price(self, product_id, variant_id, price):
        self.updates.append((product_id, variant_id, price))


def make_config(report_path, *, apply_changes=False, require_hidden=True, max_products=25):
    return Config(
        store_hash="store",
        access_token="token",
        category_id=42,
        apply_changes=apply_changes,
        require_hidden=require_hidden,
        max_products=max_products,
        max_variants=50,
        sku_suffix="MY",
        reduction_fraction=Decimal(1) / Decimal(12),
        minimum_price=Decimal("0.01"),
        report_path=report_path,
    )


class RunTests(unittest.TestCase):
    def test_dry_run_writes_report_but_does_not_update(self):
        products = [{"id": 1, "name": "Hidden", "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            client = FakeClient(products, variants)
            run(make_config(report_path), client)

            self.assertEqual(client.updates, [])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "dry-run")
            self.assertEqual(report["changes"][0]["new_price"], "110.00")
            self.assertEqual(report["changes"][0]["sku"], "TEST-MY")

    def test_apply_updates_price(self):
        products = [{"id": 1, "name": "Hidden", "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            run(make_config(Path(directory) / "report.json", apply_changes=True), client)
            self.assertEqual(client.updates, [(1, 10, Decimal("110.00"))])

    def test_only_matching_variant_skus_are_selected_case_insensitively(self):
        products = [{"id": 1, "name": "Hidden", "is_visible": False}]
        variants = {
            1: [
                {"id": 10, "sku": "TEST-my", "price": 120},
                {"id": 11, "sku": "TEST-OTHER", "price": 120},
            ]
        }
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            matches = find_matching_variants(
                products, make_config(Path(directory) / "report.json"), client
            )
            self.assertEqual([variant["id"] for variant in matches], [10])

    def test_matching_variant_without_explicit_price_fails(self):
        products = [{"id": 1, "name": "Hidden", "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": None}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            with self.assertRaisesRegex(ValueError, "explicit variant prices"):
                find_matching_variants(
                    products, make_config(Path(directory) / "report.json"), client
                )

    def test_visible_product_fails_preflight(self):
        products = [{"id": 1, "name": "Visible", "price": 120, "is_visible": True}]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Visible products"):
                validate_scope(products, make_config(Path(directory) / "report.json"))

    def test_too_many_products_fails_preflight(self):
        products = [
            {"id": 1, "name": "One", "price": 1, "is_visible": False},
            {"id": 2, "name": "Two", "price": 1, "is_visible": False},
        ]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exceeding MAX_PRODUCTS"):
                validate_scope(
                    products,
                    make_config(Path(directory) / "report.json", max_products=1),
                )


if __name__ == "__main__":
    unittest.main()
