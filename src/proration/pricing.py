from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


CENT = Decimal("0.01")


@dataclass(frozen=True)
class VariantPriceChange:
    product_id: int
    product_name: str
    variant_id: int
    sku: str
    brand_id: int
    proration_event: int
    old_price: Decimal
    base_price: Decimal
    new_price: Decimal
    is_visible: bool

    @property
    def reduction(self) -> Decimal:
        return self.base_price - self.new_price

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "variant_id": self.variant_id,
            "sku": self.sku,
            "brand_id": self.brand_id,
            "proration_event": self.proration_event,
            "old_price": str(self.old_price),
            "base_price": str(self.base_price),
            "new_price": str(self.new_price),
            "reduction": str(self.reduction),
            "is_visible": self.is_visible,
        }


def reduced_price(
    current_price: Decimal,
    reduction_fraction: Decimal,
    minimum_price: Decimal,
) -> Decimal:
    if current_price < 0:
        raise ValueError("Current price cannot be negative")
    if not Decimal("0") < reduction_fraction < Decimal("1"):
        raise ValueError("Reduction fraction must be greater than 0 and less than 1")
    if minimum_price < 0:
        raise ValueError("Minimum price cannot be negative")

    result = (current_price * (Decimal("1") - reduction_fraction)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return max(result, minimum_price)


def prorated_price(
    base_price: Decimal,
    proration_event: int,
    *,
    periods: int = 12,
    minimum_price: Decimal,
) -> Decimal:
    if base_price < 0:
        raise ValueError("Base price cannot be negative")
    if proration_event <= 0:
        raise ValueError("Proration event must be positive")
    if proration_event >= periods:
        remaining_periods = 1
    else:
        remaining_periods = periods - proration_event
    result = (base_price * Decimal(remaining_periods) / Decimal(periods)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return max(result, minimum_price)


def build_variant_price_changes(
    variants: list[dict[str, Any]],
    minimum_price: Decimal,
    *,
    periods: int = 12,
) -> list[VariantPriceChange]:
    changes = []
    for variant in variants:
        old_price = Decimal(str(variant["price"])).quantize(CENT)
        base_price = Decimal(str(variant["base_price"])).quantize(CENT)
        proration_event = int(variant["proration_event"])
        changes.append(
            VariantPriceChange(
                product_id=int(variant["product_id"]),
                product_name=str(variant["product_name"]),
                variant_id=int(variant["id"]),
                sku=str(variant["sku"]),
                brand_id=int(variant["brand_id"]),
                proration_event=proration_event,
                old_price=old_price,
                base_price=base_price,
                new_price=prorated_price(
                    base_price,
                    proration_event,
                    periods=periods,
                    minimum_price=minimum_price,
                ),
                is_visible=bool(variant["is_visible"]),
            )
        )
    return changes
