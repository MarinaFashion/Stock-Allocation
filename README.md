# Stock Auto Allocation

Custom ERPNext v15 app implementing the Stock Auto-Allocation Module described
in the project SRS (`Stock_Auto_Allocation_SRS.docx`).

**v1.1.0 scope:** item selection, all-store/all-time performance metrics,
**Tier 1 (DC → Store) allocation**, and **Tier 2 (Store → Store fallback)**
when the DC can't fully cover a store's need — driven by the Coverage Days
formula, with a mandatory manual approval step before any request is raised.

**What's still not implemented:** the full Grouping/Spreading
store-*selection* algorithms (top-N ranking for Grouping, size round-robin
for Spreading). What v1.1.0 *does* use the per-item **Mode** field for is
narrower: it controls Tier 2's safety-stock behavior at the source store
(SRS FR-16) — see Section 3 below. Tier 1 itself doesn't use Mode at all.

---

## 1. What this app does (v1.1.0)

1. **Get Items** — filter Item Templates by Item Year / Season / Collection /
   Drop, pull matching templates into a working list. Season, Collection and
   Drop are mutually cascading dropdowns (narrowing one narrows the others'
   options to only combinations that actually exist on your Items). Use
   **Start Over** (visible once the run has left Draft, until Material
   Requests are created) to clear the working list and any generated
   proposal if you change filters — `get_items` only appends and skips
   duplicates, so it won't remove stale rows from a previous filter
   selection on its own.
2. **Metrics** — for each template, shows Total Qty, Total Sales, Total
   Balance and ST% (all stores, all-time — see Known Assumptions below).
   `Total Qty = Total Sales + Total Balance`. Total Balance includes stock
   already sitting in each store's Transit Warehouse, not just what's been
   formally received into the store — see Section 4 for why.
3. **Generate Proposal** — for each variant of each included template:
   - **Tier 1 (DC → Store):** ranks store warehouses by sales velocity over
     your chosen Lookback Period, and proposes a transfer quantity per store
     using:
     ```
     Daily Sales Velocity = Sales Qty (Lookback Period) / Lookback Period (days)
     Required Quantity    = MAX(0, Daily Sales Velocity * Coverage Days - Effective Store Stock)
     ```
     `Effective Store Stock` = the store's own on-hand stock **plus**
     whatever's already sitting in its Transit Warehouse — see Section 4.
   - **Tier 2 (Store → Store fallback):** if the DC can't fully cover a
     store's Required Quantity, the app tries the **single nearest store**
     (via the Store Distance table) to cover the remaining shortfall — see
     Section 4 below for the safety-stock rule that governs how much that
     source store is allowed to give up. No splitting across multiple
     source stores in this version; whatever the nearest store can't cover
     is left unfulfilled and flagged in a summary message.

   Every line also resolves the destination store's **Transit Warehouse**
   (see Section 3 below) so you can see exactly where stock will actually be
   requested to before approving. No document is created at this step.
4. **Approve** — locks the proposal for the next step.
5. **Approve & Create Material Requests** — creates and submits ERPNext
   **Material Requests** (Material Transfer type), from the source (DC or
   another store) to each destination store's **Transit Warehouse** — not
   directly to the store. This does not move stock by itself; it raises a
   formal request for staff to action.

---

## 2. Prerequisites

- A working **Frappe bench** with **ERPNext v15** already installed on the
  target site (this app requires ERPNext — see `required_apps` in `hooks.py`).
- Each retail location must exist as an ERPNext **Warehouse**, and each store
  must have a corresponding **Transit Warehouse** already created (see
  Section 3).

---

## 3. The Transit Warehouse model

Per your store control process: stock is never requested directly into a
selling store. It's requested into that store's dedicated **Transit
Warehouse** first; the store manager then inspects it and moves it into the
store themselves (that second leg is a manual step outside this app in
v1.0 — planned as an automatic Stock Entry in a later version, see the
reserved `stock_entry` field on Stock Allocation Proposal Line).

**Resolving the Transit Warehouse for a store**, in order:
1. The store Warehouse's own `custom_transit_warehouse` field, if set
   (recommended — explicit and unambiguous).
2. Otherwise, the app looks for a warehouse literally named
   `T-<store_warehouse>` — a straight "T-" prefix on the store's full
   warehouse name, which already includes the company abbreviation suffix
   (e.g. a store `Riyadh Hayat - MA` resolves to `T-Riyadh Hayat - MA`).

If neither resolves, `generate_proposal` throws a clear error naming the
store and the warehouse name it expected — it will never silently allocate
to the wrong warehouse.

**Setup required per store Warehouse:**
- Tick **Is Store**.
- Either tick **Is Transit Warehouse** on the *transit* warehouse record
  itself (so it's correctly excluded from store ranking/balance
  calculations), and optionally set **Transit Warehouse** on the *store*
  record to link them explicitly.

---

## 4. In-transit stock, Tier 2, the Mode field, and Start Over

