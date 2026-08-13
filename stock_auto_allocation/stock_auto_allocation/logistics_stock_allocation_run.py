"""City-pair shipment consolidation layered on top of commercial allocation."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from stock_auto_allocation.stock_auto_allocation.commercial_stock_allocation_run import (
    CommercialStockAllocationRun,
)


class LogisticsStockAllocationRun(CommercialStockAllocationRun):
    """Create MRs normally while grouping physical intercity shipments economically."""

    def validate(self):
        super().validate()
        if cint(self.minimum_consolidated_city_transfer_qty) < 0:
            self.minimum_consolidated_city_transfer_qty = 0

    @frappe.whitelist()
    def create_material_requests(self):
        # Requested is allowed so economically-held Approved lines can be retried
        # later without recreating already-requested proposal lines.
        if self.status not in ("Approved", "Requested"):
            frappe.throw(
                _("Only an Approved or Requested run can create Material Requests.")
            )

        reviewed = self.proposal_review_status == "Reviewed"
        direct_minimum = max(0, cint(self.minimum_store_transfer_qty))
        city_minimum = max(0, cint(self.minimum_consolidated_city_transfer_qty))
        apply_direct_minimum_to_dc = bool(cint(self.apply_minimum_to_dc_transfers))

        routes = self._approved_routes(reviewed)
        if not routes:
            frappe.throw(
                _("There are no approved quantities to create Material Requests for.")
            )

        warehouse_city = self._warehouse_city_map(
            {
                warehouse
                for route in routes
                for warehouse in (route["source"], route["target"])
                if warehouse
            }
        )

        # Add city metadata once. DC routes and missing/same-city routes never enter
        # intercity consolidation.
        for route in routes:
            route["source_city"] = warehouse_city.get(route["source"])
            route["destination_city"] = warehouse_city.get(route["target"])
            route["source_is_dc"] = route["source"] == self.dc_warehouse
            route["city_pair"] = (
                route["source_city"],
                route["destination_city"],
            )

        intercity_pools = defaultdict(list)
        for route in routes:
            if (
                city_minimum > 0
                and not route["source_is_dc"]
                and route["source_city"]
                and route["destination_city"]
                and route["source_city"] != route["destination_city"]
            ):
                intercity_pools[route["city_pair"]].append(route)

        qualifying_pairs = {}
        for city_pair, pool in intercity_pools.items():
            total = sum(flt(route["qty"]) for route in pool)
            if total >= city_minimum:
                qualifying_pairs[city_pair] = pool

        eligible = []
        skipped = []

        for route in routes:
            pair = route["city_pair"]

            if pair in qualifying_pairs:
                # Entire city pair consolidates together, including routes which
                # individually exceed the direct-transfer minimum.
                route["shipment_mode"] = "Consolidated City Transfer"
                eligible.append(route)
                continue

            threshold_applies = (
                direct_minimum > 0
                and (
                    not route["source_is_dc"]
                    or apply_direct_minimum_to_dc
                )
            )

            if threshold_applies and flt(route["qty"]) < direct_minimum:
                route["hold_reason"] = "Below direct minimum"
                skipped.append(route)
                continue

            route["shipment_mode"] = "Direct"
            eligible.append(route)

        # Create batch headers before MRs so each MR can carry a clickable batch link.
        batches = {}
        for city_pair, pool in qualifying_pairs.items():
            batch = self._create_city_batch(city_pair, pool)
            batches[city_pair] = batch

            for route in pool:
                route["shipment_batch"] = batch.name
                route["origin_hub"] = batch.origin_hub
                route["destination_hub"] = batch.destination_hub

        errors = []
        created = 0
        batch_created_counts = defaultdict(int)

        for route in eligible:
            savepoint = f"stock_alloc_route_{created}_{len(errors)}"
            frappe.db.savepoint(savepoint)

            try:
                mr = self._create_route_material_request(route)
                created += 1

                for line, final_qty in route["rows"]:
                    line.status = "Requested"
                    line.material_request = mr.name

                if route.get("shipment_batch"):
                    batch_created_counts[route["shipment_batch"]] += 1
                    self._set_batch_item_material_request(
                        route["shipment_batch"],
                        route,
                        mr.name,
                    )

            except Exception:
                frappe.db.rollback(save_point=savepoint)
                frappe.log_error(
                    frappe.get_traceback(),
                    (
                        f"Stock Allocation Run {self.name}: MR creation failed "
                        f'for {route["source"]} -> {route["target"]} '
                        f'via {route["transit"]}'
                    ),
                )
                errors.append(f'{route["source"]} → {route["target"]}')

        # Finalize batch status based on successfully-created MRs.
        for pair, batch in batches.items():
            expected = len(qualifying_pairs[pair])
            success = batch_created_counts.get(batch.name, 0)

            if success == expected:
                status = "Ready"
            elif success > 0:
                status = "Partially Created"
            else:
                # No successful MR: remove the empty batch to avoid operational noise.
                frappe.delete_doc(
                    "Transfer Shipment Batch",
                    batch.name,
                    ignore_permissions=True,
                    force=True,
                )
                continue

            frappe.db.set_value(
                "Transfer Shipment Batch",
                batch.name,
                "status",
                status,
                update_modified=False,
            )

        if created:
            self.status = "Requested"

        self.save()

        self._show_logistics_summary(
            created=created,
            skipped=skipped,
            qualifying_pairs=qualifying_pairs,
            batches=batches,
            errors=errors,
            direct_minimum=direct_minimum,
            city_minimum=city_minimum,
        )

    def _approved_routes(self, reviewed):
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

        routes = []
        for (source, target, transit), rows in groups.items():
            routes.append(
                {
                    "source": source,
                    "target": target,
                    "transit": transit,
                    "qty": sum(flt(route_line_qty) for line_obj, route_line_qty in rows),
                    "rows": rows,
                }
            )

        return routes

    def _warehouse_city_map(self, warehouses):
        if not warehouses:
            return {}

        meta = frappe.get_meta("Warehouse")
        city_field = None
        for candidate in ("city", "custom_city"):
            if meta.has_field(candidate):
                city_field = candidate
                break

        if not city_field:
            frappe.throw(
                _(
                    "Warehouse does not have a City field. Expected fieldname 'city' "
                    "or 'custom_city'."
                )
            )

        rows = frappe.get_all(
            "Warehouse",
            filters={"name": ["in", list(warehouses)]},
            fields=["name", city_field],
        )
        return {row.name: row.get(city_field) for row in rows}

    def _create_city_batch(self, city_pair, routes):
        source_city, destination_city = city_pair

        source_totals = defaultdict(float)
        target_totals = defaultdict(float)

        for route in routes:
            source_totals[route["source"]] += flt(route["qty"])
            target_totals[route["target"]] += flt(route["qty"])

        # Highest movement volume wins. Warehouse name is a deterministic tie-breaker.
        origin_hub = sorted(
            source_totals.items(),
            key=lambda row: (-row[1], row[0]),
        )[0][0]
        destination_hub = sorted(
            target_totals.items(),
            key=lambda row: (-row[1], row[0]),
        )[0][0]

        batch = frappe.new_doc("Transfer Shipment Batch")
        batch.stock_allocation_run = self.name
        batch.status = "Planned"
        batch.source_city = source_city
        batch.destination_city = destination_city
        batch.origin_hub = origin_hub
        batch.destination_hub = destination_hub
        batch.total_qty = sum(flt(route["qty"]) for route in routes)
        batch.transfer_count = len(routes)

        for route in routes:
            batch.append(
                "items",
                {
                    "material_request": "",
                    "source_warehouse": route["source"],
                    "target_warehouse": route["target"],
                    "transit_warehouse": route["transit"],
                    "qty": route["qty"],
                },
            )

        batch.insert(ignore_permissions=True)
        return batch

    def _create_route_material_request(self, route):
        source = route["source"]
        target = route["target"]
        transit = route["transit"]

        mr = frappe.new_doc("Material Request")
        mr.material_request_type = "Material Transfer"
        mr.company = self.company
        mr.schedule_date = nowdate()
        mr.stock_auto_allocation_run = self.name

        mr.set_from_warehouse = source
        mr.set_warehouse = transit

        if mr.meta.has_field("shipment_mode"):
            mr.shipment_mode = route.get("shipment_mode") or "Direct"
        if mr.meta.has_field("transfer_shipment_batch"):
            mr.transfer_shipment_batch = route.get("shipment_batch")
        if mr.meta.has_field("source_city"):
            mr.source_city = route.get("source_city")
        if mr.meta.has_field("destination_city"):
            mr.destination_city = route.get("destination_city")
        if mr.meta.has_field("origin_hub"):
            mr.origin_hub = route.get("origin_hub")
        if mr.meta.has_field("destination_hub"):
            mr.destination_hub = route.get("destination_hub")

        for line, final_qty in route["rows"]:
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
        return mr

    def _set_batch_item_material_request(self, batch_name, route, mr_name):
        # Data field is intentional: it avoids a second hard MR backlink and the
        # circular deletion problem previously experienced in this app.
        row_name = frappe.db.get_value(
            "Transfer Shipment Batch Item",
            {
                "parent": batch_name,
                "parenttype": "Transfer Shipment Batch",
                "source_warehouse": route["source"],
                "target_warehouse": route["target"],
                "transit_warehouse": route["transit"],
            },
            "name",
        )
        if row_name:
            frappe.db.set_value(
                "Transfer Shipment Batch Item",
                row_name,
                "material_request",
                mr_name,
                update_modified=False,
            )

    def _show_logistics_summary(
        self,
        created,
        skipped,
        qualifying_pairs,
        batches,
        errors,
        direct_minimum,
        city_minimum,
    ):
        if qualifying_pairs:
            details = []
            for pair, routes in list(qualifying_pairs.items())[:10]:
                batch = batches.get(pair)
                total = sum(flt(route["qty"]) for route in routes)
                batch_name = batch.name if batch else "-"
                details.append(
                    f"{pair[0]} → {pair[1]}: {total:g} pcs ({batch_name})"
                )
            more = (
                f" (+{len(qualifying_pairs) - 10} more)"
                if len(qualifying_pairs) > 10
                else ""
            )
            frappe.msgprint(
                _(
                    "City-consolidated shipment batches: {0}{1}"
                ).format("; ".join(details), more),
                indicator="blue",
                alert=True,
            )

        if skipped:
            preview = [
                f'{route["source"]} → {route["target"]}: {route["qty"]:g} pcs'
                for route in skipped[:15]
            ]
            more = f" (+{len(skipped) - 15} more)" if len(skipped) > 15 else ""
            frappe.msgprint(
                _(
                    "Routes kept on economic hold because their city pair did not "
                    "qualify for consolidation and the individual route was below "
                    "the direct minimum: {0}{1}"
                ).format("; ".join(preview), more),
                indicator="orange",
                alert=True,
            )

        if errors:
            frappe.msgprint(
                _(
                    "Some Material Requests could not be created and were skipped: "
                    "{0}. Check Error Log."
                ).format(", ".join(errors)),
                indicator="orange",
                alert=True,
            )

        if not created and skipped and not errors:
            frappe.msgprint(
                _(
                    "No Material Requests were created. No city pair reached the "
                    "consolidated minimum, and every applicable route was below the "
                    "direct-transfer minimum."
                ),
                indicator="orange",
                alert=True,
            )
