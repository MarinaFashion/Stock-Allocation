app_name = "stock_auto_allocation"
app_title = "Stock Auto Allocation"
app_publisher = "Your Company"
app_description = "Stock Auto-Allocation Module: sell-through focused DC/store reallocation."
app_email = "dev@yourcompany.com"
app_license = "mit"
app_version = "1.6.0"

required_apps = ["erpnext"]
after_install = "stock_auto_allocation.install.after_install"
after_migrate = "stock_auto_allocation.migration.after_migrate"

override_doctype_class = {
    "Stock Allocation Run": (
        "stock_auto_allocation.stock_auto_allocation."
        "logistics_stock_allocation_run."
        "LogisticsStockAllocationRun"
    )
}

doctype_js = {
    "Stock Entry": "public/js/stock_entry_allocation_route.js",
    "Material Request": "public/js/material_request_logistics.js",
}

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
