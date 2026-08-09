"""Automatic complete-range implementation for Stock Allocation Run."""

from __future__ import annotations

from math import floor

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from stock_auto_allocation.stock_auto_allocation.allocation_engine import (
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
    def validate(self):
        super().validate()
        for row in self.items or []:
            if cint(row.minimum_per_variant) < 1:
                row.minimum_per_variant = 1

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
        skipped = []

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

            result, reason = self._plan_style(
                row.item_template,
                variants,
                stores,
                cint(row.minimum_per_variant) or 1,
                lookback_start,
            )
            unfulfilled.extend(result)
            if reason:
                skipped.append(f"{row.item_template}: {reason}")

        self.status = "Proposal Generated"
        self.save()

        if skipped:
            frappe.msgprint(
                _("Styles left unchanged: {0}").format("; ".join(skipped[:20])),
                indicator="orange",
                alert=True,
            )

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

    def _plan_style(
        self,
        template,
        variants,
        stores,
        minimum_per_variant,
        lookback_start,
    ):
        destination_current = {}
        source_current = {}
        velocity = {}
        metrics = []
        store_sales = {}

        for store in stores:
            style_sales = 0
            style_stock = 0
            complete = 0

            for variant in variants:
                destination_stock = max(
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
                source_stock = max(
                    0,
                    floor(
                        flt(
                            _get_effective_stock(
                                variant,
                                store,
                                bool(self.consider_transit_at_source),
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

                destination_current[(store, variant)] = destination_stock
                source_current[(store, variant)] = source_stock
                velocity[(store, variant)] = sales / max(
                    1, cint(self.lookback_period_days)
                )
                style_sales += sales
                style_stock += destination_stock
                if destination_stock >= minimum_per_variant:
                    complete += 1

            store_sales[store] = style_sales
            metrics.append(
                StoreMetric(
                    warehouse=store,
                    sales=style_sales,
                    current_total=style_stock,
                    complete_variants=complete,
                )
            )

        eligible_destinations = [
            store for store in stores if flt(store_sales.get(store)) > 0
        ]
        if not eligible_destinations:
            return [], _("no store has sales in the selected lookback period")

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

        available_by_variant = {
            variant: dc_by_variant[variant]
            + sum(source_current[(store, variant)] for store in stores)
            for variant in variants
        }

        ranked_all = rank_stores(metrics)
        ranked_destinations = [
            store for store in ranked_all if store in eligible_destinations
        ]
        selected = choose_selected_stores(
            ranked_destinations,
            available_by_variant,
            minimum_per_variant,
        )

        if not selected:
            return [], _(
                "the scarcest variant cannot support the minimum of {0} piece(s)"
            ).format(minimum_per_variant)

        target = build_target_matrix(
            variants=variants,
            selected_stores=selected,
            all_stores=stores,
            velocity=velocity,
            coverage_days=cint(self.coverage_days),
            available_by_variant=available_by_variant,
            minimum_per_variant=minimum_per_variant,
        )

        deficits, surplus = deficits_and_surpluses(
            variants,
            stores,
            destination_current,
            source_current,
            target,
        )

        destination_rank = {
            store: index for index, store in enumerate(ranked_destinations)
        }
        all_rank = {store: index for index, store in enumerate(ranked_all)}
        deficits = [row for row in deficits if row[0] in selected]
        deficits.sort(
            key=lambda row: (
                destination_rank.get(row[0], 999999),
                row[1],
            )
        )

        unfulfilled = []
        for destination, variant, needed in deficits:
            remaining = needed

            from_dc = min(remaining, dc_by_variant.get(variant, 0))
            if from_dc > 0:
                self._append_proposal(
                    template, variant, self.dc_warehouse,
                    destination, from_dc, "DC"
                )
                dc_by_variant[variant] -= from_dc
                remaining -= from_dc

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
                        all_rank.get(source, 999999),
                        source,
                    )
                )

                for source in donors:
                    if remaining <= 0:
                        break
                    qty = min(remaining, surplus.get((source, variant), 0))
                    if qty <= 0:
                        continue
                    self._append_proposal(
                        template, variant, source,
                        destination, qty, "Store"
                    )
                    surplus[(source, variant)] -= qty
                    remaining -= qty

            if remaining > 0:
                unfulfilled.append((variant, destination, remaining))

        return unfulfilled, None

    def _append_proposal(self, template, variant, source, destination, qty, tier):
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
        if {row.from_store, row.to_store} == {source, destination}:
            return flt(row.distance_km)

    return 10**12
