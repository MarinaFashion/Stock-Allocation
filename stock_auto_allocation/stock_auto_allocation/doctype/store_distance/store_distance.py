import frappe
from frappe import _
from frappe.model.document import Document


class StoreDistance(Document):
	def validate(self):
		if self.from_store == self.to_store:
			frappe.throw(_("From Store and To Store cannot be the same warehouse."))

		# Distance is treated as symmetrical: block duplicate entries in
		# either direction (A→B or B→A) for the same pair.
		existing = frappe.db.exists(
			"Store Distance",
			{
				"name": ["!=", self.name],
				"from_store": ["in", [self.from_store, self.to_store]],
				"to_store": ["in", [self.from_store, self.to_store]],
			},
		)
		if existing:
			frappe.throw(
				_("A distance entry between {0} and {1} already exists ({2}).").format(
					self.from_store, self.to_store, existing
				)
			)
