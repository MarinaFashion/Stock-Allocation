app_name = "stock_auto_allocation"
app_title = "Stock Auto Allocation"
app_publisher = "Your Company"
app_description = "Stock Auto-Allocation Module: DC-to-store and store-to-store allocation based on sales velocity and coverage days."
app_email = "dev@yourcompany.com"
app_license = "mit"
app_version = "1.2.0"

# Requires ERPNext (Stock module: Item, Warehouse, Bin, Stock Entry, Sales Invoice)
required_apps = ["erpnext"]

# Installation
# ------------
after_install = "stock_auto_allocation.install.after_install"

# Includes in <head>
# ------------------
# app_include_css = "/assets/stock_auto_allocation/css/stock_auto_allocation.css"
# app_include_js = "/assets/stock_auto_allocation/js/stock_auto_allocation.js"

# Doctype JS
# ----------
# doctype_js is auto-detected by Frappe for any <doctype>/<doctype>.js file
# placed alongside the doctype's .json definition — no explicit hook needed.

# Fixtures
# --------
# Custom Fields and the Stock Allocation Manager Role are created idempotently
# via after_install (see install.py) rather than static fixtures, so the app
# can be installed cleanly on any ERPNext v15 site without an export step.
override_doctype_class = {
    "Stock Allocation Run": (
        "stock_auto_allocation.stock_auto_allocation.doctype."
        "stock_allocation_run.full_scope_stock_allocation_run."
        "FullScopeStockAllocationRun"
    )
}