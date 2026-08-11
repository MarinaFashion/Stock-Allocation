"""Material Request routing and economic transfer controls."""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from stock_auto_allocation.stock_auto_allocation.consolidated_stock_allocation_run import (
    ConsolidatedStockAllocationRun,
)


class EconomicRoutingStockAllocationRun(ConsolidatedStockAllocationRun):
    def validate(self):
        super().validate()
        if cint(self.minimum_store_transfer_qty) < 0:
            self.minimum_store_transfer_qty = 0

    @frappe.whitelist()
    def create_material_requests(self):
        if self.status != "Approved":
            frappe.throw(_("Only an approved run can have Material Requests created."))

        reviewed = self.proposal_review_status == "Reviewed"
        minimum_qty = max(0, cint(self.minimum_store_transfer_qty))
        apply_minimum_to_dc = bool(cint(self.apply_minimum_to_dc_transfers))

        groups = {}
        for line in self.proposal_lines:
            if line.status != "Approved":
                continue

            final_qty = flt(line.reviewed_qty) if reviewed else flt(line.qty)
            if final_qty <= 0:
                continue

            key = (
                line.source_warehouse,
                line.target_warehouse,
                line.transit_warehouse,
            )
            groups.setdefault(key, []).append((line, final_qty))

        if not groups:
            frappe.throw(
                _("There are no approved quantities to create Material Requests for.")
            )

        eligible_groups = []
        skipped_groups = []

        for (source, target, transit), rows in groups.items():
            route_qty = sum(flt(route_line_qty) for line_obj, route_line_qty in rows)
            source_is_dc = source == self.dc_warehouse

            threshold_applies = (
                minimum_qty > 0
                and (not source_is_dc or apply_minimum_to_dc)
            )

            if threshold_applies and route_qty < minimum_qty:
                skipped_groups.append(
                    {
                        "source": source,
                        "target": target,
                        "transit": transit,
                        "qty": route_qty,
                        "minimum": minimum_qty,
                        "rows": rows,
                    }
                )
                continue

            eligible_groups.append(
                {
                    "source": source,
                    "target": target,
                    "transit": transit,
                    "qty": route_qty,
                    "rows": rows,
                }
            )

        errors = []
        created = 0

        for route in eligible_groups:
            source = route["source"]
            target = route["target"]
            transit = route["transit"]
            rows = route["rows"]

            try:
                mr = frappe.new_doc("Material Request")
                mr.material_request_type = "Material Transfer"
                mr.company = self.company
                mr.schedule_date = nowdate()
                mr.stock_auto_allocation_run = self.name
                mr.set_from_warehouse = source
                mr.set_warehouse = transit

                for line, final_qty in rows:
                    mr.append(
                        "items",
                        {
                            "item_code": line.item_code,
                            "qty": final_qty,
                            "warehouse": transit,
                            "from_warehouse": source,
                            "schedule_date": nowdate(),
                        },
                    )

                mr.insert(ignore_permissions=True)
                mr.submit()
                created += 1

                for line, final_qty in rows:
                    line.status = "Requested"
                    line.material_request = mr.name

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    (
                        f"Stock Allocation Run {self.name}: MR creation failed "
                        f"for {source} -> {target} via {transit}"
                    ),
                )
                errors.append(f"{source} → {target}")

        if created:
            self.status = "Requested"

        self.save()

        if skipped_groups:
            preview = [
                (
                    f'{row["source"]} → {row["target"]}: '
                    f'{row["qty"]:g} pcs (minimum {row["minimum"]})'
                )
                for row in skipped_groups[:15]
            ]
            more = (
                f" (+{len(skipped_groups) - 15} more)"
                if len(skipped_groups) > 15
                else ""
            )
            frappe.msgprint(
                _(
                    "Material Requests were not created for routes below the "
                    "minimum transfer quantity: {0}{1}"
                ).format("; ".join(preview), more),
                indicator="orange",
                alert=True,
            )

        if errors:
            frappe.msgprint(
                _(
                    "Some Material Requests could not be created and were skipped: "
                    "{0}. Check the Error Log."
                ).format(", ".join(errors)),
                indicator="orange",
                alert=True,
            )

        if not created and skipped_groups and not errors:
            frappe.msgprint(
                _(
                    "No Material Requests were created because every applicable "
                    "route was below the minimum transfer quantity."
                ),
                indicator="orange",
                alert=True,
            )
