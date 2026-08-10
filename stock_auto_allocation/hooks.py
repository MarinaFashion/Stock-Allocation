app_name = "stock_auto_allocation"
app_title = "Stock Auto Allocation"
app_publisher = "Your Company"
app_description = "Stock Auto-Allocation Module: sell-through focused DC/store reallocation."
app_email = "dev@yourcompany.com"
app_license = "mit"
app_version = "1.3.1"

required_apps = ["erpnext"]

after_install = "stock_auto_allocation.install.after_install"

override_doctype_class = {
    "Stock Allocation Run": (
        "stock_auto_allocation.stock_auto_allocation.doctype."
        "stock_allocation_run.full_scope_stock_allocation_run."
        "FullScopeStockAllocationRun"
    )
}

doc_events = {
    "Material Request": {
        "on_trash": (
            "stock_auto_allocation.stock_auto_allocation."
            "material_request_events.on_trash"
        )
    }
}
