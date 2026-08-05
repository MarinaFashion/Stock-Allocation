import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from stock_auto_allocation.install import CUSTOM_FIELDS, create_role


def execute():
	"""after_install only runs on the very first `bench install-app`. Sites
	that installed this app before custom_is_transit / custom_transit_warehouse
	(and the Material Request field) were added never got them created. This
	patch re-runs the same idempotent field/role creation on every site via
	`bench migrate`, so upgrades self-heal without a manual step.
	"""
	create_custom_fields(CUSTOM_FIELDS, update=True)
	create_role()
	frappe.db.commit()
