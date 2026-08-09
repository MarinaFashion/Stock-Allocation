"""Automatic complete-range allocation with configurable store scope."""

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

        if cint(self.default_minimum_per_variant) < 1:
            self.default_minimum_per_variant = 1

        for row in self.items or []:
            if cint(row.minimum_per_variant) < 0:
                row.minimum_per_variant = 0

        scope = self.allocation_scope or "Many-to-Many"

        if scope in ("One-to-Many", "One-to-One"):
            if not self.selected_source_store:
                frappe.throw(_("Select the Source Store for {0}.").format(scope))

        if scope in ("Many-to-One", "One-to-One"):
            if not self.selected_target_store:
                frappe.throw(_("Select the Target Store for {0}.").format(scope))

        if (
            self.selected_source_store
            and self.selected_target_store
            and self.selected_source_store == self.selected_target_store
        ):
            frappe.throw(_("Source and Target stores must be different."))

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

        scope = self.allocation_scope or "Many-to-Many"

        if scope in ("One-to-Many", "One-to-One"):
            if self.selected_source_store not in stores:
                frappe.throw(_("The selected source warehouse is not a store."))

        if scope in ("Many-to-One", "One-to-One"):
            if self.selected_target_store not in stores:
                frappe.throw(_("The selected target warehouse is not a store."))

        self.proposal_lines = []
        lookback_start = add_days(nowdate(), -cint(self.lookback_period_days))
        unfulfilled = []
        skipped = []
        default_minimum = max(1, cint(self.default_minimum_per_variant))

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

            item_minimum = cint(row.minimum_per_variant) or default_minimum

            result, reason = self._plan_style_by_scope(
                template=row.item_template,
                variants=variants,
                stores=stores,
                minimum_per_variant=item_minimum,
                lookback_start=lookback_start,
                scope=scope,
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
            more = (
                f" (+{len(unfulfilled) - 20} more)"
                if len(unfulfilled) > 20
                else ""
            )
            frappe.msgprint(
                _("Some target quantities could not be covered: {0}{1}").format(
                    "; ".join(preview), more
                ),
                indicator="orange",
                alert=True,
            )

    def _plan_style_by_scope(
        self,
        template,
        variants,
        stores,
        minimum_per_variant,
        lookback_start,
        scope,
    ):
        source_stores = list(stores)
        target_stores = list(stores)
        use_dc = True

        if scope == "One-to-Many":
            source_stores = [self.selected_source_store]
            target_stores = [
                store for store in stores
                if store != self.selected_source_store
            ]
            use_dc = False

        elif scope == "Many-to-One":
            target_stores = [self.selected_target_store]
            source_stores = [
                store for store in stores
                if store != self.selected_target_store
            ]
            use_dc = True

        elif scope == "One-to-One":
            source_stores = [self.selected_source_store]
            target_stores = [self.selected_target_store]
            use_dc = False

        if scope == "Many-to-Many":
            return self._plan_scoped_network(
                template=template,
                variants=variants,
                all_stores=stores,
                source_stores=source_stores,
                target_stores=target_stores,
                minimum_per_variant=minimum_per_variant,
                lookback_start=lookback_start,
                use_dc=use_dc,
            )

        return self._plan_restricted_scope(
            template=template,
            variants=variants,
            source_stores=source_stores,
            target_stores=target_stores,
            minimum_per_variant=minimum_per_variant,
            lookback_start=lookback_start,
            use_dc=use_dc,
        )

    def _plan_restricted_scope(
        self,
        template,
        variants,
        source_stores,
        target_stores,
        minimum_per_variant,
        lookback_start,
        use_dc,
    ):
        """Plan One-to-Many, Many-to-One and One-to-One transfers.

        Restricted scopes are demand-based rather than redistribution-based:
        every donor retains its own coverage/minimum reserve and every target
        receives only its calculated shortage.
        """
        days = max(1, cint(self.lookback_period_days))
        coverage_days = max(0, cint(self.coverage_days))

        stock = {}
        sales = {}
        required = {}
        surplus = {}
        deficits = {}

        relevant_stores = list(dict.fromkeys(source_stores + target_stores))

        for store in relevant_stores:
            for variant in variants:
                is_source = store in source_stores
                consider_transit = (
                    bool(self.consider_transit_at_source)
                    if is_source
                    else bool(self.consider_transit_at_target)
                )
                qty = self._effective_qty(
                    variant,
                    store,
                    consider_transit,
                )
                sold = max(
                    0,
                    flt(
                        _sum_sales_qty(
                            [variant],
                            warehouse=store,
                            from_date=lookback_start,
                        )
                    ),
                )
                velocity = sold / days
                wanted = max(
                    minimum_per_variant,
                    int(round(velocity * coverage_days)),
                )

                stock[(store, variant)] = qty
                sales[(store, variant)] = sold
                required[(store, variant)] = wanted

        eligible_targets = [
            store
            for store in target_stores
            if sum(sales[(store, variant)] for variant in variants) > 0
        ]
        if not eligible_targets:
            return [], _(
                "no selected target store has sales in the lookback period"
            )

        for source in source_stores:
            for variant in variants:
                surplus[(source, variant)] = max(
                    0,
                    stock[(source, variant)]
                    - required[(source, variant)],
                )

        for target in eligible_targets:
            for variant in variants:
                deficits[(target, variant)] = max(
                    0,
                    required[(target, variant)]
                    - stock[(target, variant)],
                )

        dc_available = {
            variant: (
                self._effective_qty(
                    variant,
                    self.dc_warehouse,
                    bool(self.consider_transit_at_source),
                )
                if use_dc
                else 0
            )
            for variant in variants
        }

        target_order = sorted(
            eligible_targets,
            key=lambda store: (
                -sum(sales[(store, variant)] for variant in variants),
                store,
            ),
        )

        unfulfilled = []

        for target in target_order:
            for variant in variants:
                remaining = deficits[(target, variant)]
                if remaining <= 0:
                    continue

                if use_dc:
                    qty = min(remaining, dc_available[variant])
                    if qty > 0:
                        self._append_proposal(
                            template=template,
                            variant=variant,
                            source=self.dc_warehouse,
                            destination=target,
                            qty=qty,
                            tier="DC",
                            source_stock=self._effective_qty(
                                variant,
                                self.dc_warehouse,
                                bool(self.consider_transit_at_source),
                            ),
                            source_sales=0,
                            target_stock=stock[(target, variant)],
                            target_sales=sales[(target, variant)],
                        )
                        dc_available[variant] -= qty
                        remaining -= qty

                donors = [
                    source
                    for source in source_stores
                    if source != target
                    and surplus[(source, variant)] > 0
                ]
                donors.sort(
                    key=lambda source: (
                        _distance_between(source, target),
                        -surplus[(source, variant)],
                        source,
                    )
                )

                for source in donors:
                    if remaining <= 0:
                        break

                    qty = min(
                        remaining,
                        surplus[(source, variant)],
                    )
                    if qty <= 0:
                        continue

                    self._append_proposal(
                        template=template,
                        variant=variant,
                        source=source,
                        destination=target,
                        qty=qty,
                        tier="Store",
                        source_stock=stock[(source, variant)],
                        source_sales=sales[(source, variant)],
                        target_stock=stock[(target, variant)],
                        target_sales=sales[(target, variant)],
                    )
                    surplus[(source, variant)] -= qty
                    remaining -= qty

                if remaining > 0:
                    unfulfilled.append((variant, target, remaining))

        if not any(
            flt(row.qty) > 0 and row.item_template == template
            for row in self.proposal_lines
        ) and not unfulfilled:
            return [], _("no transfer is required for the selected scope")

        return unfulfilled, None

    def _plan_scoped_network(
        self,
        template,
        variants,
        all_stores,
        source_stores,
        target_stores,
        minimum_per_variant,
        lookback_start,
        use_dc,
    ):
        destination_current = {}
        source_current = {}
        sales_by_store_variant = {}
        velocity = {}
        metrics = []
        store_sales = {}

        for store in all_stores:
            style_sales = 0
            style_stock = 0
            complete = 0

            for variant in variants:
                destination_stock = self._effective_qty(
                    variant,
                    store,
                    bool(self.consider_transit_at_target),
                )
                source_stock = self._effective_qty(
                    variant,
                    store,
                    bool(self.consider_transit_at_source),
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
                sales_by_store_variant[(store, variant)] = sales
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

        eligible_targets = [
            store
            for store in target_stores
            if flt(store_sales.get(store)) > 0
        ]
        if not eligible_targets:
            return [], _("no selected target store has sales in the lookback period")

        dc_by_variant = {
            variant: (
                self._effective_qty(
                    variant,
                    self.dc_warehouse,
                    bool(self.consider_transit_at_source),
                )
                if use_dc
                else 0
            )
            for variant in variants
        }

        available_by_variant = {
            variant: dc_by_variant[variant]
            + sum(source_current[(store, variant)] for store in source_stores)
            + sum(
                source_current[(store, variant)]
                for store in eligible_targets
                if store not in source_stores
            )
            for variant in variants
        }

        ranked_all = rank_stores(metrics)
        ranked_targets = [
            store for store in ranked_all if store in eligible_targets
        ]
        selected_targets = choose_selected_stores(
            ranked_targets,
            available_by_variant,
            minimum_per_variant,
        )

        if not selected_targets:
            return [], _(
                "the scarcest variant cannot support the minimum of {0} piece(s)"
            ).format(minimum_per_variant)

        target = build_target_matrix(
            variants=variants,
            selected_stores=selected_targets,
            all_stores=all_stores,
            velocity=velocity,
            coverage_days=cint(self.coverage_days),
            available_by_variant=available_by_variant,
            minimum_per_variant=minimum_per_variant,
        )

        deficits, surplus = deficits_and_surpluses(
            variants,
            all_stores,
            destination_current,
            source_current,
            target,
        )

        destination_rank = {
            store: index for index, store in enumerate(ranked_targets)
        }
        all_rank = {store: index for index, store in enumerate(ranked_all)}

        deficits = [
            row for row in deficits if row[0] in selected_targets
        ]
        deficits.sort(
            key=lambda row: (
                destination_rank.get(row[0], 999999),
                row[1],
            )
        )

        unfulfilled = []

        for destination, variant, needed in deficits:
            remaining = needed

            if use_dc:
                from_dc = min(remaining, dc_by_variant.get(variant, 0))
                if from_dc > 0:
                    self._append_proposal(
                        template=template,
                        variant=variant,
                        source=self.dc_warehouse,
                        destination=destination,
                        qty=from_dc,
                        tier="DC",
                        source_stock=self._effective_qty(
                            variant,
                            self.dc_warehouse,
                            bool(self.consider_transit_at_source),
                        ),
                        source_sales=0,
                        target_stock=destination_current[(destination, variant)],
                        target_sales=sales_by_store_variant[
                            (destination, variant)
                        ],
                    )
                    dc_by_variant[variant] -= from_dc
                    remaining -= from_dc

            if remaining > 0:
                donors = [
                    source
                    for source in source_stores
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

                    qty = min(
                        remaining,
                        surplus.get((source, variant), 0),
                    )
                    if qty <= 0:
                        continue

                    self._append_proposal(
                        template=template,
                        variant=variant,
                        source=source,
                        destination=destination,
                        qty=qty,
                        tier="Store",
                        source_stock=source_current[(source, variant)],
                        source_sales=sales_by_store_variant[(source, variant)],
                        target_stock=destination_current[
                            (destination, variant)
                        ],
                        target_sales=sales_by_store_variant[
                            (destination, variant)
                        ],
                    )
                    surplus[(source, variant)] -= qty
                    remaining -= qty

            if remaining > 0:
                unfulfilled.append((variant, destination, remaining))

        return unfulfilled, None

    @staticmethod
    def _effective_qty(item_code, warehouse, consider_transit):
        return max(
            0,
            floor(
                flt(
                    _get_effective_stock(
                        item_code,
                        warehouse,
                        consider_transit,
                    )
                )
            ),
        )

    def _append_proposal(
        self,
        template,
        variant,
        source,
        destination,
        qty,
        tier,
        source_stock,
        source_sales,
        target_stock,
        target_sales,
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
                "source_stock": source_stock,
                "source_sales": source_sales,
                "target_stock": target_stock,
                "target_sales": target_sales,
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
