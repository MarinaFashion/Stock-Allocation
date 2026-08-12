"""Commercial allocation controls for fashion launches and document governance."""

from math import inf

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, nowdate

from stock_auto_allocation.stock_auto_allocation.economic_routing_stock_allocation_run import (
    EconomicRoutingStockAllocationRun,
)
from stock_auto_allocation.stock_auto_allocation.sell_through_logic import (
    daily_velocity,
    days_cover,
    protected_qty,
    required_qty,
)
from stock_auto_allocation.stock_auto_allocation.doctype.stock_allocation_run.stock_allocation_run import (
    _get_effective_stock,
    _sum_sales_qty,
)


class CommercialStockAllocationRun(EconomicRoutingStockAllocationRun):
    """Add launch protection, depth concentration, and strict document lifecycle."""

    def validate(self):
        super().validate()
        if cint(self.new_release_grace_period_days) < 0:
            self.new_release_grace_period_days = 0

    def _plan_style(
        self,
        template,
        variants,
        stores,
        minimum_per_variant,
        lookback_start,
        scope,
    ):
        """Plan one style by fully serving higher-priority targets before the next.

        The inherited engine previously restored minimum range across several stores
        before adding depth. For scarce fashion stock this can spread supply too
        thinly. This version ranks targets commercially, completes the first target's
        range, fills its justified depth, then moves to the next target.
        """
        source_stores, target_stores, use_dc = self._scope_stores(scope, stores)

        lookback_days = max(1, cint(self.lookback_period_days))
        target_coverage_days = max(1, cint(self.coverage_days))
        source_protection_days = max(0, cint(self.source_protection_days))
        grace_days = max(0, cint(self.new_release_grace_period_days))

        source_stock = {}
        target_stock = {}
        sales = {}
        style_sales = {}

        relevant = list(dict.fromkeys(source_stores + target_stores))

        for store in relevant:
            style_total = 0.0
            for variant in variants:
                source_stock[(store, variant)] = self._effective_qty(
                    variant,
                    store,
                    bool(self.consider_transit_at_source),
                )
                target_stock[(store, variant)] = self._effective_qty(
                    variant,
                    store,
                    bool(self.consider_transit_at_target),
                )
                sold = max(
                    0.0,
                    flt(
                        _sum_sales_qty(
                            [variant],
                            warehouse=store,
                            from_date=lookback_start,
                        )
                    ),
                )
                sales[(store, variant)] = sold
                style_total += sold
            style_sales[store] = style_total

        eligible_targets = [
            store for store in target_stores if style_sales.get(store, 0) > 0
        ]
        if not eligible_targets:
            return [], "No Target Sales"

        # Resolve all display dates in one query per style. This avoids adding
        # per-store/per-variant database traffic to large allocation runs.
        grace_active = self._grace_active_by_variant(
            template=template,
            variants=variants,
            grace_days=grace_days,
        )

        source_protected = {}
        target_required = {}

        for source in source_stores:
            for variant in variants:
                normal_floor = protected_qty(
                    sales_qty=sales[(source, variant)],
                    lookback_days=lookback_days,
                    protection_days=source_protection_days,
                    minimum_per_variant=minimum_per_variant,
                )

                if grace_active[variant]:
                    # Soft launch freeze: keep enough depth for the same target
                    # coverage policy, while still allowing obvious excess stock
                    # to move. This protects visual merchandising without trapping
                    # all stock at a weak store.
                    launch_floor = required_qty(
                        sales_qty=sales[(source, variant)],
                        lookback_days=lookback_days,
                        coverage_days=target_coverage_days,
                        minimum_per_variant=minimum_per_variant,
                    )
                    normal_floor = max(normal_floor, launch_floor)

                source_protected[(source, variant)] = normal_floor

        for target in eligible_targets:
            for variant in variants:
                target_required[(target, variant)] = required_qty(
                    sales_qty=sales[(target, variant)],
                    lookback_days=lookback_days,
                    coverage_days=target_coverage_days,
                    minimum_per_variant=minimum_per_variant,
                )

        dc_initial = {
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

        selected_targets = self._select_targets_for_complete_range(
            scope=scope,
            eligible_targets=eligible_targets,
            variants=variants,
            minimum_per_variant=minimum_per_variant,
            source_stores=source_stores,
            source_stock=source_stock,
            source_protected=source_protected,
            target_stock=target_stock,
            style_sales=style_sales,
            dc_available=dc_initial,
        )

        if not selected_targets:
            return [], "Insufficient Range Supply"

        selected_targets = self._rank_targets_for_concentration(
            selected_targets=selected_targets,
            variants=variants,
            target_stock=target_stock,
            sales=sales,
            style_sales=style_sales,
            minimum_per_variant=minimum_per_variant,
            lookback_days=lookback_days,
        )

        sim_source = dict(source_stock)
        sim_target = dict(target_stock)
        dc_remaining = dict(dc_initial)
        proposal_count_before = len(self.proposal_lines)
        unfulfilled = []

        # Concentration policy: complete and deepen Target #1 first, then Target #2.
        for target in selected_targets:
            # Phase A for this target: restore its complete minimum range.
            for variant in variants:
                need = max(0, minimum_per_variant - sim_target[(target, variant)])
                if need <= 0:
                    continue

                remaining = self._allocate_need(
                    template=template,
                    variant=variant,
                    target=target,
                    needed=need,
                    range_phase=True,
                    selected_targets=selected_targets,
                    source_stores=source_stores,
                    source_stock=source_stock,
                    original_target_stock=target_stock,
                    sim_source=sim_source,
                    sim_target=sim_target,
                    sales=sales,
                    style_sales=style_sales,
                    source_protected=source_protected,
                    target_required=target_required,
                    dc_initial=dc_initial,
                    dc_remaining=dc_remaining,
                    use_dc=use_dc,
                    minimum_per_variant=minimum_per_variant,
                    lookback_days=lookback_days,
                    target_coverage_days=target_coverage_days,
                )
                if remaining > 0:
                    unfulfilled.append((variant, target, remaining))

            # Phase B for the same target: fill justified depth before moving on.
            depth_needs = []
            for variant in variants:
                need = max(
                    0,
                    target_required[(target, variant)] - sim_target[(target, variant)],
                )
                if need <= 0:
                    continue

                velocity = daily_velocity(sales[(target, variant)], lookback_days)
                current_cover = days_cover(
                    sim_target[(target, variant)],
                    sales[(target, variant)],
                    lookback_days,
                )
                depth_needs.append(
                    (
                        variant,
                        need,
                        current_cover if current_cover is not None else inf,
                        velocity,
                    )
                )

            depth_needs.sort(key=lambda row: (row[2], -row[3], row[0]))

            for variant, need, _cover, _velocity in depth_needs:
                remaining = self._allocate_need(
                    template=template,
                    variant=variant,
                    target=target,
                    needed=need,
                    range_phase=False,
                    selected_targets=selected_targets,
                    source_stores=source_stores,
                    source_stock=source_stock,
                    original_target_stock=target_stock,
                    sim_source=sim_source,
                    sim_target=sim_target,
                    sales=sales,
                    style_sales=style_sales,
                    source_protected=source_protected,
                    target_required=target_required,
                    dc_initial=dc_initial,
                    dc_remaining=dc_remaining,
                    use_dc=use_dc,
                    minimum_per_variant=minimum_per_variant,
                    lookback_days=lookback_days,
                    target_coverage_days=target_coverage_days,
                )
                if remaining > 0:
                    unfulfilled.append((variant, target, remaining))

        if len(self.proposal_lines) == proposal_count_before and not unfulfilled:
            return [], "No Transfer Required"

        return unfulfilled, None

    def _rank_targets_for_concentration(
        self,
        selected_targets,
        variants,
        target_stock,
        sales,
        style_sales,
        minimum_per_variant,
        lookback_days,
    ):
        """Rank scarce-stock receivers before sequential fulfillment.

        Priority is lowest style days-cover first, then higher style sales, then
        more missing variants. Store name is only a deterministic tie-breaker.
        """
        ranked = []
        for store in selected_targets:
            current_stock = sum(target_stock[(store, variant)] for variant in variants)
            velocity = style_sales.get(store, 0) / max(1, lookback_days)
            style_cover = current_stock / velocity if velocity > 0 else inf
            missing_variants = sum(
                1
                for variant in variants
                if target_stock[(store, variant)] < minimum_per_variant
            )
            ranked.append((style_cover, -style_sales.get(store, 0), -missing_variants, store))

        ranked.sort()
        return [row[3] for row in ranked]

    @staticmethod
    def _grace_active_by_variant(template, variants, grace_days):
        if grace_days <= 0:
            return {variant: False for variant in variants}

        meta = frappe.get_meta("Item")
        if meta.has_field("display_date"):
            fieldname = "display_date"
        elif meta.has_field("custom_display_date"):
            fieldname = "custom_display_date"
        else:
            return {variant: False for variant in variants}

        item_codes = list(dict.fromkeys([template] + list(variants)))
        rows = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", fieldname],
        )
        display_dates = {row.name: row.get(fieldname) for row in rows}
        template_date = display_dates.get(template)
        today = getdate(nowdate())

        result = {}
        for variant in variants:
            display_date = display_dates.get(variant) or template_date
            if not display_date:
                result[variant] = False
                continue

            age_days = date_diff(today, getdate(display_date))
            result[variant] = 0 <= age_days < grace_days

        return result

    @frappe.whitelist()
    def cancel_allocation_run(self):
        """Cancel only after every generated Material Request is cancelled/deleted."""
        if self.status == "Cancelled":
            return

        requests = frappe.get_all(
            "Material Request",
            filters={"stock_auto_allocation_run": self.name},
            fields=["name", "docstatus"],
            order_by="creation asc",
        )
        active = [row for row in requests if cint(row.docstatus) != 2]

        if active:
            preview = ", ".join(row.name for row in active[:15])
            more = f" (+{len(active) - 15} more)" if len(active) > 15 else ""
            frappe.throw(
                _(
                    "Cancel the generated Material Requests first. Active requests: {0}{1}"
                ).format(preview, more)
            )

        self.status = "Cancelled"
        self.save()

    def on_trash(self):
        """Block run deletion until all generated Material Requests are deleted."""
        requests = frappe.get_all(
            "Material Request",
            filters={"stock_auto_allocation_run": self.name},
            pluck="name",
            order_by="creation asc",
        )
        if requests:
            preview = ", ".join(requests[:15])
            more = f" (+{len(requests) - 15} more)" if len(requests) > 15 else ""
            frappe.throw(
                _(
                    "Cannot delete Stock Allocation Run {0}. Cancel and delete the generated Material Requests first: {1}{2}"
                ).format(self.name, preview, more)
            )

        # Do not call the inherited on_trash cleanup. The old implementation
        # cleared backlinks to force deletion and could recreate circular-governance
        # problems. Normal Frappe link protection remains active after this check.
