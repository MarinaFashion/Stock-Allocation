# Full Reallocation Scope Patch

The GitHub integration returned `403 Resource not accessible by integration` for
both branch creation and repository writes. These files are therefore packaged
for manual application.

## Files to copy

1. Copy `stock_auto_allocation/stock_auto_allocation/allocation_engine.py`
   into the same path in the repository.
2. Copy
   `stock_auto_allocation/stock_auto_allocation/doctype/stock_allocation_run/full_scope_stock_allocation_run.py`
   into the same path.
3. Copy the test file into
   `stock_auto_allocation/stock_auto_allocation/tests/test_allocation_engine.py`.

## Required hooks.py change

Add this block to `stock_auto_allocation/hooks.py`:

```python
override_doctype_class = {
    "Stock Allocation Run": (
        "stock_auto_allocation.stock_auto_allocation.doctype."
        "stock_allocation_run.full_scope_stock_allocation_run."
        "FullScopeStockAllocationRun"
    )
}
```

Update `app_version` to `1.2.0`.

## Validation commands

From the app repository:

```bash
python -m compileall stock_auto_allocation
python -m unittest stock_auto_allocation.stock_auto_allocation.tests.test_allocation_engine
```

On a bench test site:

```bash
bench --site <test-site> migrate
bench --site <test-site> clear-cache
bench --site <test-site> run-tests --app stock_auto_allocation
```

Then test a Stock Allocation Run with:
- one item above 100 pieces and blank Mode: it should use Spreading;
- one item below 100 pieces and blank Mode: it should use Grouping;
- explicit Mode values: they should override the 100-piece rule;
- incomplete ranges across stores: proposals should complete selected ranges;
- multiple donor stores: the engine should split sourcing by distance;
- transit toggles on/off;
- Material Request creation after approval.

## Important design choices

- Complete variant ranges have first priority in both modes.
- Spreading maximizes the count of stores with a complete range.
- Grouping targets fewer stores and at least two units per variant per selected
  store where total stock permits.
- Store ranking uses recent style sales, current range completeness, and stock.
- The DC is used before store-to-store transfers.
- Store donors can send only stock above their final target.
- Multiple donors are supported and ordered by configured distance.
- Existing approval, transit warehouse, and Material Request logic is inherited
  from the current doctype implementation.
