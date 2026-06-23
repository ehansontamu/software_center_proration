from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from proration.bigcommerce import BigCommerceClient
from proration.pricing import VariantPriceChange, build_variant_price_changes


LOGGER = logging.getLogger("proration")


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


@dataclass(frozen=True)
class Config:
    store_hash: str
    access_token: str
    category_id: int
    allowed_category_id: int | None
    apply_changes: bool
    require_hidden: bool
    max_products: int
    max_variants: int
    sku_suffix: str
    reduction_fraction: Decimal
    minimum_price: Decimal
    report_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        required = ["BIGCOMMERCE_STORE_HASH", "BIGCOMMERCE_ACCESS_TOKEN", "BIGCOMMERCE_CATEGORY_ID"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        config = cls(
            store_hash=os.environ["BIGCOMMERCE_STORE_HASH"],
            access_token=os.environ["BIGCOMMERCE_ACCESS_TOKEN"],
            category_id=int(os.environ["BIGCOMMERCE_CATEGORY_ID"]),
            allowed_category_id=(
                int(os.environ["ALLOWED_CATEGORY_ID"])
                if os.environ.get("ALLOWED_CATEGORY_ID")
                else None
            ),
            apply_changes=parse_bool(os.environ.get("APPLY_CHANGES")),
            require_hidden=parse_bool(os.environ.get("REQUIRE_HIDDEN"), default=True),
            max_products=int(os.environ.get("MAX_PRODUCTS", "25")),
            max_variants=int(os.environ.get("MAX_VARIANTS", "50")),
            sku_suffix=os.environ.get("SKU_SUFFIX", "MY").strip(),
            reduction_fraction=Decimal(os.environ.get("REDUCTION_FRACTION", "0.08333333333333333333333333333")),
            minimum_price=Decimal(os.environ.get("MINIMUM_PRICE", "0.01")),
            report_path=Path(os.environ.get("REPORT_PATH", "proration-report.json")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.category_id <= 0:
            raise ValueError("BIGCOMMERCE_CATEGORY_ID must be positive")
        if (
            self.allowed_category_id is not None
            and self.category_id != self.allowed_category_id
        ):
            raise ValueError(
                f"BIGCOMMERCE_CATEGORY_ID must be {self.allowed_category_id}, got {self.category_id}"
            )
        if self.max_products <= 0:
            raise ValueError("MAX_PRODUCTS must be positive")
        if self.max_variants <= 0:
            raise ValueError("MAX_VARIANTS must be positive")
        if not self.sku_suffix:
            raise ValueError("SKU_SUFFIX cannot be empty")
        if not Decimal("0") < self.reduction_fraction < Decimal("1"):
            raise ValueError("REDUCTION_FRACTION must be greater than 0 and less than 1")
        if self.minimum_price < 0:
            raise ValueError("MINIMUM_PRICE cannot be negative")


def validate_scope(products: list[dict], config: Config) -> None:
    if not products:
        raise ValueError(f"No products found in category {config.category_id}")
    if len(products) > config.max_products:
        raise ValueError(
            f"Found {len(products)} products, exceeding MAX_PRODUCTS={config.max_products}"
        )
    visible = [product for product in products if product.get("is_visible")]
    if config.require_hidden and visible:
        ids = ", ".join(str(product["id"]) for product in visible)
        raise ValueError(f"Visible products found while REQUIRE_HIDDEN=true: {ids}")


def find_matching_variants(
    products: list[dict], config: Config, client: BigCommerceClient
) -> list[dict]:
    matches = []
    suffix = config.sku_suffix.casefold()
    for product in products:
        for variant in client.get_product_variants(int(product["id"])):
            sku = str(variant.get("sku") or "")
            if sku.casefold().endswith(suffix):
                matches.append(
                    {
                        **variant,
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "is_visible": product["is_visible"],
                    }
                )

    if not matches:
        raise ValueError(
            f"No variants with SKUs ending in {config.sku_suffix!r} were found"
        )
    if len(matches) > config.max_variants:
        raise ValueError(
            f"Found {len(matches)} matching variants, exceeding MAX_VARIANTS={config.max_variants}"
        )
    missing_prices = [variant for variant in matches if variant.get("price") is None]
    if missing_prices:
        labels = ", ".join(
            f"{variant['id']} ({variant.get('sku', '')})" for variant in missing_prices
        )
        raise ValueError(f"Matching variants do not have explicit variant prices: {labels}")
    return matches


def run(config: Config, client: BigCommerceClient) -> list[VariantPriceChange]:
    products = client.get_products_in_category(config.category_id)
    validate_scope(products, config)
    variants = find_matching_variants(products, config, client)
    changes = build_variant_price_changes(
        variants, config.reduction_fraction, config.minimum_price
    )

    for change in changes:
        LOGGER.info(
            "%s variant %s on product %s (%s), SKU %s: $%s -> $%s",
            "Updating" if config.apply_changes else "Would update",
            change.variant_id,
            change.product_id,
            change.product_name,
            change.sku,
            change.old_price,
            change.new_price,
        )
        if config.apply_changes and change.new_price != change.old_price:
            client.update_variant_price(
                change.product_id, change.variant_id, change.new_price
            )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if config.apply_changes else "dry-run",
        "category_id": config.category_id,
        "sku_suffix": config.sku_suffix,
        "reduction_fraction": str(config.reduction_fraction),
        "variant_count": len(changes),
        "changes": [change.as_dict() for change in changes],
    }
    config.report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return changes


def write_error_report(config: Config, error: Exception) -> None:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "error",
        "category_id": config.category_id,
        "sku_suffix": config.sku_suffix,
        "apply_changes": config.apply_changes,
        "error": str(error),
        "changes": [],
    }
    config.report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = None
    try:
        config = Config.from_env()
        client = BigCommerceClient(config.store_hash, config.access_token)
        changes = run(config, client)
    except (ValueError, RuntimeError) as error:
        LOGGER.error("%s", error)
        if config is not None:
            write_error_report(config, error)
        return 1

    LOGGER.info(
        "%s complete for %s product(s). Report: %s",
        "Apply" if config.apply_changes else "Dry-run",
        len(changes),
        config.report_path,
    )
    return 0
