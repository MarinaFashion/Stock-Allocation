"""Material Request lifecycle synchronization for Stock Allocation Runs."""

import frappe


def on_trash(doc, method=None):
    """Clear proposal-line MR references without weakening run deletion rules."""
    rows = frappe.get_all(
        "Stock Allocation Proposal Line",
        filters={"material_request": doc.name},
        fields=["name", "parent"],
    )
    if not rows:
        return

    parents = {row.parent for row in rows}

    for row in rows:
        frappe.db.set_value(
            "Stock Allocation Proposal Line",
            row.name,
            {
                "material_request": "",
                "status": "Approved",
            },
            update_modified=False,
        )

    for parent in parents:
        if not frappe.db.exists("Stock Allocation Run", parent):
            continue

        run_status = frappe.db.get_value("Stock Allocation Run", parent, "status")
        if run_status == "Cancelled":
            continue

        remaining = frappe.db.count(
            "Material Request",
            filters={
                "stock_auto_allocation_run": parent,
                "name": ["!=", doc.name],
            },
        )
        frappe.db.set_value(
            "Stock Allocation Run",
            parent,
            "status",
            "Requested" if remaining else "Approved",
            update_modified=False,
        )
