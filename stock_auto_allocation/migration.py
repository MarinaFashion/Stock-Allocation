"""Safe schema additions implemented as Custom Fields."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


LOGISTICS_FIELDS = [
    {
        "fieldname": "shipment_mode",
        "label": "Shipment Mode",
        "fieldtype": "Select",
        "options": "Direct\nConsolidated City Transfer",
        "read_only": 1,
        "insert_after": "stock_auto_allocation_run",
        "description": "Physical logistics mode assigned by Stock Allocation.",
    },
    {
        "fieldname": "transfer_shipment_batch",
        "label": "Transfer Shipment Batch",
        "fieldtype": "Link",
        "options": "Transfer Shipment Batch",
        "read_only": 1,
        "insert_after": "shipment_mode",
        "description": "City-consolidated physical shipment batch, when applicable.",
    },
    {
        "fieldname": "source_city",
        "label": "Source City",
        "fieldtype": "Data",
        "read_only": 1,
        "insert_after": "transfer_shipment_batch",
    },
    {
        "fieldname": "destination_city",
        "label": "Destination City",
        "fieldtype": "Data",
        "read_only": 1,
        "insert_after": "source_city",
    },
    {
        "fieldname": "origin_hub",
        "label": "Origin Consolidation Hub",
        "fieldtype": "Link",
        "options": "Warehouse",
        "read_only": 1,
        "insert_after": "destination_city",
    },
    {
        "fieldname": "destination_hub",
        "label": "Destination Consolidation Hub",
        "fieldtype": "Link",
        "options": "Warehouse",
        "read_only": 1,
        "insert_after": "origin_hub",
    },
]


CUSTOM_FIELDS = {
    "Stock Allocation Run Item": [
        {
            "fieldname": "display_date",
            "label": "Display Date",
            "fieldtype": "Date",
            "insert_after": "item_name",
            "read_only": 1,
            "in_list_view": 1,
            "description": "Template display date used to audit launch/grace-period logic.",
        },
        {
            "fieldname": "grace_period_till_date",
            "label": "Grace Period Till Date",
            "fieldtype": "Date",
            "insert_after": "display_date",
            "read_only": 1,
            "in_list_view": 1,
            "description": "Inclusive final date of the New Release Grace Period for this template.",
        },
    ],
    "Material Request": LOGISTICS_FIELDS,
    "Stock Entry": LOGISTICS_FIELDS,
}


def after_migrate():
    create_custom_fields(CUSTOM_FIELDS, update=True)
