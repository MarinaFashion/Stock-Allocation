"""Full spreading/grouping implementation for Stock Allocation Run.

Registered through ``override_doctype_class`` in hooks.py. The existing
doctype implementation remains untouched; approval and Material Request
creation continue to use the original inherited methods.
"""

from __future__ import annotations

from math import floor

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from stock_auto_allocation.allocation_engine import (
    StoreMetric,
    build_target_matrix,
    choose_selected_stores,
    deficits_and_surpluses,
    rank_stores,
)
from stock_auto_allocation.stock_auto_allocation.doctype.stock_allocation_run.stock_allocation_run import (
    StockAllocationRun as BaseStockAllocationRun,
    _get_effective_stock,
    _get_store_warehouses,
    _get_transit_warehouse,
    _sum_sales_qty,
)


class FullScopeStockAllocationRun(BaseStockAllocationRun):
    """Style-level reallocation with variant-range protection."""

    @frappe.whitelist()
    def generate_proposal(self):
        if not self.items:
            frappe.throw(_("Pull items into the working list first (Get Items)."))
        if not self.dc_warehouse:
            frappe.throw(_("Set the DC Warehouse before generating a proposal."))

        stores = _get_store_warehouses(self.company)
        if not stores:
            frappe.throw(
                _('No warehouses are flagged "Is Store" for company {0}.').format(
                    self.company
                )
            )

        self.proposal_lines = []
        lookback_start = add_days(nowdate(), -cint(self.lookback_period_days))
        unfulfilled = []

        for row in self.items:
            if row.excluded:
                continue

            variants = frappe.get_all(
                "Item",
                filters={"variant_of": row.item_template, "disabled": 0},
                pluck="name",
                order_by="name asc",
            )
            if not variants:
                continue

            result = self._plan_style(
                row.item_template,
                variants,
                stores,
                row.mode,
                lookback_start,
            )
            unfulfilled.extend(result)

        self.status = "Proposal Generated"
        self.save()

        if unfulfilled:
            preview = [
                f"{item} needed by {store} (short {qty})"
                for item, store, qty in unfulfilled[:20]
            ]
            more = f" (+{len(unfulfilled) - 20} more)" if len(unfulfilled) > 20 else ""
            frappe.msgprint(
                _("Some target quantities could not be covered: {0}{1}").format(
                    "; ".join(preview), more
                ),
                indicator="orange",
                alert=True,
            )

    def _plan_style(self, template, variants, stores, requested_mode, lookback_start):
        current = {}
        velocity = {}
        metrics = []
        total_store_stock = 0

        for store in stores:
            style_sales = 0
            style_stock = 0
            complete = 0
            for variant in variants:
                stock = max(
                    0,
                    floor(
                        flt(
                            _get_effective_stock(
                                variant,
                                store,
                                bool(self.consider_transit_at_target),
                            )
                        )
                    ),
                )
                sales = flt(
                    _sum_sales_qty(
                        [variant],
                        warehouse=store,
                        from_date=lookback_start,
                    )
                )
                current[(store, variant)] = stock
                velocity[(store, variant)] = sales / max(
                    1, cint(self.lookback_period_days)
                )
                style_sales += sales
                style_stock += stock
                if stock > 0:
                    complete += 1

            total_store_stock += style_stock
            metrics.append(
                StoreMetric(
                    warehouse=store,
                    sales=style_sales,
                    current_total=style_stock,
                    complete_variants=complete,
                )
            )

        dc_by_variant = {
            variant: max(
                0,
                floor(
                    flt(
                        _get_effective_stock(
                            variant,
                            self.dc_warehouse,
                            bool(self.consider_transit_at_source),
                        )
                    )
                ),
            )
            for variant in variants
        }
        total_stock = total_store_stock + sum(dc_by_variant.values())

        # Existing manual Mode remains respected. When blank, use the agreed
        # operational threshold: below 100 pieces = Grouping, otherwise Spreading.
        mode = requested_mode or ("Grouping" if total_stock < 100 else "Spreading")

        ranked = rank_stores(metrics)
        selected = choose_selected_stores(
            mode,
            ranked,
            total_stock,
            len(variants),
        )
        target = build_target_matrix(
            mode=mode,
            variants=variants,
            selected_stores=selected,
            ranked_stores=ranked,
            current=current,
            velocity=velocity,
            coverage_days=cint(self.coverage_days),
            total_stock=total_store_stock,
        )

        deficits, surplus = deficits_and_surpluses(
            variants,
            stores,
            current,
            target,
        )

        # Stronger destinations and missing core variants are handled first.
        rank_index = {store: index for index, store in enumerate(ranked)}
        deficits.sort(key=lambda row: (rank_index.get(row[0], 999999), row[1]))

        unfulfilled = []
        for destination, variant, needed in deficits:
            remaining = needed

            # Tier 1: use confirmed/allowed DC stock first.
            from_dc = min(remaining, dc_by_variant.get(variant, 0))
            if from_dc > 0:
                self._append_proposal(
                    template,
                    variant,
                    self.dc_warehouse,
                    destination,
                    from_dc,
                    "DC",
                )
                dc_by_variant[variant] -= from_dc
                remaining -= from_dc

            # Tier 2: allow multiple source stores, nearest first. A source can
            # donate only the quantity above its final style/variant target.
            if remaining > 0:
                donors = [
                    source
                    for source in stores
                    if source != destination
                    and surplus.get((source, variant), 0) > 0
                ]
                donors.sort(
                    key=lambda source: (
                        _distance_between(source, destination),
                        rank_index.get(source, 999999),
                        source,
                    )
                )

                for source in donors:
                    if remaining <= 0:
                        break
                    sendable = surplus.get((source, variant), 0)
                    qty = min(remaining, sendable)
                    if qty <= 0:
                        continue
                    self._append_proposal(
                        template,
                        variant,
                        source,
                        destination,
                        qty,
                        "Store",
                    )
                    surplus[(source, variant)] -= qty
                    remaining -= qty

            if remaining > 0:
                unfulfilled.append((variant, destination, remaining))

        return unfulfilled

    def _append_proposal(
        self,
        template,
        variant,
        source,
        destination,
        qty,
        tier,
    ):
        if qty <= 0 or source == destination:
            return
        self.append(
            "proposal_lines",
            {
                "item_template": template,
                "item_code": variant,
                "source_warehouse": source,
                "target_warehouse": destination,
                "transit_warehouse": _get_transit_warehouse(destination),
                "qty": qty,
                "tier": tier,
                "status": "Proposed",
            },
        )


def _distance_between(source, destination):
    """Return symmetric configured distance, or a large fallback value."""
    if source == destination:
        return 0

    rows = frappe.get_all(
        "Store Distance",
        filters=[
            ["from_store", "in", [source, destination]],
            ["to_store", "in", [source, destination]],
        ],
        fields=["from_store", "to_store", "distance_km"],
    )
    for row in rows:
        pair = {row.from_store, row.to_store}
        if pair == {source, destination}:
            return flt(row.distance_km)

    return 10**12
