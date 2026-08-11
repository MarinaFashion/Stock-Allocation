frappe.ui.form.on("Stock Entry", {
    refresh(frm) {
        stock_allocation_apply_mr_route(frm);
    }
});

async function stock_allocation_apply_mr_route(frm) {
    if (!frm.is_new() || !frm.doc.items || !frm.doc.items.length) return;

    const mrNames = [...new Set(frm.doc.items.map(r => r.material_request).filter(Boolean))];
    if (mrNames.length !== 1) return;

    try {
        const mr = await frappe.db.get_doc("Material Request", mrNames[0]);
        if (!mr || !mr.stock_auto_allocation_run) return;

        const itemMap = {};
        (mr.items || []).forEach(r => itemMap[r.name] = r);

        let source = null, target = null, sourceOk = true, targetOk = true;

        for (const row of frm.doc.items) {
            if (row.material_request !== mr.name) continue;
            const mrRow = itemMap[row.material_request_item];
            const s = (mrRow && mrRow.from_warehouse) || mr.set_from_warehouse;
            const t = (mrRow && mrRow.warehouse) || mr.set_warehouse;
            if (!s || !t) continue;

            await frappe.model.set_value(row.doctype, row.name, "s_warehouse", s);
            await frappe.model.set_value(row.doctype, row.name, "t_warehouse", t);

            if (source === null) source = s; else if (source !== s) sourceOk = false;
            if (target === null) target = t; else if (target !== t) targetOk = false;
        }

        if (sourceOk && source) await frm.set_value("from_warehouse", source);
        if (targetOk && target) await frm.set_value("to_warehouse", target);

        if (frm.fields_dict.stock_auto_allocation_run) {
            await frm.set_value("stock_auto_allocation_run", mr.stock_auto_allocation_run);
        }

        frm.refresh_field("items");
    } catch (e) {
        console.warn("Stock Allocation: route preservation failed", e);
    }
}
