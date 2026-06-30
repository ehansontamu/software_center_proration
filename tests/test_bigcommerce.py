from decimal import Decimal
import json
import unittest

from proration.bigcommerce import BigCommerceClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ClientTests(unittest.TestCase):
    def test_variant_price_update_sends_numeric_json(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"data": {}})

        client = BigCommerceClient("store", "secret", opener=opener)
        client.update_variant_price(7, 70, Decimal("110.00"))

        body = json.loads(requests[0].data)
        self.assertEqual(body, {"price": 110.0})
        self.assertTrue(requests[0].full_url.endswith("/products/7/variants/70"))
        self.assertNotIn(b"secret", requests[0].data)

    def test_category_products_are_paginated(self):
        requests = []
        pages = [
            {"data": [{"id": 1}], "meta": {"pagination": {"total_pages": 2}}},
            {"data": [{"id": 2}], "meta": {"pagination": {"total_pages": 2}}},
        ]

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(pages.pop(0))

        client = BigCommerceClient("store", "secret", opener=opener)
        products = client.get_products_in_category(42)

        self.assertEqual([product["id"] for product in products], [1, 2])
        self.assertIn("brand_id", requests[0].full_url)
        self.assertIn("sku", requests[0].full_url)

    def test_product_variants_are_paginated(self):
        pages = [
            {"data": [{"id": 10}], "meta": {"pagination": {"total_pages": 2}}},
            {"data": [{"id": 11}], "meta": {"pagination": {"total_pages": 2}}},
        ]

        def opener(request, timeout):
            return FakeResponse(pages.pop(0))

        client = BigCommerceClient("store", "secret", opener=opener)
        variants = client.get_product_variants(7)

        self.assertEqual([variant["id"] for variant in variants], [10, 11])


if __name__ == "__main__":
    unittest.main()
