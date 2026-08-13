frappe.ui.form.on("Material Request", {
    refresh(frm) {
        if (
            frm.doc.shipment_mode === "Consolidated City Transfer" &&
            frm.doc.transfer_shipment_batch
        ) {
            frm.dashboard.set_headline(
                __(
                    "Consolidated City Transfer: combine this transfer with batch {0}. " +
                    "Origin Hub: {1}. Destination Hub: {2}.",
                    [
                        frm.doc.transfer_shipment_batch,
                        frm.doc.origin_hub || "-",
                        frm.doc.destination_hub || "-"
                    ]
                )
            );
        }
    }
});
