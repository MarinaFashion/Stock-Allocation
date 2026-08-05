const FILTER_METHOD =
	"stock_auto_allocation.stock_auto_allocation.doctype.stock_allocation_run.stock_allocation_run.get_item_filter_options";

frappe.ui.form.on("Stock Allocation Run", {
	onload(frm) {
		set_dc_warehouse_query(frm);
		refresh_filter_options(frm);
	},
	refresh(frm) {
		set_dc_warehouse_query(frm);
		add_workflow_buttons(frm);
	},
	company(frm) {
		frm.set_value("dc_warehouse", null);
	},
	// Mutual cascading: changing any one filter narrows the others.
	season(frm) {
		refresh_filter_options(frm);
	},
	collection(frm) {
		refresh_filter_options(frm);
	},
	drop(frm) {
		refresh_filter_options(frm);
	},
	item_year(frm) {
		refresh_filter_options(frm);
	},
});

function set_dc_warehouse_query(frm) {
	frm.set_query("dc_warehouse", () => ({
		filters: {
			custom_is_distribution_center: 1,
			...(frm.doc.company ? { company: frm.doc.company } : {}),
		},
	}));
}

function refresh_filter_options(frm) {
	frappe.call({
		method: FILTER_METHOD,
		args: {
			item_year: frm.doc.item_year,
			season: frm.doc.season,
			collection: frm.doc.collection,
			drop: frm.doc.drop,
		},
		callback(r) {
			if (!r.message) return;
			["season", "collection", "drop"].forEach((fieldname) => {
				const options = [""].concat(r.message[fieldname] || []);
				frm.set_df_property(fieldname, "options", options.join("\n"));
				// If the currently selected value is no longer valid given
				// the other filters, clear it rather than leaving a stale
				// selection the dropdown can no longer show.
				if (frm.doc[fieldname] && !options.includes(frm.doc[fieldname])) {
					frm.set_value(fieldname, "");
				}
			});
			frm.refresh_fields(["season", "collection", "drop"]);
		},
	});
}

// Ensures any unsaved edits (filter changes, excluded checkboxes, Lookback
// Period / Coverage Days) are persisted before the server-side method runs
// -- the doctype's own methods save() at the end, so the round trip always
// leaves the document consistent between clicks.
function save_then_call(frm, method) {
	const run = () => frm.call(method).then(() => frm.reload_doc());
	if (frm.is_dirty()) {
		return frm.save().then(run);
	}
	return run();
}

function add_workflow_buttons(frm) {
	if (frm.is_new()) {
		frm.dashboard.set_headline(__("Save the run before pulling items."));
		return;
	}

	if (frm.doc.status === "Draft" || frm.doc.status === "Items Pulled") {
		frm.add_custom_button(__("Get Items"), () => {
			save_then_call(frm, "get_items");
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Items Pulled" && (frm.doc.items || []).length) {
		frm.add_custom_button(__("Generate Proposal"), () => {
			save_then_call(frm, "generate_proposal");
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Proposal Generated") {
		frm.add_custom_button(__("Approve"), () => {
			frappe.confirm(
				__("Approve this allocation proposal? No Material Requests will be created yet — a separate step does that."),
				() => save_then_call(frm, "approve")
			);
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Approved") {
		frm.add_custom_button(__("Approve & Create Material Requests"), () => {
			frappe.confirm(
				__("This will create and submit Material Requests (Material Transfer) from the DC to each store's Transit Warehouse. Continue?"),
				() => save_then_call(frm, "create_material_requests")
			);
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Requested") {
		frm.dashboard.set_headline_alert(
			__("Material Requests have been created. Review the Proposal Lines below for links to each request."),
			"green"
		);
	}
}
