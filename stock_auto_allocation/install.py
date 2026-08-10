import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "Warehouse": [
        {
            "fieldname": "stock_alloc_section",
            "label": "Stock Allocation",
            "fieldtype": "Section Break",
            "insert_after": "warehouse_type",
            "collapsible": 1,
        },
        {
            "fieldname": "custom_is_store",
            "label": "Is Store (used in Allocation)",
            "fieldtype": "Check",
            "insert_after": "stock_alloc_section",
            "description": "Marks this warehouse as a retail store eligible to receive/source stock in allocation runs.",
        },
        {
            "fieldname": "custom_is_distribution_center",
            "label": "Is Distribution Center (used in Allocation)",
            "fieldtype": "Check",
            "insert_after": "custom_is_store",
            "description": "Marks this warehouse as the DC / source warehouse for allocation.",
        },
        {
            "fieldname": "custom_is_transit",
            "label": "Is Transit Warehouse (used in Allocation)",
            "fieldtype": "Check",
            "insert_after": "custom_is_distribution_center",
            "description": "Marks this warehouse as a transit warehouse.",
        },
        {
            "fieldname": "custom_transit_warehouse",
            "label": "Transit Warehouse (for Allocation)",
            "fieldtype": "Link",
            "options": "Warehouse",
            "insert_after": "custom_is_transit",
            "description": "Transit warehouse associated with the store.",
        },
    ],
    "Stock Entry": [
        {
            "fieldname": "stock_auto_allocation_run",
            "label": "Stock Allocation Run",
            "fieldtype": "Link",
            "options": "Stock Allocation Run",
            "insert_after": "stock_entry_type",
            "read_only": 1,
            "description": "Reserved for a future Transit -> Store allocation leg.",
        },
    ],
    "Material Request": [
        {
            "fieldname": "stock_auto_allocation_run",
            "label": "Stock Allocation Run",
            "fieldtype": "Data",
            "insert_after": "material_request_type",
            "read_only": 1,
            "description": (
                "Stock Allocation Run document name. Stored as text to preserve "
                "traceability without creating a circular deletion dependency."
            ),
        },
    ],
}

ROLE_NAME = "Stock Allocation Manager"


def _ensure_custom_fields():
    create_custom_fields(CUSTOM_FIELDS, update=True)

    # Existing sites may already have this field as Link. Force the metadata
    # to Data during migration so Frappe's link checker no longer creates the
    # delete loop between Material Request and Stock Allocation Run.
    field_name = "Material Request-stock_auto_allocation_run"
    if frappe.db.exists("Custom Field", field_name):
        frappe.db.set_value(
            "Custom Field",
            field_name,
            {
                "fieldtype": "Data",
                "options": "",
                "read_only": 1,
            },
            update_modified=False,
        )
        frappe.clear_cache(doctype="Material Request")


def after_install():
    _ensure_custom_fields()
    create_role()
    frappe.db.commit()


def after_migrate():
    _ensure_custom_fields()
    create_role()


def create_role():
    if not frappe.db.exists("Role", ROLE_NAME):
        role = frappe.get_doc(
            {
                "doctype": "Role",
                "role_name": ROLE_NAME,
                "desk_access": 1,
            }
        )
        role.insert(ignore_permissions=True)
