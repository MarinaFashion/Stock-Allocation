"""Material Request lifecycle cleanup for Stock Allocation Run links."""

import frappe


def on_trash(doc, method=None):
    """Clear proposal-line backlinks before Frappe validates MR links.

    Frappe executes on_trash before backlink validation. This allows a
    cancelled Material Request to be deleted normally while retaining Link
    fields and normal link protection everywhere else.
    """
    rows = frappe.get_all(
        "Stock Allocation Proposal Line",
        filters={"material_request": doc.name},
        fields=["name", "parent"],
    )
    if not rows:
        return

    parents = set()
    for row in rows:
        parents.add(row.parent)
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
        if frappe.db.exists("Stock Allocation Run", parent):
            frappe.db.set_value(
                "Stock Allocation Run",
                parent,
                "status",
                "Approved",
                update_modified=False,
            )
