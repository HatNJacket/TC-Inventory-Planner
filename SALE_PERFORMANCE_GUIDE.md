# Sale Performance — How-to Guide

This guide covers everything added in parts 1–4: sale-aware FIFO costing,
vendor-sale lot flagging, operating expenses with multi-currency, and the
Sale Performance leaderboard.

The goal of these features is to answer one question:
**Which products actually make us money once every cost is loaded in?**

---

## 1. The Pages

Two pages drive everything:

- **📒 COGS Tracker** — upload purchase invoices, run FIFO matching, see per-order P&L.
- **📊 Sale Performance** — operating expense management + fully-loaded SKU leaderboard.

Both share the same underlying data; you can drive workflows from either page.

---

## 2. Prerequisites (do this once)

### 2a. Set FX rates on the Vendors page

Sale Performance supports CAD, USD, and EUR. Non-CAD invoices and expenses
need a stored rate.

1. Open **🏭 Vendors**.
2. Find the FX rate panel and confirm `USDCAD` and `EURCAD` rates are set.
3. The "offset %" lets you bake in a buffer (e.g. +2% for credit-card markup).
   The "effective rate" is what gets applied.

If a rate is missing, expense saves and invoice imports for that currency
will fail with a clear error — that's intentional, so you don't silently
import bills at a wrong rate.

### 2b. Refresh the velocity cache

The SKU leaderboard pulls product titles and vendor names from the velocity
cache. Make sure it's been refreshed recently (Replenishment page → refresh
button) before using Sale Performance for the first time.

---

## 3. Uploading Purchase Invoices

**Where:** COGS Tracker → "Upload Invoice".

### Step 1 — Pick the file and map columns

Supported: CSV, XLSX, XLS, PDF (text-extractable).

Fill in:
- **Vendor** — the brand name on the invoice (e.g. `Celestron`, `iOptron`).
- **Currency** — CAD, USD, or EUR.
- Pick the file. The detector auto-suggests SKU / Quantity / Cost / Description columns.
- Adjust column mappings if the auto-detection is wrong.

Click **Preview Matches**.

### Step 2 — Review matches and confirm

The preview shows each invoice line with one of:
- **Exact** — vendor SKU literally equals a Shopify SKU.
- **Saved** — a previously confirmed vendor → Shopify SKU mapping was found.
- **Suggested** — fuzzy title match found likely candidates; pick the right one from the dropdown.
- **Unmatched** — no candidate; the line will be skipped on import.

You can override any match. Mappings get saved automatically when you confirm,
so the next invoice from the same vendor matches faster.

Then fill in:
- **Invoice #** — vendor's invoice number (must be unique per vendor).
- **Date** — the invoice date. **This is what FIFO ordering uses.**
- **Vendor sale invoice** ☐ — check this if the entire invoice is a vendor
  promotion. Every lot from the invoice is flagged so margin reports can
  attribute the lower cost basis.

Click **Confirm & Import**.

> **Tip:** If only some lines on an invoice are sale-priced, leave the
> checkbox off and split into two invoices (one regular, one sale) using
> a suffix like `INV-12345-SALE`. The matched_sku list is the same;
> the dates are the same.

---

## 4. Running FIFO Match

**Where:** COGS Tracker → "Run FIFO Match" button.

This is what turns Shopify orders into per-SKU revenue + COGS.

### What it does

For every paid, non-cancelled Shopify order in the date range:
1. For each line item with a SKU, find purchase lots where
   `lot.invoice_date <= order.placed_at` and `quantity_remaining > 0`.
2. Consume oldest lot first (FIFO), splitting across lots if needed.
3. Write one `fifo_journal` row per (order, sku, lot) split with
   revenue (discounted Shopify price), COGS (lot cost), and a
   "vendor sale lot?" flag.
4. If no lot covers a line, write the line to `fifo_unmatched_lines`
   so revenue still shows up in P&L (with zero cost basis).

### How to run it

1. Click **Run FIFO Match**.
2. Pick **From** / **To** dates.
3. Leave **Reprocess** unchecked the first time.
4. Click **Run FIFO Match**.

Result panel shows:
- Orders fetched / processed / skipped (already matched)
- Lot matches written
- Unmatched lines (and their revenue)
- Vendor-sale units consumed
- Matched revenue, COGS, gross profit

### When to use Reprocess

Re-running with the same date range normally skips orders already in the
journal. Check **Reprocess** when:

- You back-loaded an older invoice and need historical orders to consume
  it (otherwise FIFO already wrote zero-cost or unmatched rows for them).
