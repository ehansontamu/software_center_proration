from __future__ import annotations

import json
import time
from decimal import Decimal
from email.message import Message
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BigCommerceError(RuntimeError):
    """Raised when the BigCommerce API cannot complete an operation."""


class BigCommerceClient:
    def __init__(
        self,
        store_hash: str,
        access_token: str,
        *,
        timeout: float = 30,
        max_attempts: int = 4,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = f"https://api.bigcommerce.com/stores/{store_hash}/v3/catalog"
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.opener = opener
        self.sleeper = sleeper
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Auth-Token": access_token,
        }

    def get_products_in_category(self, category_id: int) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urlencode(
                {
                    "categories:in": category_id,
                    "page": page,
                    "limit": 250,
                    "include_fields": "id,name,is_visible,brand_id,sku",
                }
            )
            response = self._request("GET", f"/products?{query}")
            products.extend(response.get("data", []))
            pagination = response.get("meta", {}).get("pagination", {})
            if page >= int(pagination.get("total_pages", 1)):
                return products
            page += 1

    def get_product_variants(self, product_id: int) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urlencode({"page": page, "limit": 250})
            response = self._request("GET", f"/products/{product_id}/variants?{query}")
            variants.extend(response.get("data", []))
            pagination = response.get("meta", {}).get("pagination", {})
            if page >= int(pagination.get("total_pages", 1)):
                return variants
            page += 1

    def update_variant_price(
        self, product_id: int, variant_id: int, price: Decimal
    ) -> None:
        self._request(
            "PUT",
            f"/products/{product_id}/variants/{variant_id}",
            {"price": float(price)},
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self.base_url}{path}", data=data, headers=self.headers, method=method
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = response.read()
                    return json.loads(payload) if payload else {}
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code < 600
                if retryable and attempt < self.max_attempts:
                    self.sleeper(self._retry_delay(error.headers, attempt))
                    continue
                detail = error.read().decode("utf-8", errors="replace")
                raise BigCommerceError(
                    f"BigCommerce {method} {path} failed with HTTP {error.code}: {detail}"
                ) from error
            except URLError as error:
                if attempt < self.max_attempts:
                    self.sleeper(float(2 ** (attempt - 1)))
                    continue
                raise BigCommerceError(
                    f"BigCommerce {method} {path} failed: {error.reason}"
                ) from error

        raise AssertionError("Retry loop exited unexpectedly")

    @staticmethod
    def _retry_delay(headers: Message | None, attempt: int) -> float:
        if headers is not None:
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), 60.0)
                except ValueError:
                    pass
        return float(2 ** (attempt - 1))
