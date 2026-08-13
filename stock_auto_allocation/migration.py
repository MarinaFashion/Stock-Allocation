"""Safe schema additions that are intentionally implemented as Custom Fields."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


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
}


def after_migrate():
    create_custom_fields(CUSTOM_FIELDS, update=True)