- You corrected lot data and want to redo the FIFO pass.

Reprocess deletes existing journal rows for those orders, restores lot
quantities, and re-runs the matcher.

> **Warning:** Reprocess is irreversible. Run it on a small date range
> first to confirm the result before doing a wide window.

### How often to run it

A good rhythm:
- **Daily-ish** for the trailing week, to keep margins fresh.
- **Once per month-end** with the full month range, after all invoices
  for the month are uploaded.

---

## 5. Per-Order P&L (COGS Tracker)

**Where:** COGS Tracker → "Per-Order P&L" section, between the summary
cards and the Purchase Invoices list.

What you see per order:
- **Order #** / **Date**
- **Units** sold
- **Sale lots** — purple badge showing how many units came from vendor-sale
  lots and what % of the order that is.
- **Revenue / COGS / Gross profit / Margin %**
- **Unmatched** — units and revenue with no cost basis (red when present).

Click any row to expand a per-line breakdown showing every lot that fed each
SKU on that order, with:
- Sale price, unit cost, and `regular_unit_cost_cad` shown crossed-out when
  the lot was a sale lot — so you can see exactly how much cheaper the
  inventory was vs. its regular price.
- Per-line margin %.
- Which invoice each lot came from (vendor + invoice # + date).
- A **SALE** badge per line on sale-lot consumption.
- Any unmatched lines from that order, listed separately with the reason
  (`no_lots` or `partial_lots`).

Date filter at the top of the section limits the orders shown.

---

## 6. Operating Expenses

**Where:** Sale Performance page → "Operating Expenses" panel.

### What counts as an operating expense

Anything that's NOT cost-of-goods. Typical categories:
`payroll, lease, utilities, software, shipping, marketing, insurance,
professional_services, office, other`.

### 6a. Add a single expense

Click **Add Expense**:

- **Date** — when the expense was incurred or the date you want the
  allocator to anchor it to.
- **Category** — pick from the list above.
- **Currency** — CAD, USD, or EUR.
- **Amount** — in the chosen currency. The CAD equivalent updates live
  underneath using the stored FX rate.
- **Description** — free text, e.g. "April payroll".
- **Vendor** — optional, e.g. "ADP" or "Office Landlord Inc.".
- **Period start / end** — optional. Leave blank for one-time costs.
  **Set both for recurring costs that span time** (lease, monthly payroll,
  insurance premium). The allocator splits these by day-overlap.
- **Notes** — anything else.

### 6b. Bulk upload from CSV/XLSX

Click **Bulk Upload**:

1. Drop a file. Detector auto-maps columns where possible.
2. Confirm the four required columns: **Date**, **Category**, **Amount**,
   plus optional **Description**, **Vendor**, **Period start/end**, and
   **Currency**.
3. **Default currency** — used for any row where the currency column is
   blank or the column wasn't supplied at all.
4. Click **Upload**.

The upload pre-resolves one FX rate per distinct non-CAD currency before
inserting anything. If a needed rate is missing, *the whole upload fails*
— no partial state.

### 6c. The expense list

Shows for each row:
- Date / category / description / vendor / period
- **Amount** column — the foreign amount with currency code, and the FX
  rate beneath it for non-CAD entries
- **CAD** column — the canonical CAD amount used by the allocator
- Edit / delete buttons

### 6d. How allocation works

For an analysis window `[start, end]`:

- **Single-day expenses** (no period set): contribute their full amount if
  `expense_date` is inside the window, else zero.
- **Period-spanning expenses**: contribute `amount × overlap_days / period_days`.
  So a $5,400 lease covering April 1 – April 30, when the analysis window
  is April 15 – May 14, contributes `5400 × 16 / 30 = $2,880`.

The total overhead is then split across orders/SKUs using your chosen method.

---

## 7. Sale Performance Leaderboard

**Where:** Sale Performance page (main content).

### Header controls

- **From / To** — date range for the entire page.
- **Vendor** — leave blank for all, or type a vendor name to filter (e.g. `iOptron`).
- **Allocation** — how operating expense overhead is shared across SKUs:
  - **Revenue share** (default) — overhead distributed in proportion to
    each SKU's revenue. Best for retail with mixed price points.
  - **Units share** — overhead distributed evenly per unit sold. Better
    when handling cost is roughly the same per item regardless of price.

### Summary cards

- **SKUs** — distinct SKUs with sales in the window.
- **Revenue** — total Shopify revenue (matched + unmatched lines).
- **COGS (FIFO)** — total cost from FIFO matching.
- **Overhead allocated** — total operating expenses prorated to the window.
- **Fully-loaded profit** — Revenue − COGS − Overhead.
- **Margin %** — Fully-loaded profit / Revenue.

Below the cards: a one-liner showing **overhead allocated by category**
for the window, and a red banner if any revenue couldn't be costed.

### The table

One row per SKU, with:
- **#** — rank in the current sort.
- **SKU / Title / Vendor**.
- **Units** sold.
- **Sale lots** — purple badge showing units that came from vendor-sale
  lots (and the % of the SKU's units).
- **Revenue / COGS / Overhead / Gross profit / Loaded profit / Loaded margin %**.

Sort options:
- **Top profit (fully loaded)** — your moneymakers.
- **Worst profit / loss** — products that lose money once overhead is loaded.
- **Best margin %** — high-percentage products regardless of size.
- **Top revenue** — biggest sellers by gross revenue.
- **Top units** — biggest sellers by volume.

Limit picker controls how many rows show (10 / 25 / 50 / 100).

### Reading it

A few patterns to look for:

- **Sale lots column lights up purple** on a high-margin SKU → vendor
  promotions are doing real work for that product. Buy more on the next
  sale.
- **Top revenue but low loaded margin** → you're moving a lot but barely
  making money on it once overhead is shared. Either raise the price or
  push inventory into higher-margin alternatives.
- **Negative loaded profit on a low-volume SKU** → strong candidate to
  stop carrying.
- **Big unmatched revenue banner** → load older invoices into COGS Tracker
  and reprocess FIFO so those sales get a real cost basis.

---

## 8. Suggested Monthly Close Workflow

A repeatable rhythm that gets you trustworthy numbers:

1. **Upload all vendor invoices for the month** (COGS Tracker → Upload).
   Flag vendor promo invoices as you go.
2. **Add/upload all operating expenses for the month** (Sale Performance
   → Operating Expenses). Use period_start/period_end for recurring costs.
3. **Run FIFO Match** (COGS Tracker) for the month, with **Reprocess
   checked** — this rewrites any zero-cost rows now that all invoices
   are loaded.
4. **Open Sale Performance**, set the date range to the month, eyeball:
   - Total revenue against your Shopify monthly report (should match closely).
   - Fully-loaded margin % against expectations.
   - Unmatched-revenue banner — chase down any leftover unmatched lines.
   - SKU leaderboard "worst" sort — note candidates for delisting.
5. Optional: run with **vendor filter** for each major brand to spot
   brands trending up or down.

---

## 9. Troubleshooting

### "No FX rate stored for USDCAD/EURCAD"

Set the rate on the Vendors page. The expense / invoice flow will succeed
on the next attempt.

### "Invoice <X> already exists for <vendor>"

Duplicate vendor + invoice number. If it's a real duplicate, you're fine.
If you need to re-import (e.g. fixed cost data), delete the original first
on the COGS Tracker invoice list (only works if no FIFO journal entries
reference its lots — otherwise reprocess won't be possible without
manually clearing journal rows).

### "No matched sales in this window"

Either:
- The FIFO matcher hasn't been run for this date range yet — run it.
- Or the window is empty in Shopify — widen the dates.

### Big "unmatched" numbers on the leaderboard or per-order P&L

You sold things before recording purchase invoices for them. Fix:
1. Upload the older invoices.
2. Run FIFO Match with **Reprocess checked** for the date range.

### Margin % doesn't match what I expect

Check the allocation method first — switching from revenue-share to
units-share can swing margin numbers a lot. Then verify your operating
expense list isn't missing big items (payroll for the period is a
common omission).

### Vendor sale lots don't show up after upload

Confirm the lot's invoice date is on or before the order placed_at.
FIFO won't use a lot that didn't exist at order time. If you back-date
the invoice and reprocess, sale lots will start appearing.

---

## 10. What's NOT (yet) handled

A few gaps to be aware of so you don't trust the numbers more than you
should:

- **Refunds aren't subtracted** from revenue. Margin numbers are gross
  of refunds. If you have material refunds, treat the leaderboard as
  directional, not exact.
- **Overhead allocation is window-wide**, not bucketed by month. If your
  overhead is lumpy and your sales are seasonal, run smaller windows
  (a single month) for cleaner numbers.
- **Operating expense categories are fixed** (no custom categories yet).
- **Historical FX rates aren't stored per expense** — bills are
  converted at the rate that's stored when you save them. To lock a
  specific rate on a single bill, edit the entry and the API supports a
  `fx_rate` override (UI for that isn't exposed yet — ask if you need it).
