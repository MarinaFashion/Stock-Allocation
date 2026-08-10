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
				if (frm.doc[fieldname] && !options.includes(frm.doc[fieldname])) {
					frm.set_value(fieldname, "");
				}
			});
			frm.refresh_fields(["season", "collection", "drop"]);
		},
	});
}

function save_then_call(frm, method, args = {}) {
	const run = () => frm.call(method, args).then((r) => {
		return frm.reload_doc().then(() => r);
	});
	if (frm.is_dirty()) {
		return frm.save().then(run);
	}
	return run();
}

function download_review_workbook(frm) {
	save_then_call(frm, "export_proposal_for_review").then((r) => {
		if (!r.message || !r.message.file_url) return;
		window.open(r.message.file_url);
		frappe.show_alert({
			message: __("Proposal review workbook created for Version {0}.", [r.message.proposal_version]),
			indicator: "green",
		});
	});
}

function upload_review_workbook(frm) {
	new frappe.ui.FileUploader({
		allow_multiple: false,
		restrictions: {
			allowed_file_types: [".xlsx"],
		},
		doctype: frm.doctype,
		docname: frm.docname,
		on_success(file) {
			save_then_call(frm, "import_reviewed_proposal", {
				file_url: file.file_url,
			}).then((r) => {
				if (!r.message) return;
				frappe.msgprint({
					title: __("Proposal Review Imported"),
					indicator: "green",
					message: __(
						"Approved/Adjusted: {0}<br>Adjusted: {1}<br>Rejected: {2}",
						[
							r.message.approved_or_adjusted_lines,
							r.message.adjusted_lines,
							r.message.rejected_lines,
						]
					),
				});
			});
		},
	});
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

	if (frm.doc.status !== "Draft" && frm.doc.status !== "Requested") {
		frm.add_custom_button(__("Start Over"), () => {
			frappe.confirm(
				__("This clears the working list and any generated proposal. Continue?"),
				() => save_then_call(frm, "start_over")
			);
		});
	}

	if (frm.doc.status === "Items Pulled" && (frm.doc.items || []).length) {
		frm.add_custom_button(__("Generate Proposal"), () => {
			save_then_call(frm, "generate_proposal");
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Proposal Generated") {
		frm.add_custom_button(
			__("Download Proposal for Review"),
			() => download_review_workbook(frm),
			__("Excel Review")
		);

		frm.add_custom_button(
			__("Upload Reviewed Proposal"),
			() => upload_review_workbook(frm),
			__("Excel Review")
		);

		frm.add_custom_button(__("Approve"), () => {
			const review_note =
				frm.doc.proposal_review_status === "Reviewed"
					? __("The reviewed quantities will be used when Material Requests are created.")
					: __("No reviewed Excel file is currently applied; the system-proposed quantities will be used.");
			frappe.confirm(
				__("Approve this allocation proposal?<br><br>{0}", [review_note]),
				() => save_then_call(frm, "approve")
			);
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Approved") {
		frm.add_custom_button(__("Approve & Create Material Requests"), () => {
			frappe.confirm(
				__("This creates and submits Material Requests using the final approved quantities. Continue?"),
				() => save_then_call(frm, "create_material_requests")
			);
		}).addClass("btn-primary");
	}

	if (frm.doc.status === "Requested") {
		frm.dashboard.set_headline_alert(
			__("Material Requests have been created. Review the Proposal Lines below for request document names."),
			"green"
		);
	}
}
