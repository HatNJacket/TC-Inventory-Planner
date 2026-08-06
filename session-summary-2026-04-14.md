# TC Inventory Planner - Session Summary (April 14, 2026)

## Session Overview
Continued TC Planner development with two major features: vendor website scraping for draft product enrichment, and a full Sales Manager for temporary vendor sale pricing.

## COMPLETED Features

### 1. Vendor Website Scrape Config System (vendor_scraper.py)

**Problem:** Draft product creation was sending raw HTML to the AI, resulting in poor-quality listings that missed key product information.

**Solution:** A vendor-agnostic structured extraction engine with per-vendor JSON configurations.

- **New file: `vendor_scraper.py`** (~890 lines)
  - `VendorScraper` class with extraction methods: `between_markers` (with `use_last` support), `og_and_product_images`, `shopify_product_json`, `link_scan`, `extract_from_description`
  - HTML parsing for key-value spec tables (`<tr><td>`, `<th><td>`, `<dt><dd>` patterns)
  - Bullet list extraction from `<li>` tags
  - Categorized link extraction (manual/firmware/software)
  - Thumbnail filtering (`_looks_like_thumbnail`) — catches Volusion's `-2T.jpg` pattern
  - Image deduplication by base URL (strips query strings)
  - Full-size image detection from `<a href>` wrapper links
  - `format_extraction_for_ai()` — structured context for AI prompts
  - `format_specs_html()` / `format_documents_html()` — renders specs/docs as HTML appended directly to descriptions (not AI-generated)
  - Default configs for 5 vendors: iOptron, Celestron, ZWO, Sky-Watcher, Baader

- **iOptron config** (Volusion platform) — uses stable div IDs from the template:
  - Description: `itemprop='description'>` → `</span>` (unique, captures just the product description)
  - Specs: `ProductDetail_TechSpecs_div` → `</div>` (key_value_table parser extracts `<tr><td><td>` rows)
  - Documents: `ProductDetail_ExtInfo_div` → `</div>` (link_list parser categorizes manual/firmware/software)
  - Features: `ProductDetail_ProductDetails_div2` → `</table>` (bullet_list parser extracts `<li>` items)
  - Images: og:image + `vspfiles/photos` with thumbnail filtering

- **Updated `draft_creator.py`** — integrates vendor_scraper for structured extraction before AI enrichment. Specs table and support documents are appended directly (not left to AI), ensuring accuracy.

- **Database:** `vendor_settings.scrape_config` column (NVARCHAR(MAX))

- **7 API endpoints:** GET/PUT/DELETE scrape config, POST test, GET defaults, POST load-default

- **Frontend: `ScrapeConfigEditor.jsx`** — Visual/JSON/Test tabs embedded in VendorManagementPage via 🔍 button per vendor row

- **Debugging process:** First attempt used tab header text as markers (Description, Technical Specs, Support Documents) but these were grouped together in Volusion's HTML before the content area. Second attempt used `use_last` with tab headers but captured too much layout noise. Final working solution uses Volusion's stable div IDs (`ProductDetail_ProductDetails_div`, `ProductDetail_TechSpecs_div`, `ProductDetail_ExtInfo_div`, `ProductDetail_ProductDetails_div2`) which are unique and reliable.

### 2. Sales Manager (sales_manager.py + SalesManagerPage.jsx)

**Purpose:** Manage temporary vendor sale pricing — upload sale pricelists, preview changes, activate/schedule sales, and auto-revert when sales end.

- **New file: `sales_manager.py`** (~480 lines)
  - Full sale lifecycle: create → upload pricelist → preview → confirm → activate → revert
  - `preview_sale_pricelist()` — parses CSV/XLSX, matches SKUs against velocity cache, converts currencies, computes discounts and margins, detects conflicts with other active/pending sales, flags below-cost items
  - `activate_sale()` — fetches current compareAtPrice, sets price=sale + compareAt=original, adds "On Sale" tag, updates velocity cache
  - `revert_sale()` — restores original prices and compareAt, removes "On Sale" tag, updates velocity cache
  - `exclude_item_from_sale()` — partial revert for individual SKUs, only removes tag if no other items from same product are still on sale
  - `check_scheduled_sales()` — returns pending sales past start_at and active sales past end_at for auto-processing
  - Conflict detection: warns if a SKU is already in another active/pending sale
  - Margin protection: flags sale prices below cost or below 10% margin