### In-transit stock is opt-in, per direction

Stock already sitting in a warehouse's Transit Warehouse (requested but not
yet confirmed into the regular warehouse) is **excluded from every
allocation calculation by default**. Two checkboxes on the Stock Allocation
Run header let you opt in, independently, per run:

| Field | What it does when checked | Why you'd leave it unchecked |
|---|---|---|
| **Add In-Transit Stock to Source Warehouses** | A source (the DC in Tier 1, or another store in Tier 2) can send out stock that's still sitting in its own Transit Warehouse, not yet confirmed received. | Sometimes a source genuinely can't forward stock it hasn't received/verified yet — leave unchecked to only ever send from confirmed stock. |
| **Add In-Transit Stock to Target Warehouses** | A destination store's in-transit stock counts toward its Required Quantity, so it isn't allocated *more* on top of a pending request. | Leave unchecked if you'd rather ignore what's inbound and let the store's confirmed stock alone drive the calculation. |

Both default **unchecked** — i.e. the original behavior (in-transit stock
ignored everywhere) unless you turn one or both on. When checked:

```
Effective Stock (that side) = Warehouse's own Bin quantity + its Transit Warehouse's Bin quantity
```

This applies to: DC stock in Tier 1 (source toggle), a destination store's
Required Quantity in Tier 1 (target toggle), a Tier 2 source store's
sendable amount (source toggle, including how it interacts with its own
Spreading safety-stock check), and the Total Balance metric shown in the
working list (target toggle).

### Tier 2 (store-to-store) and the Mode field

Tier 2 only runs for a store/item once Tier 1 has left a shortfall (DC stock
insufficient). When that happens:

1. The app looks up the **single nearest store** to the destination via the
   `Store Distance` master table (ascending `distance_km`). No splitting
   across multiple source stores in this version.
2. If no distance data exists for that destination at all, the shortfall is
   left unfulfilled and reported in a summary message after the proposal is
   generated.
3. **How much the nearest store is allowed to give up depends on the
   destination item's `Mode` field** (set per row in the working list —
   SRS FR-16):
   - **Spreading:** the source store keeps enough to cover its *own*
     Coverage Days target first (`its own Daily Sales Velocity × Coverage
     Days`); only stock above that target is sendable.
   - **Grouping:** safety stock is ignored entirely — the source store can
     be fully depleted.
   - **Blank (not set):** Tier 2 is skipped for that item. Tier 1 (DC)
     allocation still runs and is unaffected — only the fallback step is
     blocked. A summary message lists which items need Mode set before
     regenerating the proposal.

**Important:** Mode here only controls this safety-stock decision. It does
**not** yet drive the full Grouping algorithm (picking the top-N
highest-velocity stores to concentrate stock into) or the full Spreading
algorithm (the per-size round-robin across all stores) described in the
SRS — those remain a future version. In v1.1.0, Tier 1 and Tier 2 both
allocate to *whichever* stores actually have a Required Quantity shortfall,
regardless of Mode; Mode only changes how much a Tier 2 source store is
willing to give up.

### Start Over

`get_items()` only appends new matching templates and skips ones already in
the working list — it never removes stale rows if you change a filter after
the fact. The **Start Over** button (next to Get Items, once there's
something to clear) resets the working list and any generated proposal back
to a blank slate, so filter/criteria changes actually take effect on the
next Get Items click. It's disabled once the run has already reached
"Requested" status (real Material Requests exist) — start a new Stock
Allocation Run instead at that point, to keep the audit trail intact.

---

## 5. Install on your bench (local dev / self-hosted)

```bash
# from your bench directory
bench get-app https://github.com/<your-org>/stock_auto_allocation.git
bench --site <your-site> install-app stock_auto_allocation
bench --site <your-site> migrate
```

`install-app` runs `after_install` (see `stock_auto_allocation/install.py`),
which automatically creates:

- Custom fields on **Warehouse**: `custom_is_store` (check),
  `custom_is_distribution_center` (check), `custom_is_transit` (check),
  `custom_transit_warehouse` (link to Warehouse).
- Custom field on **Material Request**: `stock_auto_allocation_run`
  (traceability link back to the run that generated it).
