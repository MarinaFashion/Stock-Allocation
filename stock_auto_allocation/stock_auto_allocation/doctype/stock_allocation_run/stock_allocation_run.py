"""
Stock Allocation Run -- core allocation engine.

v1.1.0 scope: Tier 1 (DC -> Store) allocation, and Tier 2 (Store -> Store
fallback) when DC stock can't fully cover a store's Required Quantity.
Grouping and Spreading store-*selection* algorithms (top-N ranking,
round-robin size distribution) are still reserved for a future version --
what v1.1.0 uses the per-item `mode` field for is narrower: deciding
Tier 2's safety-stock behavior at the source store (SRS FR-16):
  - Mode = Spreading: source store keeps enough to cover its OWN Coverage
    Days first (same formula, applied to itself); only surplus above that
    is available to send.
  - Mode = Grouping: safety stock is ignored entirely; source can be
    fully depleted.
  - Mode blank: Tier 2 is blocked for that item until a mode is chosen
    (surfaced via msgprint, not a hard failure of the whole run).

Formula (SRS 6.2):
    Daily Sales Velocity = Sales Qty (Lookback Period) / Lookback Period (days)
    Required Quantity    = MAX(0, Daily Sales Velocity * Coverage Days - Current Store Stock)

Tier 2 sourcing (SRS 6.5, as scoped for v1.1.0): only the SINGLE nearest
store (via the Store Distance master table) is tried per shortfall -- no
splitting across multiple source stores. Whatever that nearest store can't
cover is left unfulfilled (surfaced via msgprint).

In-transit stock handling (opt-in, per run -- see consider_transit_at_source
/ consider_transit_at_target on Stock Allocation Run): stock already sitting
in a warehouse's Transit Warehouse (requested but not yet confirmed into the
regular warehouse) is EXCLUDED from every stock calculation by default,
matching the pre-toggle behavior. Checking "Add In-Transit Stock to Source
Warehouses" lets a source (DC or, in Tier 2, another store) send out stock
that's still sitting in its own transit, before it's been confirmed
received. Checking "Add In-Transit Stock to Target Warehouses" counts a
destination store's in-transit stock as already covering part of its
Required Quantity, preventing over-allocation on top of a pending request.
Both default OFF/unchecked.

Reset (`start_over`): clears the working list and any generated proposal so
filter/criteria changes actually take effect on the next Get Items (which
only appends and skips duplicates by design, so changing filters after the
fact wouldn't otherwise remove stale rows). Blocked once Material Requests
already exist for the run (status "Requested"), since clearing
proposal_lines at that point would destroy the traceability link to real
documents -- start a new Stock Allocation Run instead.

Workflow (SRS 3.8, updated): Draft -> Items Pulled -> Proposal Generated ->
Approved -> Requested. No real document is created until
`create_material_requests` runs, and even then it raises a **Material
Request** (Material Transfer type) targeted at the destination store's
**Transit Warehouse**, not a direct Stock Entry into the store -- per the
two-step "transit then confirm" control process each store uses (goods land
in Transit, then the store manager inspects and moves them into the store
themselves, outside this app's scope).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint, add_days, nowdate


class StockAllocationRun(Document):
	def validate(self):
		if self.coverage_days is not None and self.coverage_days < 0:
			frappe.throw(_("Coverage Days cannot be negative."))
		if self.lookback_period_days is not None and self.lookback_period_days <= 0:
			frappe.throw(_("Lookback Period must be at least 1 day."))

	# ------------------------------------------------------------------
	# Step 1 -- FR-1/FR-2/FR-3: pull items matching the filters into the
	# working list, with all-store/all-time performance metrics (FR-4/5).
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def get_items(self):
		filters = {"has_variants": 1, "disabled": 0}
		if self.item_year:
			filters["item_year"] = self.item_year
		if self.season:
			filters["season"] = self.season
		if self.collection:
			filters["collection"] = self.collection
		if self.drop:
			filters["custom_drop"] = self.drop

		templates = frappe.get_all("Item", filters=filters, fields=["name", "item_name"])
		if not templates:
			frappe.msgprint(_("No template items matched the selected filters."))
			return

		existing = {d.item_template for d in self.items}
		window_start = _st_window_start()

		for tmpl in templates:
			if tmpl.name in existing:
				continue  # don't duplicate rows if Get Items is re-run

			variants = frappe.get_all("Item", filters={"variant_of": tmpl.name}, pluck="name")
			if not variants:
				continue

			total_sales = _sum_sales_qty(variants, window_start=window_start)
			total_balance = _sum_store_balance(variants, include_transit=bool(self.consider_transit_at_target))
			# Total Qty = Total Sales + Total Balance (units sold + units
			# still on hand across stores = total units that have ever
			# flowed through the store network for this item).
			total_qty = total_sales + total_balance
			st_percent = 0.0
			denom = total_sales + total_balance
			if denom > 0:
				st_percent = (total_sales / denom) * 100.0

			self.append(
				"items",
				{
					"item_template": tmpl.name,
					"total_qty": total_qty,
					"total_sales": total_sales,
					"total_balance": total_balance,
					"st_percent": st_percent,
				},
			)

		self.status = "Items Pulled"
		self.save()

	# ------------------------------------------------------------------
	# Reset -- clears the working list and any generated proposal so
	# filter/criteria changes actually take effect on the next Get Items
	# (get_items only appends and skips duplicates, so changing filters
	# after the fact wouldn't otherwise remove stale rows). Blocked once
	# Material Requests already exist for this run, since clearing
	# proposal_lines would destroy the traceability link to them -- start
	# a new Stock Allocation Run instead at that point.
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def start_over(self):
		if self.status == "Requested":
			frappe.throw(
				_(
					"This run already has Material Requests created against it and can't be "
					"reset, since that would lose the link to those requests. Create a new "
					"Stock Allocation Run instead."
				)
			)
		self.items = []
		self.proposal_lines = []
		self.status = "Draft"
		self.save()

	# ------------------------------------------------------------------
	# Step 2 -- FR-11..13 (Tier 1) and FR-14..16 (Tier 2 fallback). No
	# documents created yet. Also resolves each destination's Transit
	# Warehouse up front so the reviewer can see exactly where stock will
	# be requested to before approving.
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def generate_proposal(self):
		if not self.items:
			frappe.throw(_("Pull items into the working list first (Get Items)."))
		if not self.dc_warehouse:
			frappe.throw(_("Set the DC Warehouse before generating a proposal."))

		self.proposal_lines = []
		store_warehouses = _get_store_warehouses(self.company)
		if not store_warehouses:
			frappe.throw(
				_("No warehouses are flagged \"Is Store\" for company {0}. Set this on the Warehouse record.").format(
					self.company
				)
			)

		lookback_start = add_days(nowdate(), -cint(self.lookback_period_days))

		blocked_items = set()
		unfulfilled = []  # list of (item_code, store, qty_short) for the summary message

		for row in self.items:
			if row.excluded:
				continue

			variants = frappe.get_all("Item", filters={"variant_of": row.item_template}, pluck="name")
			for variant in variants:
				shortfalls = self._propose_tier1_for_variant(
					row.item_template, variant, store_warehouses, lookback_start
				)
				if not shortfalls:
					continue

				if not row.mode:
					blocked_items.add(row.item_template)
					continue

				remaining = self._propose_tier2_for_variant(
					row.item_template, variant, shortfalls, row.mode, store_warehouses, lookback_start
				)
				for store, qty_short in remaining:
					unfulfilled.append((variant, store, qty_short))

		self.status = "Proposal Generated"
		self.save()

		if blocked_items:
			frappe.msgprint(
				_(
					"Tier 2 (store-to-store) was skipped for these items because Mode "
					"(Grouping/Spreading) is not set: {0}. Set Mode on those rows and "
					"regenerate the proposal to cover their remaining shortfall."
				).format(", ".join(sorted(blocked_items))),
				indicator="orange",
				alert=True,
			)
		if unfulfilled:
			lines = [f"{item} needed by {store} (short {qty:.2f})" for item, store, qty in unfulfilled[:20]]
			more = f" (+{len(unfulfilled) - 20} more)" if len(unfulfilled) > 20 else ""
			frappe.msgprint(
				_("Some shortfalls could not be fully covered by the nearest store either: {0}{1}").format(
					"; ".join(lines), more
				),
				indicator="orange",
				alert=True,
			)

	def _propose_tier1_for_variant(self, item_template, variant, store_warehouses, lookback_start):
		"""Allocates from the DC in sales-velocity rank order. Returns a
		list of dicts describing any store whose Required Quantity wasn't
		fully covered by the DC -- callers use this to attempt Tier 2.
		"""
		dc_stock = _get_effective_stock(variant, self.dc_warehouse, bool(self.consider_transit_at_source))

		# Rank stores by sales velocity within the lookback period (FR-12),
		# and compute each store's Required Quantity (SRS 6.2).
		ranked = []
		for wh in store_warehouses:
			period_qty = _sum_sales_qty([variant], warehouse=wh, from_date=lookback_start)
			daily_velocity = flt(period_qty) / cint(self.lookback_period_days)
			current_stock = _get_effective_stock(variant, wh, bool(self.consider_transit_at_target))
			required_qty = max(0.0, (daily_velocity * cint(self.coverage_days)) - current_stock)
			if required_qty > 0:
				ranked.append({"warehouse": wh, "velocity": daily_velocity, "required_qty": required_qty})

		ranked.sort(key=lambda r: r["velocity"], reverse=True)

		dc_remaining = dc_stock
		shortfalls = []
		for r in ranked:
			alloc_qty = 0.0
			if dc_remaining > 0:
				alloc_qty = min(r["required_qty"], dc_remaining)

			if alloc_qty > 0:
				transit_warehouse = _get_transit_warehouse(r["warehouse"])
				self.append(
					"proposal_lines",
					{
						"item_template": item_template,
						"item_code": variant,
						"source_warehouse": self.dc_warehouse,
						"target_warehouse": r["warehouse"],
						"transit_warehouse": transit_warehouse,
						"qty": alloc_qty,
						"tier": "DC",
						"status": "Proposed",
					},
				)
				dc_remaining -= alloc_qty

			shortfall = r["required_qty"] - alloc_qty
			if shortfall > 0:
				shortfalls.append({"warehouse": r["warehouse"], "shortfall": shortfall, "velocity": r["velocity"]})

		return shortfalls

	def _propose_tier2_for_variant(self, item_template, variant, shortfalls, mode, store_warehouses, lookback_start):
		"""Store-to-store fallback (FR-14..16). For each shortfall, tries
		ONLY the single nearest store (Store Distance table) -- no
		splitting across multiple sources (v1.1.0 scope decision).
		Returns a list of (store, still_unfulfilled_qty) for shortfalls
		the nearest store couldn't (fully) cover.
		"""
		still_unfulfilled = []

		for s in shortfalls:
			destination = s["warehouse"]
			needed = s["shortfall"]

			nearest = _get_nearest_store(destination, store_warehouses)
			if not nearest:
				still_unfulfilled.append((destination, needed))
				continue

			source_stock = _get_effective_stock(variant, nearest, bool(self.consider_transit_at_source))

			if mode == "Spreading":
				# Source keeps enough to cover its OWN Coverage Days first.
				source_period_qty = _sum_sales_qty([variant], warehouse=nearest, from_date=lookback_start)
				source_daily_velocity = flt(source_period_qty) / cint(self.lookback_period_days)
				own_coverage_target = source_daily_velocity * cint(self.coverage_days)
				sendable = max(0.0, source_stock - own_coverage_target)
			else:  # Grouping -- safety stock ignored entirely
				sendable = max(0.0, source_stock)

			alloc_qty = min(needed, sendable)
			if alloc_qty > 0:
				transit_warehouse = _get_transit_warehouse(destination)
				self.append(
					"proposal_lines",
					{
						"item_template": item_template,
						"item_code": variant,
						"source_warehouse": nearest,
						"target_warehouse": destination,
						"transit_warehouse": transit_warehouse,
						"qty": alloc_qty,
						"tier": "Store",
						"status": "Proposed",
					},
				)

			remaining = needed - alloc_qty
			if remaining > 0:
				still_unfulfilled.append((destination, remaining))

		return still_unfulfilled

	# ------------------------------------------------------------------
	# Step 3 -- FR-19/20: manual approval gate. No documents created here.
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def approve(self):
		if self.status != "Proposal Generated":
			frappe.throw(_("Only a run with status \"Proposal Generated\" can be approved."))
		if not self.proposal_lines:
			frappe.throw(_("There are no proposal lines to approve."))

		for line in self.proposal_lines:
			if not line.transit_warehouse:
				frappe.throw(
					_("Line for {0} -> {1} has no Transit Warehouse resolved. Fix the store's Transit Warehouse mapping and regenerate the proposal.").format(
						line.item_code, line.target_warehouse
					)
				)
			line.status = "Approved"
		self.status = "Approved"
		self.save()

	# ------------------------------------------------------------------
	# Step 4 -- FR-21 (updated): creates & submits **Material Requests**
	# (Material Transfer type), DC -> each destination store's Transit
	# Warehouse. This does NOT move stock by itself -- it raises a formal
	# request that DC/store staff action, consistent with the store's
	# transit-then-confirm control process.
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def create_material_requests(self):
		if self.status != "Approved":
			frappe.throw(_("Only an approved run can have Material Requests created."))

		# Group lines by (source, transit warehouse) so each pair becomes
		# one Material Request with multiple item rows.
		groups = {}
		for line in self.proposal_lines:
			if line.status != "Approved":
				continue
			key = (line.source_warehouse, line.transit_warehouse)
			groups.setdefault(key, []).append(line)

		errors = []
		for (source, transit), lines in groups.items():
			try:
				mr = frappe.new_doc("Material Request")
				mr.material_request_type = "Material Transfer"
				mr.company = self.company
				mr.schedule_date = nowdate()
				mr.stock_auto_allocation_run = self.name  # requires custom field, see install.py
				for line in lines:
					mr.append(
						"items",
						{
							"item_code": line.item_code,
							"qty": line.qty,
							"warehouse": transit,  # target of the request
							"from_warehouse": source,
							"schedule_date": nowdate(),
						},
					)
				mr.insert(ignore_permissions=True)
				mr.submit()
				for line in lines:
					line.status = "Requested"
					line.material_request = mr.name
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"Stock Allocation Run {self.name}: MR creation failed")
				errors.append(f"{source} -> {transit}")

		self.status = "Requested"
		self.save()
		if errors:
			frappe.msgprint(
				_("Some Material Requests could not be created and were skipped: {0}. Check the Error Log.").format(
					", ".join(errors)
				),
				indicator="orange",
				alert=True,
			)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _st_window_start():
	"""SRS 6.1: all-time by default; falls back to start of current
	calendar year if enabled in site_config.json (performance fallback).
	Set `"stock_alloc_use_current_year_window": 1` in site_config to enable.
	"""
	if frappe.conf.get("stock_alloc_use_current_year_window"):
		return f"{frappe.utils.nowdate()[:4]}-01-01"
	return None


def _sum_sales_qty(item_codes, warehouse=None, from_date=None, window_start=None):
	if not item_codes:
		return 0.0
	conditions = ["sii.item_code in %(item_codes)s", "si.docstatus = 1"]
	values = {"item_codes": item_codes}
	if warehouse:
		conditions.append("sii.warehouse = %(warehouse)s")
		values["warehouse"] = warehouse
	effective_from = from_date or window_start
	if effective_from:
		conditions.append("si.posting_date >= %(from_date)s")
		values["from_date"] = effective_from

	result = frappe.db.sql(
		f"""
		select sum(sii.stock_qty)
		from `tabSales Invoice Item` sii
		inner join `tabSales Invoice` si on si.name = sii.parent
		where {' and '.join(conditions)}
		""",
		values,
	)
	return flt(result[0][0]) if result and result[0][0] else 0.0


def _sum_store_balance(item_codes, include_transit=False):
	"""Total Balance metric (SRS 3.2): stock on hand across all stores.
	If include_transit is True, also adds stock already in each store's
	Transit Warehouse (mirrors consider_transit_at_target when set on the
	run this is called from).
	"""
	if not item_codes:
		return 0.0
	store_warehouses = _get_store_warehouses()
	if not store_warehouses:
		return 0.0
	warehouses = list(store_warehouses)
	if include_transit:
		transit_warehouses = [t for s in store_warehouses if (t := _resolve_transit_warehouse(s))]
		warehouses += transit_warehouses
	result = frappe.db.sql(
		"""
		select sum(actual_qty)
		from `tabBin`
		where item_code in %(item_codes)s and warehouse in %(warehouses)s
		""",
		{"item_codes": item_codes, "warehouses": warehouses},
	)
	return flt(result[0][0]) if result and result[0][0] else 0.0


def _get_bin_qty(item_code, warehouse):
	qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	return flt(qty) if qty else 0.0


def _get_store_warehouses(company=None):
	# Transit warehouses are excluded even if someone mistakenly also
	# flags them "Is Store" -- they are not a real sales/stock location
	# for ranking or balance purposes.
	filters = {"custom_is_store": 1, "custom_is_transit": 0, "disabled": 0}
	if company:
		filters["company"] = company
	return frappe.get_all("Warehouse", filters=filters, pluck="name")


def _get_nearest_store(destination, store_warehouses):
	"""SRS FR-15: nearest candidate source store for Tier 2, by ascending
	distance in the Store Distance master table. Distance entries are
	stored once per pair (see Store Distance.validate -- symmetrical, no
	reverse duplicate), so this checks both directions. Restricted to
	warehouses that are actual stores (excludes DC/transit); returns None
	if no distance data exists for this destination at all.
	"""
	rows = frappe.db.sql(
		"""
		select from_store, to_store, distance_km
		from `tabStore Distance`
		where from_store = %(dest)s or to_store = %(dest)s
		order by distance_km asc
		""",
		{"dest": destination},
		as_dict=True,
	)
	store_set = set(store_warehouses)
	for row in rows:
		counterpart = row.to_store if row.from_store == destination else row.from_store
		if counterpart != destination and counterpart in store_set:
			return counterpart
	return None


def _resolve_transit_warehouse(store_warehouse):
	"""Same resolution logic as _get_transit_warehouse, but returns None
	instead of throwing when nothing resolves -- used by metrics/required-
	quantity calculations that should degrade gracefully rather than block.
	"""
	explicit = frappe.db.get_value("Warehouse", store_warehouse, "custom_transit_warehouse")
	if explicit:
		return explicit

	candidate = f"T-{store_warehouse}"
	if frappe.db.exists("Warehouse", candidate):
		return candidate

	return None


def _get_transit_warehouse(store_warehouse):
	"""Resolves the Transit Warehouse for a given store warehouse, raising
	a clear error if it can't be resolved -- used when actually creating a
	Material Request, where silently skipping would allocate to nowhere.

	1. Prefers the explicit `custom_transit_warehouse` link set on the
	   Warehouse record.
	2. Falls back to looking for a warehouse literally named
	   "T-<store_warehouse>" -- a straight "T-" prefix on the store's full
	   warehouse name (which already includes the company abbreviation
	   suffix, e.g. "Riyadh Hayat - MA" -> "T-Riyadh Hayat - MA").
	"""
	resolved = _resolve_transit_warehouse(store_warehouse)
	if resolved:
		return resolved

	frappe.throw(
		_(
			"Could not resolve a Transit Warehouse for store {0}. "
			"Set the \"Transit Warehouse\" field on that Warehouse record, "
			"or create a warehouse named \"T-{0}\"."
		).format(store_warehouse)
	)


def _get_effective_stock(item_code, warehouse, include_transit):
	"""A warehouse's own Bin quantity, PLUS whatever is already sitting in
	its Transit Warehouse (goods already on the way in), but ONLY when
	include_transit is True. Callers pass the run's
	consider_transit_at_source / consider_transit_at_target flag for this,
	so the behavior is opt-in per direction, not automatic.
	"""
	total = _get_bin_qty(item_code, warehouse)
	if include_transit:
		transit = _resolve_transit_warehouse(warehouse)
		if transit:
			total += _get_bin_qty(item_code, transit)
	return total


@frappe.whitelist()
def get_item_filter_options(item_year=None, season=None, collection=None, drop=None):
	"""Mutually cascading filter options for the Get Items filters.

	For each of the three Select fields (season, collection, custom_drop),
	returns the distinct values that actually occur among Item Templates
	matching the OTHER currently-selected filters -- so choosing a Season,
	for example, narrows the Collection and Drop dropdowns to only values
	that co-occur with it, and vice versa. item_year is a free-text Data
	field on Item, so it also participates as a filter input but is never
	itself narrowed to a dropdown.

	Called on form load (no filters) and again whenever any filter field
	changes (with the current values of the other fields).
	"""
	base_filters = {"has_variants": 1, "disabled": 0}

	def options_for(target_run_field, target_item_field):
		filters = dict(base_filters)
		if item_year and target_run_field != "item_year":
			filters["item_year"] = item_year
		if season and target_run_field != "season":
			filters["season"] = season
		if collection and target_run_field != "collection":
			filters["collection"] = collection
		if drop and target_run_field != "drop":
			filters["custom_drop"] = drop
		values = frappe.get_all(
			"Item", filters=filters, pluck=target_item_field, distinct=True
		)
		return sorted([v for v in values if v])

	return {
		"season": options_for("season", "season"),
		"collection": options_for("collection", "collection"),
		"drop": options_for("drop", "custom_drop"),
	}
