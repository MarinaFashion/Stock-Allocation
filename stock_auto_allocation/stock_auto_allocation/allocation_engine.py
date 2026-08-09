"""Pure planning helpers for style-level stock reallocation.

No Frappe imports are used here, so the policy can be tested independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

EPSILON = 1e-9
GROUPING_DEPTH_PER_VARIANT = 2


@dataclass(frozen=True)
class StoreMetric:
    warehouse: str
    sales: float
    current_total: float
    complete_variants: int


def rank_stores(metrics: Iterable[StoreMetric]) -> List[str]:
    """Rank by item sales, then range completeness, then current stock."""
    return [
        row.warehouse
        for row in sorted(
            metrics,
            key=lambda row: (
                -float(row.sales),
                -int(row.complete_variants),
                -float(row.current_total),
                row.warehouse,
            ),
        )
    ]


def choose_selected_stores(
    mode: str,
    ranked_stores: Sequence[str],
    total_stock: float,
    variant_count: int,
) -> List[str]:
    """Choose stores that should continue carrying the style."""
    if not ranked_stores or variant_count <= 0 or total_stock <= 0:
        return []

    stock_units = floor(float(total_stock) + EPSILON)
    units_per_store = variant_count * (
        GROUPING_DEPTH_PER_VARIANT if mode == "Grouping" else 1
    )
    count = max(1, stock_units // max(1, units_per_store))
    return list(ranked_stores[: min(len(ranked_stores), count)])


def build_target_matrix(
    mode: str,
    variants: Sequence[str],
    selected_stores: Sequence[str],
    ranked_stores: Sequence[str],
    current: Mapping[Tuple[str, str], float],
    velocity: Mapping[Tuple[str, str], float],
    coverage_days: int,
    total_stock: float,
) -> Dict[Tuple[str, str], int]:
    """Build final integer stock targets.

    Complete variant ranges are allocated first. Remaining units add demand
    depth. Spreading rotates by variant across stores; Grouping fills stronger
    stores first.
    """
    target: Dict[Tuple[str, str], int] = {
        (store, variant): 0 for store in ranked_stores for variant in variants
    }
    remaining = floor(float(total_stock) + EPSILON)

    for store in selected_stores:
        for variant in variants:
            if remaining <= 0:
                return target
            target[(store, variant)] = 1
            remaining -= 1

    desired: Dict[Tuple[str, str], int] = {}
    for store in selected_stores:
        for variant in variants:
            demand_target = max(
                1,
                int(
                    round(
                        float(velocity.get((store, variant), 0.0))
                        * max(0, int(coverage_days))
                    )
                ),
            )
            desired[(store, variant)] = max(
                target[(store, variant)],
                demand_target,
            )

    if mode == "Grouping":
        ordered_pairs = [
            (store, variant)
            for store in selected_stores
            for variant in variants
        ]
    else:
        ordered_pairs = [
            (store, variant)
            for variant in variants
            for store in selected_stores
        ]

    while remaining > 0 and ordered_pairs:
        progressed = False
        for key in ordered_pairs:
            if remaining <= 0:
                break
            if target[key] < desired[key]:
                target[key] += 1
                remaining -= 1
                progressed = True

        if not progressed:
            for key in ordered_pairs:
                if remaining <= 0:
                    break
                target[key] += 1
                remaining -= 1
                progressed = True

        if not progressed:
            break

    return target


def deficits_and_surpluses(
    variants: Sequence[str],
    stores: Sequence[str],
    current: Mapping[Tuple[str, str], float],
    target: Mapping[Tuple[str, str], int],
) -> Tuple[List[Tuple[str, str, int]], Dict[Tuple[str, str], int]]:
    """Return destination deficits and donor surplus."""
    deficits: List[Tuple[str, str, int]] = []
    surplus: Dict[Tuple[str, str], int] = {}

    for store in stores:
        for variant in variants:
            actual = int(floor(float(current.get((store, variant), 0.0)) + EPSILON))
            wanted = int(target.get((store, variant), 0))
            if wanted > actual:
                deficits.append((store, variant, wanted - actual))
            elif actual > wanted:
                surplus[(store, variant)] = actual - wanted

    return deficits, surplus
