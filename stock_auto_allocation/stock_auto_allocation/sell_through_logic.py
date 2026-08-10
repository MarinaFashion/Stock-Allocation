"""Pure sell-through planning helpers."""

from __future__ import annotations

from math import ceil
from typing import Optional


def daily_velocity(sales_qty: float, lookback_days: int) -> float:
    return max(0.0, float(sales_qty or 0)) / max(1, int(lookback_days or 1))


def required_qty(
    sales_qty: float,
    lookback_days: int,
    coverage_days: int,
    minimum_per_variant: int,
) -> int:
    velocity = daily_velocity(sales_qty, lookback_days)
    demand_cover = int(ceil(velocity * max(0, int(coverage_days or 0))))
    return max(max(1, int(minimum_per_variant or 1)), demand_cover)


def protected_qty(
    sales_qty: float,
    lookback_days: int,
    protection_days: int,
    minimum_per_variant: int,
) -> int:
    velocity = daily_velocity(sales_qty, lookback_days)
    demand_cover = int(ceil(velocity * max(0, int(protection_days or 0))))
    return max(max(1, int(minimum_per_variant or 1)), demand_cover)


def days_cover(stock_qty: float, sales_qty: float, lookback_days: int) -> Optional[float]:
    velocity = daily_velocity(sales_qty, lookback_days)
    if velocity <= 0:
        return None
    return max(0.0, float(stock_qty or 0)) / velocity


def donor_is_commercially_valid(
    *,
    source_variant_sales: float,
    target_variant_sales: float,
    source_style_sales: float,
    target_style_sales: float,
    source_stock: float,
    lookback_days: int,
    target_coverage_days: int,
    range_phase: bool,
) -> bool:
    """Protect stronger selling stores unless they have clearly excessive cover."""
    if float(source_variant_sales or 0) <= 0:
        return True

    if range_phase:
        if float(target_style_sales or 0) > float(source_style_sales or 0):
            return True
    else:
        if float(target_variant_sales or 0) > float(source_variant_sales or 0):
            return True

    source_cover = days_cover(source_stock, source_variant_sales, lookback_days)
    if source_cover is None:
        return True

    excessive_cover_threshold = max(
        float(target_coverage_days or 0) * 1.5,
        float(target_coverage_days or 0) + 3.0,
    )
    return source_cover > excessive_cover_threshold