- **Database tables:**
  - `vendor_sales` — sale metadata (vendor, name, status, currency, fx_rate, schedule, timestamps)
  - `vendor_sale_items` — individual SKU records with original_price, original_compare_at, sale_price, discount_pct, cost, margin, status

- **Shopify client:** `update_variant_price_with_compare()` — new method using `productVariantsBulkUpdate` with both `price` and `compareAtPrice` fields

- **13 API endpoints:** CRUD + upload + confirm + activate + revert + exclude + check-schedule

- **Frontend: `SalesManagerPage.jsx`** (~546 lines)
  - Dashboard with stats cards (active/pending/completed counts)
  - Sortable sales table with status badges, schedule info
  - Create Sale form (vendor, name, currency, schedule, notes)
  - Sale Detail view: schedule editor, pricelist upload with column mapping, preview table with before/after prices and color-coded warnings, confirmation workflow, active items list with per-SKU Remove for partial revert

- **Azure Function: `check_sale_schedule/`** — timer trigger every 5 minutes, calls `POST /api/sales/check-schedule` to auto-activate/revert scheduled sales

- **On Sale exclusion:** Already in place — line ~1468 of main.py skips "On Sale" tagged products during regular pricelist price updates

## Key Files Modified

### Backend (`C:\tc-planner\backend\app\`)
- `main.py` — +192 lines: 13 sales endpoints, 4 Pydantic models, sales_manager import
- `shopify_client.py` — +39 lines: `update_variant_price_with_compare()` method
- `vendor_scraper.py` — New file, ~890 lines (replaces earlier version with corrected iOptron markers)
- `draft_creator.py` — Replaced: uses vendor_scraper for structured extraction
- `sales_manager.py` — New file, ~480 lines
- `sales_migration.py` — Reference file with SQL + integration code snippets

### Frontend (`C:\tc-planner\frontend\src\`)
- `App.jsx` — +5 lines: SalesManagerPage import, nav item, route
- `api.js` — +71 lines: 12 sales API functions
- `SalesManagerPage.jsx` — New file, ~546 lines
- `ScrapeConfigEditor.jsx` — New file, ~570 lines
- `VendorManagementPage.jsx` — +11 lines: scrape config button and panel

### Azure Functions
- `check_sale_schedule/__init__.py` — Timer trigger for sale scheduling
- `check_sale_schedule/function.json` — Binding config (every 5 minutes)

## Database Changes

### New Tables
- `vendor_sales` — sale metadata with scheduling
- `vendor_sale_items` — SKU-level pricing records

### New Columns
- `vendor_settings.scrape_config` — NVARCHAR(MAX), vendor website scraping config

## Deployment
```powershell
# TC Planner app
cd C:\tc-planner\frontend && npm run build && cd ..
az acr build --registry tcplanneracr --image tc-planner:latest .
az webapp restart --name tc-planner-app --resource-group shopify-automation-rg

# Azure Function (sale scheduler)
cd C:\shopify-automation-func
func azure functionapp publish shopify-automation-func
```

## Known Issues / Pending
- Scrape configs for Celestron, ZWO, Sky-Watcher, Baader have default configs but haven't been tested against live pages yet — iOptron is the only verified config
- Sale scheduling Azure Function needs `httpx` in the function app's requirements.txt
- Sale export CSV feature not yet implemented
- Sale history/audit log not yet implemented (timestamps exist on sale records)
- FIFO matching against Shopify orders still pending (from prior session)
- FreshDesk AI draft responses still to-do
- Custom carrier service for split shipments still to-do
