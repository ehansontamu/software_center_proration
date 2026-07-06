from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from proration.bigcommerce import BigCommerceClient
from proration.pricing import VariantPriceChange, build_variant_price_changes


LOGGER = logging.getLogger("proration")
BRAND_EVENT_LIMITS = {40: 11, 39: 6}
DEFAULT_STATE_PATH = Path("state/proration-state.json")


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def parse_int_list(value: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def parse_run_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(UTC).date()


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
    brand_ids: tuple[int, ...]
    sku_suffix: str
    periods: int
    minimum_price: Decimal
    schedule_mode: str
    run_date: date
    state_path: Path
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
            max_products=int(os.environ.get("MAX_PRODUCTS", "50")),
            max_variants=int(os.environ.get("MAX_VARIANTS", "50")),
            brand_ids=parse_int_list(os.environ.get("BRAND_IDS", "40,39")),
            sku_suffix=os.environ.get("SKU_SUFFIX", "-MY").strip(),
            periods=int(os.environ.get("PRORATION_PERIODS", "12")),
            minimum_price=Decimal(os.environ.get("MINIMUM_PRICE", "0.01")),
            schedule_mode=os.environ.get("PRORATION_MODE", "daily_test").strip(),
            run_date=parse_run_date(os.environ.get("RUN_DATE")),
            state_path=Path(os.environ.get("STATE_PATH", str(DEFAULT_STATE_PATH))),
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
        if not self.brand_ids:
            raise ValueError("BRAND_IDS cannot be empty")
        if any(brand_id <= 0 for brand_id in self.brand_ids):
            raise ValueError("BRAND_IDS must be positive")
        if not self.sku_suffix:
            raise ValueError("SKU_SUFFIX cannot be empty")
        if self.periods <= 0:
            raise ValueError("PRORATION_PERIODS must be positive")
        if self.minimum_price < 0:
            raise ValueError("MINIMUM_PRICE cannot be negative")
        if self.schedule_mode not in {"daily_test", "monthly"}:
            raise ValueError("PRORATION_MODE must be 'daily_test' or 'monthly'")


def validate_category_scope(products: list[dict], config: Config) -> None:
    if not products:
        raise ValueError(f"No products found in category {config.category_id}")


def validate_update_scope(products: list[dict], config: Config) -> None:
    if len(products) > config.max_products:
        raise ValueError(
            f"Found {len(products)} products, exceeding MAX_PRODUCTS={config.max_products}"
        )
    visible = [product for product in products if product.get("is_visible")]
    if config.require_hidden and visible:
        ids = ", ".join(str(product["id"]) for product in visible)
        raise ValueError(f"Visible products found while REQUIRE_HIDDEN=true: {ids}")


def product_brand_id(product: dict) -> int | None:
    brand_id = product.get("brand_id")
    if brand_id in {None, ""}:
        return None
    return int(brand_id)


def filter_products_by_brand_id(products: list[dict], config: Config) -> list[dict]:
    return [
        product
        for product in products
        if product_brand_id(product) in config.brand_ids
    ]


def collect_variants(
    products: list[dict], client: BigCommerceClient
) -> list[dict]:
    variants = []
    for product in products:
        for variant in client.get_product_variants(int(product["id"])):
            variants.append(
                {
                    **variant,
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "product_sku": product.get("sku", ""),
                    "base_price": product.get("price"),
                    "brand_id": product.get("brand_id"),
                    "is_visible": product["is_visible"],
                }
            )
    return variants


def find_matching_variants(
    variants: list[dict], config: Config
) -> list[dict]:
    matches = []
    suffix = config.sku_suffix.casefold()
    for variant in variants:
        sku = str(variant.get("sku") or "")
        if sku.casefold().endswith(suffix):
            matches.append(variant)

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
    missing_base_prices = [variant for variant in matches if variant.get("base_price") is None]
    if missing_base_prices:
        labels = ", ".join(
            f"{variant['product_id']} ({variant.get('product_name', '')})"
            for variant in missing_base_prices
        )
        raise ValueError(f"Matching products do not have default prices: {labels}")
    return matches


def inspected_variant_summary(variants: list[dict]) -> list[dict]:
    return [
        {
            "product_id": variant["product_id"],
            "product_name": variant["product_name"],
            "product_sku": variant.get("product_sku"),
            "base_price": variant.get("base_price"),
            "brand_id": variant.get("brand_id"),
            "variant_id": variant.get("id"),
            "sku": variant.get("sku"),
            "price": variant.get("price"),
            "is_visible": variant["is_visible"],
        }
        for variant in variants
    ]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fiscal_year(run_date: date) -> str:
    year = run_date.year + 1 if run_date.month >= 9 else run_date.year
    return f"FY{year}"


def monthly_event_number(run_date: date) -> int | None:
    if run_date.day != 1:
        return None
    if run_date.month >= 10:
        return run_date.month - 9
    if run_date.month <= 8:
        return run_date.month + 3
    return None


def state_scope(config: Config) -> str:
    if config.schedule_mode == "daily_test":
        return "daily-test"
    return fiscal_year(config.run_date)


def variant_state_record(state: dict, scope: str, variant_id: int) -> dict:
    return state.get(scope, {}).get("variants", {}).get(str(variant_id), {})


def event_limit_for_brand(brand_id: int) -> int:
    try:
        return BRAND_EVENT_LIMITS[brand_id]
    except KeyError as error:
        raise ValueError(f"Unsupported proration brand ID: {brand_id}") from error


def planned_event_for_variant(
    variant: dict, config: Config, state: dict, scope: str
) -> tuple[int | None, str | None]:
    brand_id = int(variant["brand_id"])
    event_limit = event_limit_for_brand(brand_id)
    record = variant_state_record(state, scope, int(variant["id"]))

    if config.schedule_mode == "daily_test":
        if record.get("last_applied_on") == config.run_date.isoformat():
            return None, "already_applied_today"
        next_event = int(record.get("last_event", 0)) + 1
        if next_event > event_limit:
            return None, "brand_event_limit_reached"
        return next_event, None

    event_number = monthly_event_number(config.run_date)
    if event_number is None:
        return None, "not_scheduled_monthly_proration_date"
    if event_number > event_limit:
        return None, "brand_event_limit_reached"
    if int(record.get("last_event", 0)) >= event_number:
        return None, "already_applied_for_event"
    return event_number, None


def plan_proration_events(
    variants: list[dict], config: Config, state: dict, scope: str
) -> tuple[list[dict], list[dict]]:
    planned = []
    skipped = []
    for variant in variants:
        event_number, reason = planned_event_for_variant(variant, config, state, scope)
        if event_number is None:
            skipped.append(
                {
                    "product_id": variant["product_id"],
                    "product_name": variant["product_name"],
                    "variant_id": variant.get("id"),
                    "sku": variant.get("sku"),
                    "brand_id": variant.get("brand_id"),
                    "reason": reason,
                }
            )
            continue
        planned.append({**variant, "proration_event": event_number})
    return planned, skipped


def record_applied_events(
    state: dict,
    scope: str,
    changes: list[VariantPriceChange],
    run_date: date,
) -> None:
    scope_record = state.setdefault(scope, {"variants": {}})
    variants_record = scope_record.setdefault("variants", {})
    for change in changes:
        variants_record[str(change.variant_id)] = {
            "product_id": change.product_id,
            "product_name": change.product_name,
            "variant_id": change.variant_id,
            "sku": change.sku,
            "brand_id": change.brand_id,
            "base_price": str(change.base_price),
            "last_price": str(change.new_price),
            "last_event": change.proration_event,
            "last_applied_on": run_date.isoformat(),
        }


def run(config: Config, client: BigCommerceClient) -> list[VariantPriceChange]:
    state = load_state(config.state_path)
    scope = state_scope(config)
    products = client.get_products_in_category(config.category_id)
    validate_category_scope(products, config)
    matched_products = filter_products_by_brand_id(products, config)
    validate_update_scope(matched_products, config)
    inspected_variants = collect_variants(matched_products, client)
    variants = find_matching_variants(inspected_variants, config)
    planned_variants, skipped_variants = plan_proration_events(
        variants, config, state, scope
    )
    changes = build_variant_price_changes(
        planned_variants,
        config.minimum_price,
        periods=config.periods,
    )

    if not changes:
        LOGGER.warning(
            "No unapplied variants with SKUs ending in %r were found for brands %s",
            config.sku_suffix,
            ", ".join(str(brand_id) for brand_id in config.brand_ids),
        )

    for change in changes:
        LOGGER.info(
            "%s event %s for variant %s on product %s (%s), SKU %s: $%s base $%s -> $%s",
            "Updating" if config.apply_changes else "Would update",
            change.proration_event,
            change.variant_id,
            change.product_id,
            change.product_name,
            change.sku,
            change.old_price,
            change.base_price,
            change.new_price,
        )
        if config.apply_changes and change.new_price != change.old_price:
            client.update_variant_price(
                change.product_id, change.variant_id, change.new_price
            )

    if config.apply_changes and changes:
        record_applied_events(state, scope, changes, config.run_date)
        save_state(config.state_path, state)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if config.apply_changes else "dry-run",
        "schedule_mode": config.schedule_mode,
        "run_date": config.run_date.isoformat(),
        "state_scope": scope,
        "category_id": config.category_id,
        "brand_ids": list(config.brand_ids),
        "sku_suffix": config.sku_suffix,
        "periods": config.periods,
        "category_product_count": len(products),
        "brand_product_count": len(matched_products),
        "inspected_variant_count": len(inspected_variants),
        "matching_variant_count": len(variants),
        "variant_count": len(changes),
        "inspected_variants": inspected_variant_summary(inspected_variants),
        "skipped_variants": skipped_variants,
        "changes": [change.as_dict() for change in changes],
    }
    config.report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return changes


def write_error_report(config: Config, error: Exception) -> None:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "error",
        "category_id": config.category_id,
        "brand_ids": list(config.brand_ids),
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
