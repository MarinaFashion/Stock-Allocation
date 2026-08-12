app_name = "stock_auto_allocation"
app_title = "Stock Auto Allocation"
app_publisher = "Your Company"
app_description = "Stock Auto-Allocation Module: sell-through focused DC/store reallocation."
app_email = "dev@yourcompany.com"
app_license = "mit"
app_version = "1.5.0"

required_apps = ["erpnext"]
after_install = "stock_auto_allocation.install.after_install"

override_doctype_class = {
    "Stock Allocation Run": (
        "stock_auto_allocation.stock_auto_allocation."
        "commercial_stock_allocation_run."
        "CommercialStockAllocationRun"
    )
}

doctype_js = {"Stock Entry": "public/js/stock_entry_allocation_route.js"}

doc_events = {
    "Material Request": {
        "on_trash": (
            "stock_auto_allocation.stock_auto_allocation."
            "material_request_events.on_trash"
        )
    },
    "Stock Entry": {
        "validate": (
            "stock_auto_allocation.stock_auto_allocation."
            "stock_entry_events.preserve_allocation_route"
        )
    },
}