- Custom field on **Stock Entry**: `stock_auto_allocation_run` (reserved for
  a future version's Transit → Store leg).
- The **Stock Allocation Manager** role.
- A **Workspace** ("Stock Auto Allocation") with shortcuts to Stock
  Allocation Run, Store Distance, and Warehouse, plus a placeholder section
  for future reports/dashboards. If the layout isn't quite to your taste,
  open it in the Desk and use **Edit Workspace** to rearrange — the shipped
  JSON is just a starting layout.

**Item filter fields are NOT created by this app.** The Get Items filter
(SRS FR-1) queries these existing fields directly on the Item doctype:

| Filter | Item fieldname used | Fieldtype on Item |
|---|---|---|
| Item Year | `item_year` | Data (free text) |
| Season | `season` | Select |
| Collection | `collection` | Select |
| Drop | `custom_drop` | Select |

The **Season / Collection / Drop** dropdowns on the Stock Allocation Run
form are mutually cascading — populated via `get_item_filter_options()`,
which queries actual Item data filtered by whatever else is currently
selected, not just the field's static option list. This means, for example,
picking a Season narrows Collection/Drop to values that actually co-occur
with it on real Items. **Item Year** stays free text, since it's Data on
Item too.

If any of these fieldnames change on your site, update the filter dict in
`get_items()` **and** `get_item_filter_options()`, both in
`stock_allocation_run.py`.

### After install — manual setup required

1. Open each **Warehouse** record: tick **Is Store** for every retail
   store, **Is Distribution Center** for your DC warehouse(s), and **Is
   Transit Warehouse** for each transit warehouse. Optionally set the
   store's **Transit Warehouse** field explicitly (recommended).
2. Confirm `item_year` / `season` / `collection` / `custom_drop` are
   populated on your Item Templates (they already exist on this site's Item
   doctype — this app only reads them).
3. Assign the **Stock Allocation Manager** role to the relevant user(s).
4. **DC Warehouse** on a new Stock Allocation Run only lists warehouses
   flagged **Is Distribution Center** (filtered client-side).
5. Populate the **Store Distance** doctype with distance (km) between store
   pairs — Tier 2 has nothing to rank candidates with until this exists.
6. Set **Mode** (Grouping/Spreading) on each item row in the working list
   before generating a proposal, if you want Tier 2 fallback to run for
   that item — see Section 4.

---

## 6. Pushing to GitHub & deploying via Frappe Cloud

```bash
cd stock_auto_allocation
git add .
git commit -m "Your message"
git push
```

On **Frappe Cloud**: Dashboard → your bench → **Apps** → **Install App from
GitHub** → point it at this repository/branch → install it on your site the
same way as any other custom app. Frappe Cloud will pull, build assets, and
run `after_install` for you on deploy.

Note: `pyproject.toml` declares `[tool.bench.frappe-dependencies]` for
Frappe/ERPNext `>=15.0.0,<16.0.0` — required for Frappe Cloud to accept the
app.

Tag the release once you've verified it on a test site:

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 7. Known assumptions / simplifications (v1.1.0)

These were flagged during the SRS review as genuinely open, or were
simplified pragmatically to ship an MVP. Revisit before relying on this in
production:

- **Transit stock not counted for the source's own need**: in Tier 2
  Spreading mode, a source store's own Coverage Days target is compared
  only against its own physical stock, not stock it has inbound in its own
  Transit Warehouse. In principle a store with a lot already inbound needs
  less new safety stock reserved — this isn't accounted for yet, so Tier 2
  can be slightly more conservative (send less) than strictly necessary in
  that specific case.

- **ST% all-time window**: true all-time by default. If performance at your
  data volume requires it, add `"stock_alloc_use_current_year_window": 1` to
  `site_config.json` to restrict to the current calendar year (per SRS
  Section 6.1's fallback rule).
- **Grouping/Spreading store-selection algorithms**: not implemented yet.
  Mode currently only controls Tier 2 safety-stock behavior (Section 4) —
  the full top-N Grouping ranking and per-size Spreading round-robin from
  the SRS are a future version.
- **Tier 2 single-source only**: only the nearest store is tried per
  shortfall; no splitting across multiple source stores in this version.
- **Material Request grouping**: proposal lines are grouped into one
  Material Request per (source, transit warehouse) pair, rather than one
  request per line, to avoid flooding the Material Request list. Since
  `source` is part of the grouping key, a Tier 1 (DC-sourced) line and a
  Tier 2 (store-sourced) line for the same destination always end up as
  separate Material Requests, even though they share a target.
- **Transit → Store leg**: not automated. The store manager moves stock
  from Transit into the store manually via standard ERPNext. A future
  version could add a "Confirm Receipt" action that creates that Stock Entry
  automatically — the `stock_entry` field on Stock Allocation Proposal Line
  is reserved for that.
- **Multi-company**: `company` is a required field on the run and is used to
  filter store warehouses; DC warehouse selection is filtered by both
  `Is Distribution Center` and `company` when company is set.

---

## 8. Doctypes in this app

| Doctype | Type | Purpose |
|---|---|---|
| Stock Allocation Run | Document | Header: filters, Lookback Period, Coverage Days, status, working list, proposal |
| Stock Allocation Run Item | Child table | Per-item metrics + Mode (Grouping/Spreading — controls Tier 2 safety stock, see Section 4) |
| Stock Allocation Proposal Line | Child table | Per-movement proposal line (source, target store, transit warehouse, item, qty, Tier "DC"/"Store", status, linked Material Request) |
| Store Distance | Document | Master data: km between store pairs — now actively used by Tier 2 (Section 4) |

Plus a **Workspace** ("Stock Auto Allocation") tying these together with
Warehouse.

---

## 9. License

MIT — see `LICENSE`.
