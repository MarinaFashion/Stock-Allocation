"""Pure planning helpers for automatic complete-range allocation."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

EPSILON = 1e-9


@dataclass(frozen=True)
class StoreMetric:
    warehouse: str
    sales: float
    current_total: float
    complete_variants: int


def rank_stores(metrics: Iterable[StoreMetric]) -> List[str]:
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
    ranked_stores: Sequence[str],
    available_by_variant: Mapping[str, float],
    minimum_per_variant: int,
) -> List[str]:
    """Return the maximum number of stores supportable by every variant."""
    if not ranked_stores or not available_by_variant:
        return []

    minimum = max(1, int(minimum_per_variant or 1))
    capacities = [
        max(0, floor(float(qty) + EPSILON)) // minimum
        for qty in available_by_variant.values()
    ]
    complete_store_capacity = min(capacities) if capacities else 0
    return list(ranked_stores[: min(len(ranked_stores), complete_store_capacity)])


def build_target_matrix(
    variants: Sequence[str],
    selected_stores: Sequence[str],
    all_stores: Sequence[str],
    velocity: Mapping[Tuple[str, str], float],
    coverage_days: int,
    available_by_variant: Mapping[str, float],
    minimum_per_variant: int,
) -> Dict[Tuple[str, str], int]:
    """Allocate the minimum complete range first, then demand-based depth."""
    minimum = max(1, int(minimum_per_variant or 1))
    target = {
        (store, variant): 0
        for store in all_stores
        for variant in variants
    }
    remaining = {
        variant: max(
            0,
            floor(float(available_by_variant.get(variant, 0)) + EPSILON),
        )
        for variant in variants
    }

    for store in selected_stores:
        for variant in variants:
            qty = min(minimum, remaining[variant])
            target[(store, variant)] = qty
            remaining[variant] -= qty

    for variant in variants:
        desired = {
            store: max(
                minimum,
                int(
                    round(
                        float(velocity.get((store, variant), 0.0))
                        * max(0, int(coverage_days))
                    )
                ),
            )
            for store in selected_stores
        }

        while remaining[variant] > 0 and selected_stores:
            progressed = False
            for store in selected_stores:
                if remaining[variant] <= 0:
                    break
                key = (store, variant)
                if target[key] < desired[store]:
                    target[key] += 1
                    remaining[variant] -= 1
                    progressed = True

            if not progressed:
                for store in selected_stores:
                    if remaining[variant] <= 0:
                        break
                    target[(store, variant)] += 1
                    remaining[variant] -= 1
                    progressed = True

            if not progressed:
                break

    return target


def deficits_and_surpluses(
    variants: Sequence[str],
    stores: Sequence[str],
    destination_current: Mapping[Tuple[str, str], float],
    source_current: Mapping[Tuple[str, str], float],
    target: Mapping[Tuple[str, str], int],
) -> Tuple[List[Tuple[str, str, int]], Dict[Tuple[str, str], int]]:
    deficits = []
    surplus = {}

    for store in stores:
        for variant in variants:
            wanted = int(target.get((store, variant), 0))
            destination_qty = int(
                floor(float(destination_current.get((store, variant), 0.0)) + EPSILON)
            )
            source_qty = int(
                floor(float(source_current.get((store, variant), 0.0)) + EPSILON)
            )

            if wanted > destination_qty:
                deficits.append((store, variant, wanted - destination_qty))
            if source_qty > wanted:
                surplus[(store, variant)] = source_qty - wanted

    return deficits, surplus
