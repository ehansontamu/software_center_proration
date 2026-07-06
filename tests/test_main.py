from decimal import Decimal
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from proration.main import (
    Config,
    collect_variants,
    find_matching_variants,
    run,
    validate_update_scope,
    write_error_report,
)


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


def make_config(
    report_path,
    *,
    apply_changes=False,
    require_hidden=True,
    max_products=25,
    run_date=date(2026, 7, 6),
    schedule_mode="daily_test",
):
    return Config(
        store_hash="store",
        access_token="token",
        category_id=42,
        allowed_category_id=None,
        apply_changes=apply_changes,
        require_hidden=require_hidden,
        max_products=max_products,
        max_variants=50,
        brand_ids=(40, 39),
        sku_suffix="-MY",
        periods=12,
        minimum_price=Decimal("0.01"),
        schedule_mode=schedule_mode,
        run_date=run_date,
        state_path=report_path.parent / "state.json",
        report_path=report_path,
    )


class RunTests(unittest.TestCase):
    def test_dry_run_writes_report_but_does_not_update(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 40, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 115}]}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            client = FakeClient(products, variants)
            run(make_config(report_path), client)

            self.assertEqual(client.updates, [])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "dry-run")
            self.assertEqual(report["brand_product_count"], 1)
            self.assertEqual(report["changes"][0]["proration_event"], 1)
            self.assertEqual(report["changes"][0]["old_price"], "115.00")
            self.assertEqual(report["changes"][0]["base_price"], "120.00")
            self.assertEqual(report["changes"][0]["new_price"], "110.00")
            self.assertEqual(report["changes"][0]["sku"], "TEST-MY")

    def test_apply_updates_price(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 40, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 115}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            run(make_config(Path(directory) / "report.json", apply_changes=True), client)
            self.assertEqual(client.updates, [(1, 10, Decimal("110.00"))])

    def test_only_matching_variant_skus_are_selected_case_insensitively(self):
        products = [{"id": 1, "name": "Hidden", "sku": "PRODUCT", "price": 120, "is_visible": False}]
        variants = {
            1: [
                {"id": 10, "sku": "TEST-my", "price": 120},
                {"id": 11, "sku": "TEST-OTHER", "price": 120},
            ]
        }
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            inspected = collect_variants(products, client)
            matches = find_matching_variants(inspected, make_config(Path(directory) / "report.json"))
            self.assertEqual([variant["id"] for variant in matches], [10])

    def test_parent_product_sku_does_not_select_variant_price(self):
        products = [
            {
                "id": 1,
                "name": "Hidden",
                "sku": "PRODUCT-MY",
                "brand_id": 40,
                "price": 120,
                "is_visible": False,
            }
        ]
        variants = {1: [{"id": 10, "sku": "DEFAULT", "price": 120}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            changes = run(make_config(Path(directory) / "report.json", apply_changes=True), client)

            self.assertEqual(changes, [])
            self.assertEqual(client.updates, [])

    def test_configured_proration_brands_are_inspected(self):
        products = [
            {"id": 1, "name": "Match", "brand_id": 40, "price": 120, "is_visible": False},
            {"id": 2, "name": "Skip", "brand_id": 39, "price": 120, "is_visible": False},
        ]
        variants = {
            1: [{"id": 10, "sku": "MATCH-MY", "price": 120}],
            2: [{"id": 11, "sku": "SKIP-MY", "price": 120}],
        }
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            changes = run(make_config(Path(directory) / "report.json"), client)

            self.assertEqual([change.sku for change in changes], ["MATCH-MY", "SKIP-MY"])

    def test_monthly_mode_maps_october_to_first_event(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 40, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            client = FakeClient(products, variants)
            changes = run(
                make_config(
                    report_path,
                    schedule_mode="monthly",
                    run_date=date(2026, 10, 1),
                ),
                client,
            )

            self.assertEqual(changes[0].proration_event, 1)
            self.assertEqual(changes[0].new_price, Decimal("110.00"))

    def test_monthly_six_month_brand_stops_after_march(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 39, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            client = FakeClient(products, variants)
            changes = run(
                make_config(
                    report_path,
                    schedule_mode="monthly",
                    run_date=date(2027, 4, 1),
                ),
                client,
            )

            self.assertEqual(changes, [])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["skipped_variants"][0]["reason"],
                "brand_event_limit_reached",
            )

    def test_no_matching_variant_skus_writes_empty_report(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 40, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 11, "sku": "TEST-OTHER", "price": 120}]}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            client = FakeClient(products, variants)
            changes = run(make_config(report_path, apply_changes=True), client)

            self.assertEqual(changes, [])
            self.assertEqual(client.updates, [])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "apply")
            self.assertEqual(report["variant_count"], 0)
            self.assertEqual(report["brand_ids"], [40, 39])
            self.assertEqual(report["inspected_variant_count"], 1)
            self.assertEqual(report["inspected_variants"][0]["product_sku"], "")
            self.assertEqual(report["inspected_variants"][0]["sku"], "TEST-OTHER")
            self.assertEqual(report["changes"], [])

    def test_daily_state_skips_same_day_rerun_and_advances_next_day(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 40, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            first_report = Path(directory) / "first.json"
            client = FakeClient(products, variants)
            first_changes = run(make_config(first_report, apply_changes=True), client)

            self.assertEqual(first_changes[0].proration_event, 1)
            self.assertEqual(first_changes[0].new_price, Decimal("110.00"))

            same_day_report = Path(directory) / "same-day.json"
            same_day_client = FakeClient(products, variants)
            same_day_changes = run(
                make_config(same_day_report, apply_changes=True),
                same_day_client,
            )
            self.assertEqual(same_day_changes, [])
            self.assertEqual(same_day_client.updates, [])

            next_day_report = Path(directory) / "next-day.json"
            next_day_client = FakeClient(products, variants)
            next_day_changes = run(
                make_config(
                    next_day_report,
                    apply_changes=True,
                    run_date=date(2026, 7, 7),
                ),
                next_day_client,
            )
            self.assertEqual(next_day_changes[0].proration_event, 2)
            self.assertEqual(next_day_changes[0].new_price, Decimal("100.00"))

    def test_six_month_brand_stops_after_six_daily_events(self):
        products = [{"id": 1, "name": "Hidden", "brand_id": 39, "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            changes = []
            for index in range(7):
                report_path = Path(directory) / f"report-{index}.json"
                client = FakeClient(products, variants)
                changes = run(
                    make_config(
                        report_path,
                        apply_changes=True,
                        run_date=date(2026, 7, 6 + index),
                    ),
                    client,
                )

            self.assertEqual(changes, [])
            state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["daily-test"]["variants"]["10"]["last_event"], 6)

    def test_matching_variant_without_explicit_price_fails(self):
        products = [{"id": 1, "name": "Hidden", "price": 120, "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": None}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            with self.assertRaisesRegex(ValueError, "explicit variant prices"):
                inspected = collect_variants(products, client)
                find_matching_variants(inspected, make_config(Path(directory) / "report.json"))

    def test_matching_product_without_default_price_fails(self):
        products = [{"id": 1, "name": "Hidden", "is_visible": False}]
        variants = {1: [{"id": 10, "sku": "TEST-MY", "price": 120}]}
        with TemporaryDirectory() as directory:
            client = FakeClient(products, variants)
            with self.assertRaisesRegex(ValueError, "default prices"):
                inspected = collect_variants(products, client)
                find_matching_variants(inspected, make_config(Path(directory) / "report.json"))

    def test_visible_product_fails_preflight(self):
        products = [{"id": 1, "name": "Visible", "price": 120, "is_visible": True}]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Visible products"):
                validate_update_scope(products, make_config(Path(directory) / "report.json"))

    def test_too_many_products_fails_preflight(self):
        products = [
            {"id": 1, "name": "One", "price": 1, "is_visible": False},
            {"id": 2, "name": "Two", "price": 1, "is_visible": False},
        ]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exceeding MAX_PRODUCTS"):
                validate_update_scope(
                    products,
                    make_config(Path(directory) / "report.json", max_products=1),
                )

    def test_allowed_category_id_mismatch_fails_validation(self):
        with TemporaryDirectory() as directory:
            config = make_config(Path(directory) / "report.json")
            config = Config(
                store_hash=config.store_hash,
                access_token=config.access_token,
                category_id=43,
                allowed_category_id=44,
                apply_changes=config.apply_changes,
                require_hidden=config.require_hidden,
                max_products=config.max_products,
                max_variants=config.max_variants,
                brand_ids=config.brand_ids,
                sku_suffix=config.sku_suffix,
                periods=config.periods,
                minimum_price=config.minimum_price,
                schedule_mode=config.schedule_mode,
                run_date=config.run_date,
                state_path=config.state_path,
                report_path=config.report_path,
            )
            with self.assertRaisesRegex(ValueError, "must be 44"):
                config.validate()

    def test_error_report_is_written_for_preflight_failure(self):
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            config = make_config(report_path, apply_changes=True)
            write_error_report(config, ValueError("Visible products found"))

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "error")
            self.assertEqual(report["category_id"], 42)
            self.assertEqual(report["brand_ids"], [40, 39])
            self.assertTrue(report["apply_changes"])
            self.assertEqual(report["error"], "Visible products found")
            self.assertEqual(report["changes"], [])


if __name__ == "__main__":
    unittest.main()
