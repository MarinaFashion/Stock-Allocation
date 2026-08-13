"""Commercial allocation controls for fashion launches, target-demand modes, and governance."""

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
    _sum_sales_qty,
)
from stock_auto_allocation.stock_auto_allocation.doctype.stock_allocation_run.full_scope_stock_allocation_run import (
    _distance_between,
)


class CommercialStockAllocationRun(EconomicRoutingStockAllocationRun):
    """Fashion-specific launch protection and concentrated reallocation."""

    def validate(self):
        super().validate()
        if cint(self.new_release_grace_period_days) < 0:
            self.new_release_grace_period_days = 0

    @frappe.whitelist()
    def get_items(self):
        """Pull the normal working list, then expose launch dates for audit."""
        result = super().get_items()
        self._populate_working_list_display_dates()
        self.save()
        return result

    def _populate_working_list_display_dates(self):
        if not self.items:
            return

        templates = [row.item_template for row in self.items if row.item_template]
        info = self._display_info_for_items(templates, include_template_fallback=False)

        for row in self.items:
            display_date, grace_till = info.get(row.item_template, (None, None))
            if row.meta.has_field("display_date"):
                row.display_date = display_date
            if row.meta.has_field("grace_period_till_date"):
                row.grace_period_till_date = grace_till

    def _plan_style(
        self,
        template,
        variants,
        stores,
        minimum_per_variant,
        lookback_start,
        scope,
    ):
        """Fully serve higher-priority targets before moving to the next target."""
        source_stores, target_stores, use_dc = self._scope_stores(scope, stores)

        lookback_days = max(1, cint(self.lookback_period_days))
        target_coverage_days = max(1, cint(self.coverage_days))
        source_protection_days = max(0, cint(self.source_protection_days))
        grace_days = max(0, cint(self.new_release_grace_period_days))
        use_target_history = bool(cint(self.use_target_historical_sales))

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

        # Normal mode: historical sales prove the target can sell the style.
        # Reopen/minimum mode: target sales are deliberately ignored, therefore
        # a zero/immature target history must NOT make that store ineligible.
        if use_target_history:
            eligible_targets = [
                store for store in target_stores if style_sales.get(store, 0) > 0
            ]
            if not eligible_targets:
                return [], "No Target Sales"
        else:
            eligible_targets = list(target_stores)
            if not eligible_targets:
                return [], "No Eligible Target"

        # Resolve display dates once for the whole style and retain them for
        # proposal-line audit. Variant display date overrides template date.
        display_info = self._display_info_by_variant(template, variants, grace_days)
        self._proposal_display_info = getattr(self, "_proposal_display_info", {})
        self._proposal_display_info.update(display_info)

        grace_active = {
            variant: self._is_in_grace(display_info[variant][0], grace_days)
            for variant in variants
        }

        source_protected = {}
        target_required = {}

        # Source protection ALWAYS uses source-store historical performance,
        # even when target historical sales are intentionally disabled.
        for source in source_stores:
            for variant in variants:
                normal_floor = protected_qty(
                    sales_qty=sales[(source, variant)],
                    lookback_days=lookback_days,
                    protection_days=source_protection_days,
                    minimum_per_variant=minimum_per_variant,
                )

                if grace_active[variant]:
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
                if use_target_history:
                    target_required[(target, variant)] = required_qty(
                        sales_qty=sales[(target, variant)],
                        lookback_days=lookback_days,
                        coverage_days=target_coverage_days,
                        minimum_per_variant=minimum_per_variant,
                    )
                else:
                    # Reopened-store mode: target demand is display minimum only.
                    target_required[(target, variant)] = minimum_per_variant

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

        if use_target_history:
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
        else:
            selected_targets = self._select_targets_minimum_mode(
                scope=scope,
                eligible_targets=eligible_targets,
                variants=variants,
                minimum_per_variant=minimum_per_variant,
                source_stores=source_stores,
                source_stock=source_stock,
                source_protected=source_protected,
                target_stock=target_stock,
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
            use_target_history=use_target_history,
        )

        sim_source = dict(source_stock)
        sim_target = dict(target_stock)
        dc_remaining = dict(dc_initial)
        proposal_count_before = len(self.proposal_lines)
        unfulfilled = []

        # Concentration: finish Target #1 before starting Target #2.
        for target in selected_targets:
            # Range/minimum phase.
            for variant in variants:
                need = max(0, minimum_per_variant - sim_target[(target, variant)])
                if need <= 0:
                    continue

                remaining = self._allocate_need_commercial(
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
                    use_target_history=use_target_history,
                )
                if remaining > 0:
                    unfulfilled.append((variant, target, remaining))

            # Demand-depth phase exists only when target history is enabled.
            if not use_target_history:
                continue

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
                remaining = self._allocate_need_commercial(
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
                    use_target_history=use_target_history,
                )
                if remaining > 0:
                    unfulfilled.append((variant, target, remaining))

        if len(self.proposal_lines) == proposal_count_before and not unfulfilled:
            return [], "No Transfer Required"

        return unfulfilled, None

    def _allocate_need_commercial(
        self,
        template,
        variant,
        target,
        needed,
        range_phase,
        selected_targets,
        source_stores,
        source_stock,
        original_target_stock,
        sim_source,
        sim_target,
        sales,
        style_sales,
        source_protected,
        target_required,
        dc_initial,
        dc_remaining,
        use_dc,
        minimum_per_variant,
        lookback_days,
        use_target_history,
    ):
        """Allocate one target need while always respecting source performance.

        When target history is disabled, donor eligibility is determined by the
        surplus remaining after source protection. We intentionally do not
        compare target sales with source sales, because target sales were declared
        immature/unreliable for this run.
        """
        remaining = max(0, int(needed))
        if remaining <= 0:
            return 0

        if use_dc and dc_remaining.get(variant, 0) > 0:
            qty = min(remaining, dc_remaining[variant])
            if qty > 0:
                self._append_proposal(
                    template=template,
                    variant=variant,
                    source=self.dc_warehouse,
                    destination=target,
                    qty=qty,
                    tier="DC",
                    source_stock=dc_initial.get(variant, 0),
                    source_sales=0,
                    source_protection_qty=0,
                    target_stock=original_target_stock[(target, variant)],
                    target_sales=sales[(target, variant)],
                    target_required_qty=target_required[(target, variant)],
                    source_days_cover_value=None,
                    target_days_cover_value=days_cover(
                        original_target_stock[(target, variant)],
                        sales[(target, variant)],
                        lookback_days,
                    ),
                )
                dc_remaining[variant] -= qty
                sim_target[(target, variant)] += qty
                remaining -= qty

        if remaining <= 0:
            return 0

        donors = []
        for source in source_stores:
            if source == target:
                continue

            floor_qty = source_protected[(source, variant)]

            # A store which is also a selected receiver keeps its own requirement.
            if not range_phase and source in selected_targets:
                floor_qty = max(
                    floor_qty,
                    target_required.get((source, variant), minimum_per_variant),
                )

            available = max(0, sim_source[(source, variant)] - floor_qty)
            if available <= 0:
                continue

            cover = days_cover(
                sim_source[(source, variant)],
                sales[(source, variant)],
                lookback_days,
            )

            if use_target_history:
                # Preserve inherited commercial validity in the normal mode.
                from stock_auto_allocation.stock_auto_allocation.sell_through_logic import (
                    donor_is_commercially_valid,
                )

                if not donor_is_commercially_valid(
                    source_variant_sales=sales[(source, variant)],
                    target_variant_sales=sales[(target, variant)],
                    source_style_sales=style_sales.get(source, 0),
                    target_style_sales=style_sales.get(target, 0),
                    source_stock=sim_source[(source, variant)],
                    lookback_days=lookback_days,
                    target_coverage_days=max(1, cint(self.coverage_days)),
                    range_phase=range_phase,
                ):
                    continue

            donors.append((source, available, cover, floor_qty))

        # Source performance is always respected: zero/low-selling and
        # over-covered donors are preferred; distance is a later tie-breaker.
        donors.sort(
            key=lambda row: (
                0 if sales[(row[0], variant)] <= 0 else 1,
                sales[(row[0], variant)],
                style_sales.get(row[0], 0),
                -(row[2] if row[2] is not None else 10**12),
                _distance_between(row[0], target),
                row[0],
            )
        )

        for source, available, cover, floor_qty in donors:
            if remaining <= 0:
                break

            qty = min(remaining, available)
            if qty <= 0:
                continue

            target_cover_before = days_cover(
                original_target_stock[(target, variant)],
                sales[(target, variant)],
                lookback_days,
            )

            self._append_proposal(
                template=template,
                variant=variant,
                source=source,
                destination=target,
                qty=qty,
                tier="Store",
                source_stock=source_stock[(source, variant)],
                source_sales=sales[(source, variant)],
                source_protection_qty=floor_qty,
                target_stock=original_target_stock[(target, variant)],
                target_sales=sales[(target, variant)],
                target_required_qty=target_required[(target, variant)],
                source_days_cover_value=cover,
                target_days_cover_value=target_cover_before,
            )

            sim_source[(source, variant)] -= qty
            if (source, variant) in sim_target:
                sim_target[(source, variant)] = max(
                    0,
                    sim_target[(source, variant)] - qty,
                )

            sim_target[(target, variant)] += qty
            remaining -= qty

        return remaining

    def _select_targets_minimum_mode(
        self,
        scope,
        eligible_targets,
        variants,
        minimum_per_variant,
        source_stores,
        source_stock,
        source_protected,
        target_stock,
        dc_available,
    ):
        """Select minimum-only targets without using target sales in ranking."""
        if scope in ("Many-to-One", "One-to-One"):
            return list(eligible_targets)

        ranked = sorted(
            eligible_targets,
            key=lambda store: (
                -sum(
                    1
                    for variant in variants
                    if target_stock[(store, variant)] < minimum_per_variant
                ),
                -sum(
                    max(0, minimum_per_variant - target_stock[(store, variant)])
                    for variant in variants
                ),
                store,
            ),
        )

        supply = {
            variant: dc_available.get(variant, 0)
            + sum(
                max(
                    0,
                    source_stock[(source, variant)]
                    - source_protected[(source, variant)],
                )
                for source in source_stores
            )
            for variant in variants
        }

        selected = []
        cumulative_need = {variant: 0 for variant in variants}

        for candidate in ranked:
            candidate_need = {
                variant: max(
                    0,
                    minimum_per_variant - target_stock[(candidate, variant)],
                )
                for variant in variants
            }
            feasible = all(
                cumulative_need[variant] + candidate_need[variant] <= supply[variant]
                for variant in variants
            )
            already_complete = all(qty == 0 for qty in candidate_need.values())

            if feasible or already_complete:
                selected.append(candidate)
                for variant in variants:
                    cumulative_need[variant] += candidate_need[variant]

        return selected

    def _rank_targets_for_concentration(
        self,
        selected_targets,
        variants,
        target_stock,
        sales,
        style_sales,
        minimum_per_variant,
        lookback_days,
        use_target_history,
    ):
        ranked = []

        for store in selected_targets:
            missing_variants = sum(
                1
                for variant in variants
                if target_stock[(store, variant)] < minimum_per_variant
            )
            minimum_shortage = sum(
                max(0, minimum_per_variant - target_stock[(store, variant)])
                for variant in variants
            )

            if not use_target_history:
                ranked.append(
                    (-missing_variants, -minimum_shortage, store)
                )
                continue

            current_stock = sum(target_stock[(store, variant)] for variant in variants)
            velocity = style_sales.get(store, 0) / max(1, lookback_days)
            style_cover = current_stock / velocity if velocity > 0 else inf
            ranked.append(
                (
                    style_cover,
                    -style_sales.get(store, 0),
                    -missing_variants,
                    store,
                )
            )

        ranked.sort()
        if use_target_history:
            return [row[3] for row in ranked]
        return [row[2] for row in ranked]

    def _display_info_for_items(self, item_codes, include_template_fallback=False):
        """Return item -> (display_date, grace_till) in one query."""
        if not item_codes:
            return {}

        fieldname = self._display_date_fieldname()
        if not fieldname:
            return {code: (None, None) for code in item_codes}

        grace_days = max(0, cint(self.new_release_grace_period_days))
        rows = frappe.get_all(
            "Item",
            filters={"name": ["in", list(dict.fromkeys(item_codes))]},
            fields=["name", fieldname],
        )

        result = {}
        for row in rows:
            display_date = row.get(fieldname)
            grace_till = (
                add_days(getdate(display_date), grace_days - 1)
                if display_date and grace_days > 0
                else None
            )
            result[row.name] = (display_date, grace_till)

        for code in item_codes:
            result.setdefault(code, (None, None))
        return result

    def _display_info_by_variant(self, template, variants, grace_days):
        fieldname = self._display_date_fieldname()
        if not fieldname:
            return {variant: (None, None) for variant in variants}

        item_codes = list(dict.fromkeys([template] + list(variants)))
        rows = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", fieldname],
        )
        dates = {row.name: row.get(fieldname) for row in rows}
        template_date = dates.get(template)

        result = {}
        for variant in variants:
            display_date = dates.get(variant) or template_date
            grace_till = (
                add_days(getdate(display_date), grace_days - 1)
                if display_date and grace_days > 0
                else None
            )
            result[variant] = (display_date, grace_till)
        return result

    @staticmethod
    def _display_date_fieldname():
        meta = frappe.get_meta("Item")
        if meta.has_field("display_date"):
            return "display_date"
        if meta.has_field("custom_display_date"):
            return "custom_display_date"
        return None

    @staticmethod
    def _is_in_grace(display_date, grace_days):
        if not display_date or grace_days <= 0:
            return False
        age_days = date_diff(getdate(nowdate()), getdate(display_date))
        return 0 <= age_days < grace_days

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
        source_protection_qty,
        target_stock,
        target_sales,
        target_required_qty,
        source_days_cover_value,
        target_days_cover_value,
    ):
        """Append/consolidate normally, then stamp launch and demand-mode audit."""
        super()._append_proposal(
            template=template,
            variant=variant,
            source=source,
            destination=destination,
            qty=qty,
            tier=tier,
            source_stock=source_stock,
            source_sales=source_sales,
            source_protection_qty=source_protection_qty,
            target_stock=target_stock,
            target_sales=target_sales,
            target_required_qty=target_required_qty,
            source_days_cover_value=source_days_cover_value,
            target_days_cover_value=target_days_cover_value,
        )

        display_date, grace_till = getattr(
            self, "_proposal_display_info", {}
        ).get(variant, (None, None))

        # There is at most one Proposed row for this physical movement after
        # the consolidation layer. Stamp that row without another DB query.
        for line in reversed(self.proposal_lines):
            if (
                line.item_code == variant
                and line.source_warehouse == source
                and line.target_warehouse == destination
                and line.status == "Proposed"
            ):
                if line.meta.has_field("display_date"):
                    line.display_date = display_date
                if line.meta.has_field("grace_period_till_date"):
                    line.grace_period_till_date = grace_till
                if line.meta.has_field("target_historical_sales_used"):
                    line.target_historical_sales_used = cint(
                        self.use_target_historical_sales
                    )
                break

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
