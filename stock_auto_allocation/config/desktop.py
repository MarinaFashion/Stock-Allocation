from frappe import _


def get_data():
	return [
		{
			"module_name": "Stock Auto Allocation",
			"category": "Modules",
			"label": _("Stock Auto Allocation"),
			"color": "#2E7D32",
			"icon": "octicon octicon-package",
			"type": "module",
			"description": "DC-to-store and store-to-store stock allocation based on sales velocity and coverage days.",
		}
	]
