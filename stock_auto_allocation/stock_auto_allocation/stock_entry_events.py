import frappe


LOGISTICS_FIELDS = (
    "shipment_mode",
    "transfer_shipment_batch",
    "source_city",
    "destination_city",
    "origin_hub",
    "destination_hub",
)


def preserve_allocation_route(doc, method=None):
    if doc.doctype != "Stock Entry" or not doc.get("items"):
        return

    routes = []
    allocation_runs = set()
    logistics_values = {}

    for row in doc.items:
        mr_name = row.get("material_request")
        if not mr_name:
            continue

        fields = [
            "stock_auto_allocation_run",
            "set_from_warehouse",
            "set_warehouse",
            *LOGISTICS_FIELDS,
        ]
        mr = frappe.db.get_value(
            "Material Request",
            mr_name,
            fields,
            as_dict=True,
        )
        if not mr or not mr.stock_auto_allocation_run:
            continue

        allocation_runs.add(mr.stock_auto_allocation_run)
        source = None
        target = None

        if row.get("material_request_item"):
            mr_item = frappe.db.get_value(
                "Material Request Item",
                row.material_request_item,
                ["from_warehouse", "warehouse"],
                as_dict=True,
            )
            if mr_item:
                source = mr_item.from_warehouse
                target = mr_item.warehouse

        source = source or mr.set_from_warehouse
        target = target or mr.set_warehouse

        if source and target:
            row.s_warehouse = source
            row.t_warehouse = target
            routes.append((source, target))

        for fieldname in LOGISTICS_FIELDS:
            value = mr.get(fieldname)
            if value:
                logistics_values.setdefault(fieldname, set()).add(value)

    if not routes:
        return

    if len(allocation_runs) == 1 and doc.meta.has_field("stock_auto_allocation_run"):
        doc.stock_auto_allocation_run = next(iter(allocation_runs))

    sources = {source for source, target in routes}
    targets = {target for source, target in routes}

    if len(sources) == 1:
        doc.from_warehouse = next(iter(sources))
    if len(targets) == 1:
        doc.to_warehouse = next(iter(targets))

    # A Stock Entry created from one route should inherit one consistent set of
    # logistics instructions from its Material Request.
    for fieldname, values in logistics_values.items():
        if len(values) == 1 and doc.meta.has_field(fieldname):
            doc.set(fieldname, next(iter(values)))
