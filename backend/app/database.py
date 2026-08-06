"""
TC Inventory Planner - Azure SQL Database
Manages stock orders and caches product/velocity data.
"""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import pyodbc

from .config import config

logger = logging.getLogger(__name__)


# ─── SCHEMA ──────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Stock orders (purchase orders)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'stock_orders')
CREATE TABLE stock_orders (
    id INT IDENTITY(1,1) PRIMARY KEY,
    reference_number INT NOT NULL,
    status NVARCHAR(50) NOT NULL DEFAULT 'open',
    vendor NVARCHAR(200) NOT NULL,
    currency NVARCHAR(10) NOT NULL DEFAULT 'CAD',
    expected_date DATE NULL,
    notes NVARCHAR(MAX) NULL,
    total_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    closed_at DATETIME2 NULL
);

-- Stock order line items
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'stock_order_items')
CREATE TABLE stock_order_items (
    id INT IDENTITY(1,1) PRIMARY KEY,
    stock_order_id INT NOT NULL REFERENCES stock_orders(id),
    product_title NVARCHAR(500) NOT NULL,
    variant_title NVARCHAR(200) NULL,
    sku NVARCHAR(100) NOT NULL,
    barcode NVARCHAR(100) NULL,
    vendor NVARCHAR(200) NULL,
    ordered_qty INT NOT NULL DEFAULT 0,
    received_qty INT NOT NULL DEFAULT 0,
    unit_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
    unit_price DECIMAL(12,2) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- Receipt audit log: one row per receive event (who received how many of a
-- line item, and when). Powers the hover detail on the PO screen's Received
-- column. Kept as an append-only log so partial receipts over several days
-- each show up separately.
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'stock_order_receipts')
CREATE TABLE stock_order_receipts (
    id INT IDENTITY(1,1) PRIMARY KEY,
    stock_order_id INT NOT NULL,
    stock_order_item_id INT NOT NULL,
    sku NVARCHAR(100) NULL,
    received_qty INT NOT NULL,
    received_by NVARCHAR(100) NULL,
    received_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_stock_order_receipts_order')
    CREATE INDEX IX_stock_order_receipts_order ON stock_order_receipts(stock_order_id);

-- Sale tracking on PO line items: when a line is added during a vendor
-- sale window, is_vendor_sale=1 and regular_unit_cost preserves the
-- non-sale cost so the UI can show "was $X, on sale at $Y" and reports
-- can flag savings.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('stock_order_items') AND name = 'is_vendor_sale')
    ALTER TABLE stock_order_items ADD is_vendor_sale BIT NOT NULL DEFAULT 0;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('stock_order_items') AND name = 'regular_unit_cost')
    ALTER TABLE stock_order_items ADD regular_unit_cost DECIMAL(12,2) NULL;

-- Cached product/velocity data (refreshed periodically)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'product_velocity_cache')
CREATE TABLE product_velocity_cache (
    sku NVARCHAR(100) PRIMARY KEY,
    variant_id NVARCHAR(200) NOT NULL,
    product_id NVARCHAR(200) NOT NULL,
    product_title NVARCHAR(500) NOT NULL,
    variant_title NVARCHAR(200) NULL,
    vendor NVARCHAR(200) NOT NULL,
    product_type NVARCHAR(200) NULL,
    image_url NVARCHAR(1000) NULL,
    price DECIMAL(12,2) NOT NULL DEFAULT 0,
    cost DECIMAL(12,2) NOT NULL DEFAULT 0,
    current_stock INT NOT NULL DEFAULT 0,
    barcode NVARCHAR(100) NULL,
    -- Velocity metrics
    total_sold_365d INT NOT NULL DEFAULT 0,
    total_sold_90d INT NOT NULL DEFAULT 0,
    total_sold_30d INT NOT NULL DEFAULT 0,
    avg_daily_velocity DECIMAL(10,4) NOT NULL DEFAULT 0,
    avg_monthly_velocity DECIMAL(10,4) NOT NULL DEFAULT 0,
    seasonal_daily_velocity DECIMAL(10,4) NOT NULL DEFAULT 0,
    seasonal_monthly_velocity DECIMAL(10,4) NOT NULL DEFAULT 0,
    trend_direction NVARCHAR(20) NOT NULL DEFAULT 'stable',
    monthly_sales_json NVARCHAR(500) NULL,
    -- Replenishment
    replenish_qty INT NOT NULL DEFAULT 0,
    lead_time_days INT NOT NULL DEFAULT 14,
    days_of_stock DECIMAL(10,1) NOT NULL DEFAULT 0,
    on_order INT NOT NULL DEFAULT 0,
    forecast_profit DECIMAL(12,2) NOT NULL DEFAULT 0,
    planning_start NVARCHAR(20) NULL,
    planning_end NVARCHAR(20) NULL,
    sells_out_date NVARCHAR(50) NULL,
    -- Vendor-specific metadata
    cost_usd NVARCHAR(50) NULL,
    system_code NVARCHAR(100) NULL,
    tags NVARCHAR(MAX) NULL,
    inventory_policy NVARCHAR(20) NULL,
    -- Metadata
    cached_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- Vendor lead time overrides
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'vendor_settings')
CREATE TABLE vendor_settings (
    vendor NVARCHAR(200) PRIMARY KEY,
    lead_time_days INT NOT NULL DEFAULT 14,
    min_order_value DECIMAL(12,2) NULL,
    notes NVARCHAR(MAX) NULL,
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- Next reference number tracking
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'app_settings')
CREATE TABLE app_settings (
    setting_key NVARCHAR(100) PRIMARY KEY,
    setting_value NVARCHAR(MAX) NOT NULL,
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- Non-replenishable SKUs (persists across cache refreshes)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'non_replenishable_skus')
CREATE TABLE non_replenishable_skus (
    sku NVARCHAR(100) PRIMARY KEY,
    marked_by NVARCHAR(100) NULL,
    marked_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- Initialize next reference number if not exists
IF NOT EXISTS (SELECT 1 FROM app_settings WHERE setting_key = 'next_reference_number')
    INSERT INTO app_settings (setting_key, setting_value)
    VALUES ('next_reference_number', '1000');

-- Create indexes
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_stock_order_items_order_id')
    CREATE INDEX IX_stock_order_items_order_id ON stock_order_items(stock_order_id);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_stock_order_items_sku')
    CREATE INDEX IX_stock_order_items_sku ON stock_order_items(sku);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_stock_orders_status')
    CREATE INDEX IX_stock_orders_status ON stock_orders(status);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_stock_orders_vendor')
    CREATE INDEX IX_stock_orders_vendor ON stock_orders(vendor);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_product_velocity_vendor')
    CREATE INDEX IX_product_velocity_vendor ON product_velocity_cache(vendor);

-- Add new columns to existing tables (safe to run multiple times)
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'cost_usd')
    ALTER TABLE product_velocity_cache ADD cost_usd NVARCHAR(50) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'system_code')
    ALTER TABLE product_velocity_cache ADD system_code NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'tags')
    ALTER TABLE product_velocity_cache ADD tags NVARCHAR(MAX) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'inventory_policy')
    ALTER TABLE product_velocity_cache ADD inventory_policy NVARCHAR(20) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'listed_at')
    ALTER TABLE product_velocity_cache ADD listed_at DATETIME2 NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'days_listed')
    ALTER TABLE product_velocity_cache ADD days_listed INT NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'velocity_window_days')
    ALTER TABLE product_velocity_cache ADD velocity_window_days INT NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'listing_adjusted')
    ALTER TABLE product_velocity_cache ADD listing_adjusted BIT NOT NULL DEFAULT 0;

-- Cached projected demand from the most recent forecast. Storing this lets us
-- recompute replenish_qty on PO changes without re-running the full forecaster
-- (which requires fetching Shopify products and 12 months of orders).
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'projected_demand')
    ALTER TABLE product_velocity_cache ADD projected_demand DECIMAL(12,4) NOT NULL DEFAULT 0;

-- Alternative seasonal-adjusted recommendation, surfaced in brackets on the
-- Replenishment table for reference. Not used to drive replenish_qty.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'seasonal_replenish_qty')
    ALTER TABLE product_velocity_cache ADD seasonal_replenish_qty INT NOT NULL DEFAULT 0;

-- Full gross profit if every replenished unit sells: qty × (price − cost).
-- Companion to forecast_profit (which is pro-rated by the 30d sell-through
-- rate). Surfaced as "Total forecast profit" on the Replenishment table.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('product_velocity_cache') AND name = 'total_forecast_profit')
    ALTER TABLE product_velocity_cache ADD total_forecast_profit DECIMAL(12,2) NOT NULL DEFAULT 0;

-- User-facing comments on POs (separate from `notes` which is used for
-- internal metadata like IP import references)
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('stock_orders') AND name = 'comments')
    ALTER TABLE stock_orders ADD comments NVARCHAR(MAX) NULL;

-- Daily inventory snapshots (overall)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'inventory_snapshots')
CREATE TABLE inventory_snapshots (
    id INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    total_inventory_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    dead_stock_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    slow_stock_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    overstock_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    on_order_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_skus INT NOT NULL DEFAULT 0,
    stocked_skus INT NOT NULL DEFAULT 0,
    revenue_365d DECIMAL(14,2) NOT NULL DEFAULT 0,
    cogs_365d DECIMAL(14,2) NOT NULL DEFAULT 0,
    inventory_turns DECIMAL(8,2) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_inventory_snapshots_date UNIQUE (snapshot_date)
);

-- Daily inventory snapshots (per vendor)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'inventory_snapshots_vendor')
CREATE TABLE inventory_snapshots_vendor (
    id INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    vendor NVARCHAR(200) NOT NULL,
    inventory_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    dead_stock_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    units_in_stock INT NOT NULL DEFAULT 0,
    dead_sku_count INT NOT NULL DEFAULT 0,
    active_sku_count INT NOT NULL DEFAULT 0,
    revenue_365d DECIMAL(14,2) NOT NULL DEFAULT 0,
    capital_efficiency DECIMAL(8,2) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_inv_snapshots_vendor_date UNIQUE (snapshot_date, vendor)
);

-- Competitor price tracking
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'competitor_prices')
CREATE TABLE competitor_prices (
    id INT IDENTITY(1,1) PRIMARY KEY,
    sku NVARCHAR(100) NOT NULL,
    product_title NVARCHAR(500) NOT NULL,
    competitor NVARCHAR(200) NOT NULL,
    competitor_price DECIMAL(12,2) NULL,
    competitor_url NVARCHAR(1000) NULL,
    competitor_in_stock BIT NULL,
    our_price DECIMAL(12,2) NOT NULL DEFAULT 0,
    price_diff DECIMAL(12,2) NULL,
    price_diff_pct DECIMAL(8,2) NULL,
    last_checked DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    notes NVARCHAR(500) NULL,
    CONSTRAINT UQ_competitor_sku UNIQUE (sku, competitor)
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_competitor_prices_sku')
    CREATE INDEX IX_competitor_prices_sku ON competitor_prices(sku);

-- Extend vendor_settings with purchasing configuration
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'invoice_currency')
    ALTER TABLE vendor_settings ADD invoice_currency NVARCHAR(10) NOT NULL DEFAULT 'CAD';

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'enforces_map')
    ALTER TABLE vendor_settings ADD enforces_map BIT NOT NULL DEFAULT 0;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'default_markup_pct')
    ALTER TABLE vendor_settings ADD default_markup_pct DECIMAL(6,2) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'website_url')
    ALTER TABLE vendor_settings ADD website_url NVARCHAR(500) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_filename')
    ALTER TABLE vendor_settings ADD pricelist_filename NVARCHAR(200) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_uploaded_at')
    ALTER TABLE vendor_settings ADD pricelist_uploaded_at DATETIME2 NULL;

-- Vendor-published release date for the pricelist itself (e.g. "Sky-Watcher
-- April 2026 Price List"). Distinct from pricelist_uploaded_at which is
-- when WE imported it. Stored as a DATE so the operator can see at a
-- glance whether ours is stale relative to the supplier's latest issue.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_release_date')
    ALTER TABLE vendor_settings ADD pricelist_release_date DATE NULL;

-- Per-vendor flag: when ON, the receive-stock flow auto-generates and
-- prints a ZPL barcode label per unit received via the local label
-- agent (see ``pending_label_jobs`` below). Used for vendors whose
-- products arrive without scannable barcodes — set to OFF for vendors
-- who already ship with acceptable UPC stickers.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'requires_barcode_labels')
    ALTER TABLE vendor_settings ADD requires_barcode_labels BIT NOT NULL DEFAULT 0;

-- ─── PENDING LABEL JOBS QUEUE ────────────────────────────────────
-- Cloud-side queue for barcode label print jobs. Created by the
-- receive-stock flow; consumed by a local Python agent running on the
-- receiving PC that polls /api/labels/pending, prints via Browser
-- Print on its own localhost (which is unaffected by Chrome's Local
-- Network Access restriction since the agent isn't a webpage), and
-- POSTs back to /api/labels/{id}/printed.
--
-- Status values:
--   pending  — waiting for an agent to claim
--   claimed  — an agent has reserved this job (claimed_at + agent_id set)
--   printed  — agent confirmed successful print
--   failed   — agent reported a permanent failure (see error_message)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'pending_label_jobs')
CREATE TABLE pending_label_jobs (
    id INT IDENTITY(1,1) PRIMARY KEY,
    sku NVARCHAR(100) NOT NULL,
    barcode NVARCHAR(100) NOT NULL,
    qty INT NOT NULL DEFAULT 1,
    stock_order_id INT NULL,         -- where the receive originated, for audit
    status NVARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    claimed_at DATETIME2 NULL,
    claimed_by NVARCHAR(100) NULL,   -- agent identifier (hostname or arbitrary tag)
    printed_at DATETIME2 NULL,
    error_message NVARCHAR(1000) NULL
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_pending_label_jobs_status')
    CREATE INDEX IX_pending_label_jobs_status ON pending_label_jobs(status, created_at);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_csv')
    ALTER TABLE vendor_settings ADD pricelist_csv NVARCHAR(MAX) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_sku_column')
    ALTER TABLE vendor_settings ADD pricelist_sku_column NVARCHAR(100) NULL;

-- Widen supplier_description if it exists at 500
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_pricelist_items') AND name = 'supplier_description' AND max_length = 1000)
    SELECT 1;  -- already widened
IF EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_pricelist_items') AND name = 'supplier_description' AND max_length < 4000)
    ALTER TABLE vendor_pricelist_items ALTER COLUMN supplier_description NVARCHAR(2000) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_cost_column')
    ALTER TABLE vendor_settings ADD pricelist_cost_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_desc_column')
    ALTER TABLE vendor_settings ADD pricelist_desc_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_msrp_column')
    ALTER TABLE vendor_settings ADD pricelist_msrp_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_cost_currency')
    ALTER TABLE vendor_settings ADD pricelist_cost_currency NVARCHAR(10) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_msrp_currency')
    ALTER TABLE vendor_settings ADD pricelist_msrp_currency NVARCHAR(10) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_fallback_msrp_column')
    ALTER TABLE vendor_settings ADD pricelist_fallback_msrp_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_fallback_msrp_currency')
    ALTER TABLE vendor_settings ADD pricelist_fallback_msrp_currency NVARCHAR(10) NULL;

-- Optional secondary SKU column + separator. Used by vendors who split the
-- product identifier across two columns (e.g. Optolong: 'Item's Name'
-- + 'Size'). When set, the matcher concatenates the two columns to form a
-- synthetic supplier SKU and falls back to title-token matching against
-- ``product_velocity_cache.product_title + variant_title``.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_sku_secondary_column')
    ALTER TABLE vendor_settings ADD pricelist_sku_secondary_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_sku_separator')
    ALTER TABLE vendor_settings ADD pricelist_sku_separator NVARCHAR(20) NULL;

-- Vendor pricelist items (uploaded supplier pricelists for comparison)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'vendor_pricelist_items')
CREATE TABLE vendor_pricelist_items (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(200) NOT NULL,
    supplier_sku NVARCHAR(200) NOT NULL,
    supplier_description NVARCHAR(2000) NULL,
    supplier_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
    supplier_currency NVARCHAR(10) NOT NULL DEFAULT 'USD',
    supplier_msrp DECIMAL(12,2) NULL,
    matched_shopify_sku NVARCHAR(100) NULL,
    match_status NVARCHAR(50) NOT NULL DEFAULT 'unmatched',
    uploaded_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_vendor_pricelist UNIQUE (vendor, supplier_sku)
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_vendor_pricelist_vendor')
    CREATE INDEX IX_vendor_pricelist_vendor ON vendor_pricelist_items(vendor);

-- Pricelist enrichment columns (added later):
--   supplier_barcode  — UPC/EAN/etc. from the pricelist, used to populate
--                       the Shopify barcode field on draft creation.
--   supplier_map_cad  — explicit Canadian MAP column from the vendor
--                       pricelist (e.g. Sky-Watcher's "Canada MAP" column).
--                       Used as the draft listing price when present, in
--                       preference to a markup-derived sale price.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_pricelist_items') AND name = 'supplier_barcode')
    ALTER TABLE vendor_pricelist_items ADD supplier_barcode NVARCHAR(50) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_pricelist_items') AND name = 'supplier_map_cad')
    ALTER TABLE vendor_pricelist_items ADD supplier_map_cad DECIMAL(12,2) NULL;

--   supplier_coo  — Country of Origin (ISO-3166 alpha-2, e.g. 'CN') from the
--                   pricelist's COO column. Pushed to Shopify's inventory item
--                   countryCodeOfOrigin via the "Set Country of Origin" action.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_pricelist_items') AND name = 'supplier_coo')
    ALTER TABLE vendor_pricelist_items ADD supplier_coo NVARCHAR(2) NULL;

-- Remember per-vendor pricelist column choices so re-uploads/re-compares
-- don't make the user re-pick columns each time.
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_barcode_column')
    ALTER TABLE vendor_settings ADD pricelist_barcode_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_map_cad_column')
    ALTER TABLE vendor_settings ADD pricelist_map_cad_column NVARCHAR(100) NULL;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('vendor_settings') AND name = 'pricelist_coo_column')
    ALTER TABLE vendor_settings ADD pricelist_coo_column NVARCHAR(100) NULL;

-- ─── VENDOR PROMO PRICING (TEMPORARY COST DISCOUNTS FROM VENDORS) ─
-- Tracks vendor-side promotional pricing windows — costs WE pay during a
-- vendor's promo. Each row is a SKU with a sale cost effective between
-- starts_at and ends_at. The PO + replenishment code consults this table
-- when looking up "what would this cost if I bought it today?", falling
-- back to vendor_pricelist_items.supplier_cost when no active promo exists.
--
-- NOTE: distinct from `vendor_sales` / `vendor_promo_pricing` (Sales Manager),
-- which track CUSTOMER-FACING storefront sales we run. Both concepts had
-- the natural name "vendor sale" — this table covers the inbound side
-- (cost we pay), Sales Manager covers the outbound (price customers pay).
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'vendor_promo_pricing')
CREATE TABLE vendor_promo_pricing (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(200) NOT NULL,
    supplier_sku NVARCHAR(200) NOT NULL,
    sale_cost DECIMAL(12,2) NOT NULL,
    sale_currency NVARCHAR(10) NOT NULL DEFAULT 'USD',
    starts_at DATETIME2 NOT NULL,
    ends_at DATETIME2 NOT NULL,
    source_filename NVARCHAR(500) NULL,
    notes NVARCHAR(1000) NULL,
    uploaded_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_vendor_promo_lookup')
    CREATE INDEX IX_vendor_promo_lookup ON vendor_promo_pricing(vendor, supplier_sku, ends_at);

-- FX rate cache
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'fx_rates')
CREATE TABLE fx_rates (
    currency_pair NVARCHAR(10) PRIMARY KEY,
    rate DECIMAL(12,6) NOT NULL,
    offset_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
    effective_rate DECIMAL(12,6) NOT NULL,
    fetched_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- ─── COGS TRACKING (FIFO) ──────────────────────────────────────

-- Purchase invoices (vendor receipts)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'purchase_invoices')
CREATE TABLE purchase_invoices (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(200) NOT NULL,
    invoice_number NVARCHAR(100) NOT NULL,
    invoice_date DATE NOT NULL,
    currency NVARCHAR(10) NOT NULL DEFAULT 'USD',
    fx_rate DECIMAL(12,6) NOT NULL DEFAULT 1.0,
    total_items INT NOT NULL DEFAULT 0,
    total_cost_cad DECIMAL(14,2) NOT NULL DEFAULT 0,
    notes NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_invoice UNIQUE (vendor, invoice_number)
);

-- Purchase lots (individual SKU line items from invoices — FIFO inventory layers)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'purchase_lots')
CREATE TABLE purchase_lots (
    id INT IDENTITY(1,1) PRIMARY KEY,
    invoice_id INT NOT NULL REFERENCES purchase_invoices(id),
    sku NVARCHAR(100) NOT NULL,
    vendor NVARCHAR(200) NOT NULL,
    quantity_purchased INT NOT NULL,
    quantity_remaining INT NOT NULL,
    unit_cost_foreign DECIMAL(12,2) NOT NULL,
    unit_cost_cad DECIMAL(12,2) NOT NULL,
    invoice_date DATE NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    INDEX IX_lots_sku (sku),
    INDEX IX_lots_date (invoice_date),
    INDEX IX_lots_remaining (sku, quantity_remaining)
);

-- FIFO journal (sales matched to purchase lots)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'fifo_journal')
CREATE TABLE fifo_journal (
    id INT IDENTITY(1,1) PRIMARY KEY,
    order_number NVARCHAR(50) NOT NULL,
    order_date DATE NOT NULL,
    sku NVARCHAR(100) NOT NULL,
    quantity_sold INT NOT NULL,
    sale_price DECIMAL(12,2) NOT NULL,
    lot_id INT NOT NULL REFERENCES purchase_lots(id),
    unit_cost_cad DECIMAL(12,2) NOT NULL,
    total_cogs DECIMAL(14,2) NOT NULL,
    total_revenue DECIMAL(14,2) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    INDEX IX_fifo_order (order_number),
    INDEX IX_fifo_sku (sku),
    INDEX IX_fifo_date (order_date)
);

-- Vendor SKU mappings (vendor SKU → Shopify SKU for invoice matching)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sku_mappings')
CREATE TABLE sku_mappings (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(200) NOT NULL,
    vendor_sku NVARCHAR(200) NOT NULL,
    shopify_sku NVARCHAR(200) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_sku_mapping UNIQUE (vendor, vendor_sku)
);
"""


class Database:
    """Azure SQL Database interface for TC Inventory Planner."""

    def __init__(self):
        self._conn_str = config.azure_sql_connection_string

    def _get_connection(self) -> pyodbc.Connection:
        return pyodbc.connect(self._conn_str)

    def initialize_schema(self):
        """Create tables if they don't exist."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Strip SQL line comments BEFORE splitting on ';'. The schema is
            # executed by naively splitting on ';', which has two failure modes
            # when comments are involved:
            #   1. A ';' inside a '--' comment (e.g. "receive-stock flow; ...")
            #      splits a statement in the middle, leaving raw comment text as
            #      the start of the next chunk -> "Incorrect syntax near ...".
            #   2. Non-ASCII characters in comments (em-dash, minus-sign U+2212,
            #      times U+00D7, arrows, box-drawing) can trip the ODBC text
            #      codec and cause the whole statement to fail silently.
            # Removing comments entirely sidesteps both. There are no '--'
            # sequences inside string literals in SCHEMA_SQL, so stripping from
            # '--' to end-of-line per line is safe.
            cleaned_lines = []
            for line in SCHEMA_SQL.splitlines():
                idx = line.find("--")
                if idx != -1:
                    line = line[:idx]
                cleaned_lines.append(line)
            cleaned_sql = "\n".join(cleaned_lines)
            # Execute each statement separately
            for statement in cleaned_sql.split(";"):
                stmt = statement.strip()
                if stmt:
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        logger.warning(f"Schema statement warning: {e}")
            conn.commit()
            logger.info("Database schema initialized")
        finally:
            conn.close()

    # ─── SKU NORMALIZATION LOOKUP ────────────────────────────────

    def resolve_skus_via_cache(self, input_skus):
        """Map each input SKU to its canonical ``product_velocity_cache.sku``,
        matching case-insensitively and through ``normalize_sku`` so Unicode
        roman numerals (Ⅱ↔II) and quote/prime variants (″↔") align with what
        Shopify actually stores.

        Returns ``{input_sku: canonical_cache_sku}`` for hits only. SKUs with
        no cache row are omitted from the result so callers can still fall
        back to the original SKU.
        """
        if not input_skus:
            return {}
        from .shopify_client import normalize_sku
        # Pre-normalize the inputs, keep an "original" map for return shape.
        normalized_to_original = {}
        for s in input_skus:
            if not s:
                continue
            n = normalize_sku(s).upper()
            if n and n not in normalized_to_original:
                normalized_to_original[n] = s

        if not normalized_to_original:
            return {}

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Pull the cache by SKU prefix-buckets to keep the result set
            # small. We post-filter in Python because SQL Server doesn't
            # have the same Unicode normalization rules.
            cursor.execute("SELECT sku FROM product_velocity_cache")
            results = {}
            for row in cursor.fetchall():
                cache_sku = row[0]
                if not cache_sku:
                    continue
                cache_norm = normalize_sku(cache_sku).upper()
                original = normalized_to_original.get(cache_norm)
                if original and original not in results:
                    results[original] = cache_sku
                    if len(results) == len(normalized_to_original):
                        break
            return results
        finally:
            conn.close()

    # ─── VENDOR SALES (PROMOTIONAL PRICING) ──────────────────────

    def get_active_vendor_sales(self, vendor: str = None, as_of=None):
        """Return all currently-active sale rows. Pass ``vendor`` to scope to
        one vendor. ``as_of`` defaults to UTC now.

        Each returned row is a dict with keys: id, vendor, supplier_sku,
        sale_cost, sale_currency, starts_at, ends_at, source_filename, notes.
        """
        from datetime import datetime, timezone
        if as_of is None:
            as_of = datetime.now(timezone.utc).replace(tzinfo=None)
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if vendor:
                cursor.execute(
                    """
                    SELECT id, vendor, supplier_sku, sale_cost, sale_currency,
                           starts_at, ends_at, source_filename, notes, uploaded_at
                    FROM vendor_promo_pricing
                    WHERE vendor = ? AND starts_at <= ? AND ends_at >= ?
                    ORDER BY supplier_sku
                    """,
                    vendor, as_of, as_of,
                )
            else:
                cursor.execute(
                    """
                    SELECT id, vendor, supplier_sku, sale_cost, sale_currency,
                           starts_at, ends_at, source_filename, notes, uploaded_at
                    FROM vendor_promo_pricing
                    WHERE starts_at <= ? AND ends_at >= ?
                    ORDER BY vendor, supplier_sku
                    """,
                    as_of, as_of,
                )
            rows = cursor.fetchall()
            keys = ['id', 'vendor', 'supplier_sku', 'sale_cost', 'sale_currency',
                    'starts_at', 'ends_at', 'source_filename', 'notes', 'uploaded_at']
            return [dict(zip(keys, r)) for r in rows]
        finally:
            conn.close()

    def list_vendor_sales(self, vendor: str):
        """Return ALL sales for a vendor (active + future + expired), ordered
        most-recently-uploaded first. Used by the management UI."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, vendor, supplier_sku, sale_cost, sale_currency,
                       starts_at, ends_at, source_filename, notes, uploaded_at
                FROM vendor_promo_pricing
                WHERE vendor = ?
                ORDER BY uploaded_at DESC, supplier_sku
                """,
                vendor,
            )
            rows = cursor.fetchall()
            keys = ['id', 'vendor', 'supplier_sku', 'sale_cost', 'sale_currency',
                    'starts_at', 'ends_at', 'source_filename', 'notes', 'uploaded_at']
            return [dict(zip(keys, r)) for r in rows]
        finally:
            conn.close()

    def lookup_active_sale(self, vendor: str, supplier_sku: str, as_of=None):
        """Return the active sale row for a (vendor, supplier_sku) at ``as_of``,
        or None. If multiple active rows exist, the one with the lowest
        sale_cost (best deal for us) wins.
        """
        from datetime import datetime, timezone
        if as_of is None:
            as_of = datetime.now(timezone.utc).replace(tzinfo=None)
        if not vendor or not supplier_sku:
            return None
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT TOP 1 id, vendor, supplier_sku, sale_cost, sale_currency,
                       starts_at, ends_at, source_filename, notes, uploaded_at
                FROM vendor_promo_pricing
                WHERE vendor = ? AND supplier_sku = ?
                  AND starts_at <= ? AND ends_at >= ?
                ORDER BY sale_cost ASC
                """,
                vendor, supplier_sku, as_of, as_of,
            )
            row = cursor.fetchone()
            if not row:
                return None
            keys = ['id', 'vendor', 'supplier_sku', 'sale_cost', 'sale_currency',
                    'starts_at', 'ends_at', 'source_filename', 'notes', 'uploaded_at']
            return dict(zip(keys, row))
        finally:
            conn.close()

    def resolve_effective_cost(self, vendor: str, supplier_sku: str,
                               regular_cost: float = None, as_of=None):
        """Return the effective cost we'd pay TODAY for this SKU.

        If an active sale exists, returns
            {'cost': sale_cost, 'currency': sale_currency, 'source': 'sale',
             'sale_id': <id>, 'regular_cost': <regular_cost>, 'ends_at': <dt>}
        Otherwise returns
            {'cost': regular_cost, 'currency': None, 'source': 'pricelist',
             'sale_id': None, 'regular_cost': regular_cost, 'ends_at': None}

        ``regular_cost`` should be the supplier_cost from vendor_pricelist_items
        in the vendor's invoice currency. Pass it in so the caller can keep
        its own currency-conversion logic intact when no sale applies.
        """
        sale = self.lookup_active_sale(vendor, supplier_sku, as_of=as_of)
        if sale:
            return {
                'cost': float(sale['sale_cost']),
                'currency': sale['sale_currency'],
                'source': 'sale',
                'sale_id': sale['id'],
                'regular_cost': regular_cost,
                'ends_at': sale['ends_at'],
            }
        return {
            'cost': regular_cost,
            'currency': None,
            'source': 'pricelist',
            'sale_id': None,
            'regular_cost': regular_cost,
            'ends_at': None,
        }

    def insert_vendor_sales(self, vendor: str, items: list,
                            source_filename: str = None) -> int:
        """Bulk-insert sale rows. ``items`` is a list of dicts with keys
        supplier_sku, sale_cost, sale_currency, starts_at (datetime),
        ends_at (datetime), notes (optional). Returns rows inserted.

        Existing rows for the same (vendor, supplier_sku, starts_at) are
        DELETED first, so re-uploading the same window is idempotent.
        """
        if not items:
            return 0
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            inserted = 0
            for item in items:
                supplier_sku = (item.get('supplier_sku') or '').strip()
                if not supplier_sku:
                    continue
                cursor.execute(
                    """
                    DELETE FROM vendor_promo_pricing
                    WHERE vendor = ? AND supplier_sku = ? AND starts_at = ?
                    """,
                    vendor, supplier_sku, item['starts_at'],
                )
                cursor.execute(
                    """
                    INSERT INTO vendor_promo_pricing
                    (vendor, supplier_sku, sale_cost, sale_currency,
                     starts_at, ends_at, source_filename, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    vendor, supplier_sku, float(item['sale_cost']),
                    item.get('sale_currency') or 'USD',
                    item['starts_at'], item['ends_at'],
                    source_filename, item.get('notes'),
                )
                inserted += 1
            conn.commit()
            return inserted
        finally:
            conn.close()

    def delete_vendor_sale(self, sale_id: int) -> bool:
        """Delete a single sale row by id. Returns True if a row was deleted."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM vendor_promo_pricing WHERE id = ?", sale_id)
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def build_sale_cost_lookup(self, as_of=None):
        """Return ``{ shopify_sku_upper: {...sale info...} }`` for all SKUs
        currently on a vendor sale.

        Joins vendor_promo_pricing → vendor_pricelist_items via (vendor,
        supplier_sku) to find the matched Shopify SKU, then converts each
        sale cost to CAD using fx_rates. Used by the replenishment forecast
        and PO-line-item-add flows to substitute sale pricing.

        Each value dict carries:
            sale_id, vendor, supplier_sku, shopify_sku,
            sale_cost (foreign), sale_currency,
            sale_cost_cad, fx_rate,
            starts_at, ends_at
        """
        from datetime import datetime, timezone
        if as_of is None:
            as_of = datetime.now(timezone.utc).replace(tzinfo=None)
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Pull active sale rows joined to their matched Shopify SKU
            cursor.execute(
                """
                SELECT vsi.id, vsi.vendor, vsi.supplier_sku,
                       vpi.matched_shopify_sku,
                       vsi.sale_cost, vsi.sale_currency,
                       vsi.starts_at, vsi.ends_at
                FROM vendor_promo_pricing vsi
                LEFT JOIN vendor_pricelist_items vpi
                  ON vpi.vendor = vsi.vendor
                 AND vpi.supplier_sku = vsi.supplier_sku
                WHERE vsi.starts_at <= ? AND vsi.ends_at >= ?
                """,
                as_of, as_of,
            )
            rows = cursor.fetchall()
            if not rows:
                return {}

            # Cache FX rates as we encounter currencies
            fx_cache = {'CAD': 1.0}

            def _fx(currency):
                if currency in fx_cache:
                    return fx_cache[currency]
                pair = f"{currency}CAD"
                cursor.execute(
                    "SELECT effective_rate FROM fx_rates WHERE currency_pair = ?",
                    pair,
                )
                row = cursor.fetchone()
                rate = float(row[0]) if row and row[0] else 1.0
                fx_cache[currency] = rate
                return rate

            lookup = {}
            for r in rows:
                (sale_id, vendor, supplier_sku, shopify_sku,
                 sale_cost, sale_currency, starts_at, ends_at) = r
                if not shopify_sku:
                    # No matched Shopify SKU yet — skip; caller can fall back
                    # to supplier_sku-based lookup if it has one.
                    continue
                fx = _fx(sale_currency or 'USD')
                sale_cost_f = float(sale_cost)
                entry = {
                    'sale_id': sale_id,
                    'vendor': vendor,
                    'supplier_sku': supplier_sku,
                    'shopify_sku': shopify_sku,
                    'sale_cost': sale_cost_f,
                    'sale_currency': sale_currency,
                    'sale_cost_cad': round(sale_cost_f * fx, 4),
                    'fx_rate': fx,
                    'starts_at': starts_at.isoformat() if starts_at else None,
                    'ends_at': ends_at.isoformat() if ends_at else None,
                }
                # Index case-insensitively so Shopify SKU casing differences
                # don't drop matches (same pattern used by waiters / min_stock).
                lookup[shopify_sku.upper()] = entry
            return lookup
        finally:
            conn.close()

    def clear_vendor_sales(self, vendor: str, only_expired: bool = False) -> int:
        """Delete all sales for a vendor. With ``only_expired=True``, only
        clears sales whose ends_at is in the past."""
        from datetime import datetime, timezone
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if only_expired:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                cursor.execute(
                    "DELETE FROM vendor_promo_pricing WHERE vendor = ? AND ends_at < ?",
                    vendor, now,
                )
            else:
                cursor.execute("DELETE FROM vendor_promo_pricing WHERE vendor = ?", vendor)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    # ─── APP SETTINGS ────────────────────────────────────────────

    def get_setting(self, key: str, default=None):
        """Return a setting value, or default if not set."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?", key
            )
            row = cursor.fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    def set_setting(self, key: str, value) -> None:
        """Upsert a setting."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                MERGE app_settings AS target
                USING (SELECT ? AS setting_key, ? AS setting_value) AS src
                ON target.setting_key = src.setting_key
                WHEN MATCHED THEN UPDATE SET setting_value = src.setting_value,
                                             updated_at = GETUTCDATE()
                WHEN NOT MATCHED THEN INSERT (setting_key, setting_value)
                    VALUES (src.setting_key, src.setting_value);
            """, key, str(value))
            conn.commit()
        finally:
            conn.close()

    def get_float_setting(self, key: str, default: float) -> float:
        """Get a setting as a float with a default fallback."""
        raw = self.get_setting(key, None)
        if raw is None:
            return default
        try:
            return float(raw)
        except (ValueError, TypeError):
            return default

    def get_int_setting(self, key: str, default: int) -> int:
        """Get a setting as an int with a default fallback."""
        raw = self.get_setting(key, None)
        if raw is None:
            return default
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return default

    # ─── STOCK ORDERS ────────────────────────────────────────────

    def get_next_reference_number(self) -> int:
        """Get and increment the next stock order reference number.

        Self-healing: if the stored counter has fallen behind the highest
        existing reference_number (e.g. after an IP import that preserved
        higher numeric references), we catch up before returning.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = 'next_reference_number'"
            )
            row = cursor.fetchone()
            stored = int(row[0]) if row else 1000

            # Make sure we're past any existing reference number
            cursor.execute("SELECT ISNULL(MAX(reference_number), 0) FROM stock_orders")
            existing_max = cursor.fetchone()[0] or 0
            ref_num = max(stored, existing_max + 1)

            cursor.execute(
                "UPDATE app_settings SET setting_value = ?, updated_at = GETUTCDATE() "
                "WHERE setting_key = 'next_reference_number'",
                str(ref_num + 1),
            )
            conn.commit()
            return ref_num
        finally:
            conn.close()

    def create_stock_order(
        self,
        vendor: str,
        items: List[Dict],
        expected_date: Optional[str] = None,
        notes: Optional[str] = None,
        comments: Optional[str] = None,
    ) -> Dict:
        """Create a new stock order with line items."""
        ref_num = self.get_next_reference_number()
        total_cost = sum(
            item.get("ordered_qty", 0) * item.get("unit_cost", 0) for item in items
        )

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Insert stock order
            cursor.execute(
                """INSERT INTO stock_orders
                   (reference_number, vendor, expected_date, notes, comments, total_cost)
                   OUTPUT INSERTED.id
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ref_num, vendor, expected_date, notes, comments, total_cost,
            )
            order_id = cursor.fetchone()[0]

            # Insert line items
            for item in items:
                cursor.execute(
                    """INSERT INTO stock_order_items
                       (stock_order_id, product_title, variant_title, sku,
                        barcode, vendor, ordered_qty, unit_cost, unit_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    order_id,
                    item.get("product_title", ""),
                    item.get("variant_title", ""),
                    item.get("sku", ""),
                    item.get("barcode", ""),
                    vendor,
                    item.get("ordered_qty", 0),
                    item.get("unit_cost", 0),
                    item.get("unit_price", 0),
                )

            conn.commit()
            logger.info(f"Created stock order #{ref_num} for {vendor} with {len(items)} items")
            return self.get_stock_order(order_id)
        finally:
            conn.close()

    def get_stock_order(self, order_id: int) -> Optional[Dict]:
        """Get a stock order with its line items.

        Each item is enriched with ``current_stock``, ``total_sold_365d``,
        and ``avg_monthly_velocity`` joined from ``product_velocity_cache``
        so the PO edit screen can show on-hand and movement at a glance.
        Values are NULL when the SKU isn't in the cache (e.g. a manual
        line for a product not yet in Shopify)."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM stock_orders WHERE id = ?", order_id
            )
            row = cursor.fetchone()
            if not row:
                return None

            columns = [desc[0] for desc in cursor.description]
            order = dict(zip(columns, row))

            # We pull pvc.barcode under a separate alias `pvc_barcode` and
            # post-process: prefer it (most current Shopify state) over the
            # stored soi.barcode (snapshot at PO creation). Receive-time
            # label printing reads the resolved `barcode` field.
            cursor.execute(
                """SELECT soi.*,
                          pvc.current_stock AS current_stock,
                          pvc.total_sold_365d AS total_sold_365d,
                          pvc.avg_monthly_velocity AS avg_monthly_velocity,
                          pvc.replenish_qty AS replenish_qty,
                          pvc.seasonal_replenish_qty AS seasonal_replenish_qty,
                          pvc.barcode AS pvc_barcode,
                          pvc.variant_id AS variant_id
                   FROM stock_order_items soi
                   LEFT JOIN product_velocity_cache pvc ON pvc.sku = soi.sku
                   WHERE soi.stock_order_id = ?
                   ORDER BY soi.id""",
                order_id,
            )
            item_cols = [desc[0] for desc in cursor.description]
            rows = []
            for r in cursor.fetchall():
                d = dict(zip(item_cols, r))
                # Decimal → float for clean JSON
                if d.get('avg_monthly_velocity') is not None:
                    d['avg_monthly_velocity'] = float(d['avg_monthly_velocity'])
                if d.get('regular_unit_cost') is not None:
                    d['regular_unit_cost'] = float(d['regular_unit_cost'])
                if d.get('is_vendor_sale') is not None:
                    d['is_vendor_sale'] = bool(d['is_vendor_sale'])
                # Resolve the canonical barcode: prefer the live Shopify
                # value from the velocity cache, fall back to the snapshot
                # stored on the line at PO creation.
                pvc_bc = (d.pop('pvc_barcode', None) or '').strip() if d.get('pvc_barcode') else None
                soi_bc = (d.get('barcode') or '').strip() if d.get('barcode') else None
                d['barcode'] = pvc_bc or soi_bc or None
                rows.append(d)
            order["items"] = rows

            # Attach the receipt history (who/when/how many) per line item so
            # the UI can show it on hover over the Received column.
            try:
                cursor.execute(
                    """SELECT stock_order_item_id, received_qty, received_by, received_at
                       FROM stock_order_receipts
                       WHERE stock_order_id = ?
                       ORDER BY received_at ASC""",
                    order_id,
                )
                receipts_by_item = {}
                for rr in cursor.fetchall():
                    receipts_by_item.setdefault(rr[0], []).append({
                        "received_qty": rr[1],
                        "received_by": rr[2] or "Unknown",
                        "received_at": rr[3].isoformat() if rr[3] else None,
                    })
                for it in rows:
                    it["receipts"] = receipts_by_item.get(it.get("id"), [])
            except Exception as e:
                logger.warning(f"Could not load receipt history for order {order_id}: {e}")
                for it in rows:
                    it["receipts"] = []

            # Surface the vendor's label-printing flag at the order level
            # so the receive flow can decide whether to fire ZPL prints
            # without an extra round-trip to the vendors endpoint.
            try:
                cursor.execute(
                    "SELECT requires_barcode_labels FROM vendor_settings WHERE vendor = ?",
                    order.get("vendor"),
                )
                vrow = cursor.fetchone()
                order["requires_barcode_labels"] = bool(vrow[0]) if vrow and vrow[0] is not None else False
            except Exception:
                order["requires_barcode_labels"] = False

            return order
        finally:
            conn.close()

    # ─── LABEL PRINT QUEUE ───────────────────────────────────────

    def enqueue_label_jobs(self, jobs: List[Dict]) -> int:
        """Insert one row per (sku, barcode, qty) job into ``pending_label_jobs``.
        Each job carries an optional ``stock_order_id`` for audit. Returns
        the number of rows created.

        Each entry is a single row regardless of qty — the agent reads
        ``qty`` and prints that many labels at once. Storing one row per
        receive event (not per label) keeps the queue compact.
        """
        if not jobs:
            return 0
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            count = 0
            for job in jobs:
                sku = (job.get("sku") or "").strip()
                barcode = (job.get("barcode") or "").strip()
                qty = int(job.get("qty") or 1)
                if not sku or not barcode or qty < 1:
                    continue
                cursor.execute(
                    """INSERT INTO pending_label_jobs
                       (sku, barcode, qty, stock_order_id, status)
                       VALUES (?, ?, ?, ?, 'pending')""",
                    sku, barcode, qty, job.get("stock_order_id"),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def claim_pending_label_jobs(self, agent_id: str, limit: int = 25) -> List[Dict]:
        """Atomically claim up to ``limit`` pending label jobs and return
        them. The atomic move from 'pending' → 'claimed' uses an
        UPDATE … OUTPUT pattern so two agents polling concurrently don't
        race on the same rows.

        Stale claims older than 5 minutes are automatically returned to
        the pending pool first — covers the case where an agent crashed
        or lost network mid-print.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Recover stale claims (agent died between claim and ack)
            cursor.execute(
                """UPDATE pending_label_jobs
                      SET status = 'pending', claimed_at = NULL, claimed_by = NULL
                    WHERE status = 'claimed'
                      AND claimed_at < DATEADD(minute, -5, GETUTCDATE())"""
            )

            # Atomic claim: move oldest pending rows to claimed, return them.
            cursor.execute(
                """UPDATE TOP (?) pending_label_jobs
                      SET status = 'claimed',
                          claimed_at = GETUTCDATE(),
                          claimed_by = ?
                   OUTPUT inserted.id, inserted.sku, inserted.barcode,
                          inserted.qty, inserted.stock_order_id,
                          inserted.created_at
                    WHERE status = 'pending'""",
                limit, agent_id,
            )
            cols = [d[0] for d in cursor.description]
            rows = []
            for r in cursor.fetchall():
                d = dict(zip(cols, r))
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                rows.append(d)
            conn.commit()
            # Sort oldest first so labels print in receive order
            rows.sort(key=lambda r: r.get("created_at") or "")
            return rows
        finally:
            conn.close()

    def mark_label_printed(self, job_id: int) -> bool:
        """Mark a claimed job as printed. Returns True if updated."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE pending_label_jobs
                      SET status = 'printed', printed_at = GETUTCDATE(),
                          error_message = NULL
                    WHERE id = ? AND status = 'claimed'""",
                job_id,
            )
            updated = cursor.rowcount
            conn.commit()
            return updated > 0
        finally:
            conn.close()

    def mark_label_failed(self, job_id: int, error: str) -> bool:
        """Mark a claimed job as failed with the given error message.
        Returns True if updated. Failed jobs stay in the table for
        audit; they don't auto-retry. The operator can investigate via
        a future "Failed labels" UI or by SQL inspection."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE pending_label_jobs
                      SET status = 'failed', error_message = ?
                    WHERE id = ? AND status IN ('pending', 'claimed')""",
                (error or "")[:1000], job_id,
            )
            updated = cursor.rowcount
            conn.commit()
            return updated > 0
        finally:
            conn.close()

    def list_stock_orders(
        self,
        status_filter: Optional[str] = None,
        vendor_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict]:
        """List stock orders with summary info.

        Search matches across:
          - reference_number (substring)
          - vendor name
          - comments field
          - line item SKU (so 'ZWO PE200' finds POs containing that SKU)
          - line item product_title
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Aggregate in a subquery so SELECT so.* is safe — adding new
            # columns to stock_orders no longer requires updating GROUP BY.
            query = """
                SELECT so.*,
                    ISNULL(agg.total_ordered, 0) as total_ordered,
                    ISNULL(agg.total_received, 0) as total_received,
                    ISNULL(agg.item_count, 0) as item_count
                FROM stock_orders so
                LEFT JOIN (
                    SELECT stock_order_id,
                           SUM(ordered_qty) AS total_ordered,
                           SUM(received_qty) AS total_received,
                           COUNT(id) AS item_count
                    FROM stock_order_items
                    GROUP BY stock_order_id
                ) agg ON agg.stock_order_id = so.id
                WHERE 1=1
            """
            params = []

            if status_filter == "open":
                query += " AND so.status != 'closed'"
            elif status_filter == "closed":
                query += " AND so.status = 'closed'"
            elif status_filter and status_filter != "all":
                query += " AND so.status = ?"
                params.append(status_filter)

            if vendor_filter:
                query += " AND so.vendor = ?"
                params.append(vendor_filter)

            if search:
                # Match across header fields and line items. The EXISTS subquery
                # keeps things fast on large tables — we only do the join when
                # the user has actually typed something.
                like = f"%{search.strip()}%"
                query += """
                    AND (
                        CAST(so.reference_number AS NVARCHAR(20)) LIKE ?
                        OR so.vendor LIKE ?
                        OR ISNULL(so.comments, '') LIKE ?
                        OR EXISTS (
                            SELECT 1 FROM stock_order_items soi2
                            WHERE soi2.stock_order_id = so.id
                              AND (soi2.sku LIKE ? OR soi2.product_title LIKE ?)
                        )
                    )"""
                params.extend([like, like, like, like, like])

            query += " ORDER BY so.reference_number DESC"

            cursor.execute(query, *params)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def update_stock_order_status(self, order_id: int, status: str) -> Optional[Dict]:
        """Update stock order status."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            extra = ", closed_at = GETUTCDATE()" if status == "closed" else ""
            cursor.execute(
                f"UPDATE stock_orders SET status = ?, updated_at = GETUTCDATE(){extra} "
                f"WHERE id = ?",
                status, order_id,
            )
            conn.commit()
            return self.get_stock_order(order_id)
        finally:
            conn.close()

    def delete_stock_order(self, order_id: int) -> Dict:
        """Permanently delete a stock order and its line items.
        Returns {deleted: True, reference_number: N, items_deleted: N, skus: [...]}
        on success, or {deleted: False, reason: str} if the order doesn't exist.
        The `skus` list lets callers refresh just the affected rows in the
        velocity cache without doing a full Shopify-side refresh."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT reference_number FROM stock_orders WHERE id = ?", order_id
            )
            row = cursor.fetchone()
            if not row:
                return {"deleted": False, "reason": "Stock order not found"}
            ref_num = row[0]

            # Capture SKUs before deletion so we can refresh their cache rows
            cursor.execute(
                "SELECT DISTINCT sku FROM stock_order_items WHERE stock_order_id = ? AND sku IS NOT NULL AND sku != ''",
                order_id,
            )
            affected_skus = [r[0] for r in cursor.fetchall()]

            cursor.execute(
                "SELECT COUNT(*) FROM stock_order_items WHERE stock_order_id = ?",
                order_id,
            )
            item_count = cursor.fetchone()[0] or 0

            cursor.execute(
                "DELETE FROM stock_order_items WHERE stock_order_id = ?", order_id
            )
            cursor.execute("DELETE FROM stock_orders WHERE id = ?", order_id)
            conn.commit()
            return {
                "deleted": True,
                "reference_number": ref_num,
                "items_deleted": item_count,
                "skus": affected_skus,
            }
        finally:
            conn.close()

    def receive_stock_order_items(
        self, order_id: int, received_items: List[Dict],
        received_by: str = None,
    ) -> Optional[Dict]:
        """
        Record received quantities for stock order items.
        received_items: [{"item_id": 1, "received_qty": 5}, ...]

        ``received_by`` (the acting user's name, resolved from their API
        token) is written to the stock_order_receipts audit log along with a
        UTC timestamp, so the PO screen can show who received what and when.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            for item in received_items:
                cursor.execute(
                    """UPDATE stock_order_items
                       SET received_qty = received_qty + ?,
                           updated_at = GETUTCDATE()
                       WHERE id = ? AND stock_order_id = ?""",
                    item["received_qty"], item["item_id"], order_id,
                )
                # Audit row — skip no-op/negative corrections of 0.
                qty = int(item.get("received_qty") or 0)
                if qty:
                    cursor.execute(
                        "SELECT sku FROM stock_order_items WHERE id = ?",
                        item["item_id"],
                    )
                    srow = cursor.fetchone()
                    cursor.execute(
                        """INSERT INTO stock_order_receipts
                           (stock_order_id, stock_order_item_id, sku, received_qty, received_by)
                           VALUES (?, ?, ?, ?, ?)""",
                        order_id, item["item_id"],
                        (srow[0] if srow else None), qty,
                        (received_by or "Unknown"),
                    )

            # Update order status based on received quantities
            cursor.execute(
                """SELECT SUM(ordered_qty) as total_ordered,
                          SUM(received_qty) as total_received
                   FROM stock_order_items
                   WHERE stock_order_id = ?""",
                order_id,
            )
            row = cursor.fetchone()
            if row:
                total_ordered = row[0] or 0
                total_received = row[1] or 0

                if total_received >= total_ordered:
                    new_status = "closed"
                elif total_received > 0:
                    new_status = "partial_received"
                else:
                    new_status = None  # don't change

                if new_status:
                    extra = ", closed_at = GETUTCDATE()" if new_status == "closed" else ""
                    cursor.execute(
                        f"UPDATE stock_orders SET status = ?, updated_at = GETUTCDATE(){extra} "
                        f"WHERE id = ?",
                        new_status, order_id,
                    )

            conn.commit()
            return self.get_stock_order(order_id)
        finally:
            conn.close()

    # ─── ON-ORDER QUANTITIES ─────────────────────────────────────

    def get_on_order_by_sku(self) -> Dict[str, int]:
        """Get total on-order (not yet received) quantity by SKU."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT soi.sku, SUM(soi.ordered_qty - soi.received_qty) as on_order
                   FROM stock_order_items soi
                   JOIN stock_orders so ON soi.stock_order_id = so.id
                   WHERE so.status NOT IN ('closed', 'cancelled')
                     AND soi.ordered_qty > soi.received_qty
                   GROUP BY soi.sku"""
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

    def get_on_order_excluding_order(self, order_id: int, skus: List[str]) -> Dict[str, int]:
        """Outstanding on-order units per SKU across all OPEN POs EXCEPT
        ``order_id``. Powers the PO detail screen's "On order elsewhere" column,
        so the operator can see how many of each line item are already coming in
        on other purchase orders. Mirrors get_on_order_by_sku's definition of
        "on order" (status not closed/cancelled, ordered > received) but scoped
        to the given SKUs and excluding the PO being viewed. Returns {sku: qty}
        for SKUs with a positive outstanding qty elsewhere."""
        clean = [s for s in skus if s]
        if not clean:
            return {}
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(clean))
            cursor.execute(
                f"""SELECT soi.sku, SUM(soi.ordered_qty - soi.received_qty) as on_order
                    FROM stock_order_items soi
                    JOIN stock_orders so ON soi.stock_order_id = so.id
                    WHERE so.status NOT IN ('closed', 'cancelled')
                      AND so.id <> ?
                      AND soi.ordered_qty > soi.received_qty
                      AND soi.sku IN ({placeholders})
                    GROUP BY soi.sku""",
                order_id, *clean,
            )
            result = {}
            for row in cursor.fetchall():
                qty = int(row[1] or 0)
                if qty > 0:
                    result[row[0]] = qty
            return result
        finally:
            conn.close()

    def get_motw_eligible_skus(self) -> List[str]:
        """SKUs that should carry the Shopify 'More on the Way' tag.

        A SKU qualifies when BOTH are true:
          1. It still has units on order (ordered > received on a
             non-closed/cancelled PO), AND
          2. Those incoming units exceed the current backorder hole:
             ``current_stock + on_order > 0``.

        Rule (2) is the backorder carve-out. If customers have already
        back-ordered as many (or more) units than are incoming
        (backorders >= on_order, i.e. current_stock + on_order <= 0), then
        the in-progress PO is fully spoken for — a NEW customer ordering
        now would not get stock from it. In that case the product should
        read 'Back Ordered', not 'More on the Way', so we exclude it.

        ``current_stock`` is the Shopify available quantity cached in
        product_velocity_cache. It is written through on receive (see
        apply_stock_update) and refreshed fully on the periodic Shopify
        sync, so it stays current. SKUs with no cache row are treated as
        0 stock (optimistic: a brand-new on-order product still shows
        'More on the Way').

        Note: the 'More on the Way' tag is product-level in Shopify, but
        on_order / current_stock are per-variant. The Azure tagger maps
        each qualifying SKU to its product and tags the product, so a
        product is tagged when ANY of its variants qualifies.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT t.sku
                FROM (
                    SELECT soi.sku AS sku,
                           SUM(soi.ordered_qty - soi.received_qty) AS on_order
                    FROM stock_order_items soi
                    JOIN stock_orders so ON soi.stock_order_id = so.id
                    WHERE so.status NOT IN ('closed', 'cancelled')
                      AND soi.ordered_qty > soi.received_qty
                    GROUP BY soi.sku
                ) t
                LEFT JOIN product_velocity_cache pvc
                    ON UPPER(pvc.sku) = UPPER(t.sku)
                WHERE (ISNULL(pvc.current_stock, 0) + t.on_order) > 0
                """
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def recalc_on_order_for_skus(self, skus: List[str]) -> int:
        """
        Refresh `on_order`, `replenish_qty`, and `days_of_stock` in the velocity
        cache for a specific list of SKUs. Used after stock-order mutations
        (create / delete / receive / edit) so the Replenishment screen reflects
        the change without requiring a full Shopify-side refresh.

        This recomputes from cached `projected_demand`, `current_stock`, and
        `seasonal_daily_velocity` — values that don't change with PO mutations.
        Status overrides (Discontinued / Replacement Part / DENY+OOS) are
        re-applied so the result matches what a full forecast would produce.

        Returns the number of SKU rows updated.
        """
        if not skus:
            return 0
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Recompute on_order from open stock orders for just these SKUs.
            # Build a parameterized IN clause; pyodbc doesn't support array
            # binding so we splice placeholders manually.
            placeholders = ",".join("?" * len(skus))
            cursor.execute(
                f"""SELECT soi.sku, SUM(soi.ordered_qty - soi.received_qty) as on_order
                    FROM stock_order_items soi
                    JOIN stock_orders so ON soi.stock_order_id = so.id
                    WHERE so.status NOT IN ('closed', 'cancelled')
                      AND soi.ordered_qty > soi.received_qty
                      AND soi.sku IN ({placeholders})
                    GROUP BY soi.sku""",
                *skus,
            )
            on_order_by_sku = {row[0]: int(row[1]) for row in cursor.fetchall()}

            # Pull waiter conversion rate and the waiter map for these SKUs.
            # Cheap because we filter to just the SKUs being recalculated.
            cursor.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = 'waiter_conversion_rate'"
            )
            row = cursor.fetchone()
            try:
                waiter_rate = float(row[0]) if row and row[0] is not None else 0.6
            except (ValueError, TypeError):
                waiter_rate = 0.6

            waiter_count_by_sku: Dict[str, int] = {}
            if waiter_rate > 0:
                # Find the latest snapshot id, then count waiters per SKU among
                # ours. SKUs are stored uppercase in waiter_requests.
                cursor.execute(
                    "SELECT TOP 1 id FROM waiter_snapshots ORDER BY imported_at DESC"
                )
                snap_row = cursor.fetchone()
                if snap_row:
                    snap_id = snap_row[0]
                    upper_skus = [s.upper() for s in skus]
                    sku_placeholders = ",".join("?" * len(upper_skus))
                    cursor.execute(
                        f"""SELECT shopify_sku, COUNT(*) AS cnt
                            FROM waiter_requests
                            WHERE snapshot_id = ?
                              AND shopify_sku IS NOT NULL
                              AND shopify_sku != ''
                              AND UPPER(shopify_sku) IN ({sku_placeholders})
                            GROUP BY shopify_sku""",
                        snap_id, *upper_skus,
                    )
                    waiter_count_by_sku = {row[0].upper(): int(row[1]) for row in cursor.fetchall()}

            # Apply update per SKU. Each gets:
            #   on_order  = recomputed (or 0 if no open POs reference it now)
            #   replenish_qty = max(0, round(projected_demand - current_stock - on_order))
            #     ...with waiter floor applied, then status overrides
            #   days_of_stock = (stock + on_order) / seasonal_daily_velocity
            updated = 0
            for sku in skus:
                new_on_order = on_order_by_sku.get(sku, 0)

                # Pull what we need to recompute
                cursor.execute(
                    """SELECT current_stock, projected_demand, seasonal_daily_velocity,
                              tags, inventory_policy
                       FROM product_velocity_cache WHERE sku = ?""",
                    sku,
                )
                row = cursor.fetchone()
                if not row:
                    continue  # SKU not in cache (e.g. archived product) — skip
                current_stock, projected_demand, seasonal_vel, tags, inv_policy = row
                current_stock = int(current_stock or 0)
                projected_demand = float(projected_demand or 0)
                seasonal_vel = float(seasonal_vel or 0)

                replenish_qty = max(0, round(projected_demand - current_stock - new_on_order))

                # Waiter-driven hard floor — must match forecasting.calculate_replenishment
                wc = waiter_count_by_sku.get(sku.upper(), 0)
                if wc > 0 and waiter_rate > 0:
                    from math import ceil
                    waiter_demand = max(1, ceil(wc * waiter_rate))
                    waiter_floor = max(0, waiter_demand - current_stock - new_on_order)
                    replenish_qty = max(replenish_qty, waiter_floor)

                # Re-apply status overrides — same logic as forecasting.calculate_replenishment
                tags_lower = (tags or '').lower()
                if 'discontinued' in tags_lower:
                    replenish_qty = 0
                elif 'replacement part' in tags_lower:
                    replenish_qty = 0
                elif (inv_policy or '') == 'DENY' and current_stock <= 0:
                    replenish_qty = 0

                # Days of stock at the seasonally-adjusted rate
                if seasonal_vel > 0:
                    days_of_stock = round(max(current_stock + new_on_order, 0) / seasonal_vel, 1)
                else:
                    days_of_stock = 999.0 if current_stock > 0 else 0.0

                cursor.execute(
                    """UPDATE product_velocity_cache
                       SET on_order = ?, replenish_qty = ?, days_of_stock = ?
                       WHERE sku = ?""",
                    new_on_order, replenish_qty, days_of_stock, sku,
                )
                updated += cursor.rowcount

            conn.commit()
            logger.info(f"Recalculated cache for {updated}/{len(skus)} SKU(s) after stock order mutation")
            return updated
        finally:
            conn.close()

    # ─── VELOCITY CACHE ──────────────────────────────────────────

    def cache_velocity_data(self, recommendations: List) -> None:
        """Cache replenishment data for fast retrieval using batch insert."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Truncate and re-insert is much faster than 4000+ individual MERGEs
            cursor.execute("DELETE FROM product_velocity_cache")
            cursor.fast_executemany = True

            insert_sql = """INSERT INTO product_velocity_cache (
                sku, variant_id, product_id, product_title,
                variant_title, vendor, product_type, image_url,
                price, cost, current_stock, barcode,
                total_sold_365d, total_sold_90d, total_sold_30d,
                avg_daily_velocity, avg_monthly_velocity,
                seasonal_daily_velocity, seasonal_monthly_velocity,
                trend_direction, monthly_sales_json,
                replenish_qty, lead_time_days,
                days_of_stock, on_order, forecast_profit,
                planning_start, planning_end, sells_out_date,
                cost_usd, system_code, tags, inventory_policy,
                listed_at, days_listed, velocity_window_days, listing_adjusted,
                projected_demand, seasonal_replenish_qty,
                total_forecast_profit
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?
            )"""

            # Build rows, skipping duplicates by SKU
            seen_skus = set()
            rows = []
            for rec in recommendations:
                if not rec.sku or rec.sku in seen_skus:
                    continue
                seen_skus.add(rec.sku)
                monthly_json = json.dumps(rec.velocity.monthly_sales)
                # Get listing data; gracefully degrade if old recommendation objects
                listed_at = getattr(rec, 'listed_at', None)
                vel = rec.velocity
                rows.append((
                    rec.sku, rec.variant_id, rec.product_id, rec.product_title,
                    rec.variant_title, rec.vendor, rec.product_type,
                    rec.image_url, rec.price, rec.cost, rec.current_stock,
                    rec.barcode,
                    vel.total_units_sold_365d,
                    vel.total_units_sold_90d,
                    vel.total_units_sold_30d,
                    vel.avg_daily_velocity,
                    vel.avg_monthly_velocity,
                    vel.seasonal_daily_velocity,
                    vel.seasonal_monthly_velocity,
                    vel.trend_direction, monthly_json,
                    rec.replenish_qty, rec.lead_time_days,
                    rec.days_of_stock, rec.on_order,
                    rec.forecast_profit,
                    rec.planning_start, rec.planning_end,
                    rec.sells_out_date,
                    getattr(rec, 'cost_usd', ''),
                    getattr(rec, 'system_code', ''),
                    getattr(rec, 'tags', ''),
                    getattr(rec, 'inventory_policy', 'DENY'),
                    listed_at,
                    getattr(vel, 'days_listed', None),
                    getattr(vel, 'velocity_window_days', 365),
                    1 if getattr(vel, 'listing_adjusted', False) else 0,
                    getattr(rec, 'projected_demand', 0.0),
                    int(getattr(rec, 'seasonal_replenish_qty', 0) or 0),
                    getattr(rec, 'total_forecast_profit', 0.0),
                ))

            # Batch insert in chunks of 500
            chunk_size = 500
            for i in range(0, len(rows), chunk_size):
                chunk = rows[i:i + chunk_size]
                cursor.executemany(insert_sql, chunk)
                logger.info(f"  Inserted {min(i + chunk_size, len(rows))}/{len(rows)} rows")

            conn.commit()
            logger.info(f"Cached {len(rows)} velocity records")
        finally:
            conn.close()

    def get_cached_replenishment(
        self,
        vendor: Optional[str] = None,
        search: Optional[str] = None,
        sort_field: str = "replenish_qty",
        sort_dir: str = "desc",
        page: int = 1,
        per_page: int = 50,
        needs_replenish: bool = False,
        replenishable_only: bool = True,
    ) -> Dict:
        """Get cached replenishment data with filtering and pagination."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            where_clauses = []
            params = []

            if vendor:
                where_clauses.append("pvc.vendor = ?")
                params.append(vendor)

            if search:
                where_clauses.append(
                    "(pvc.product_title LIKE ? OR pvc.sku LIKE ?)"
                )
                params.extend([f"%{search}%", f"%{search}%"])

            if needs_replenish:
                where_clauses.append("pvc.replenish_qty > 0")

            if replenishable_only:
                where_clauses.append("nrs.sku IS NULL")

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            join_sql = "LEFT JOIN non_replenishable_skus nrs ON pvc.sku = nrs.sku"

            # Whitelist sort fields
            allowed_sorts = {
                "replenish_qty", "price", "cost", "current_stock",
                "days_of_stock", "total_sold_365d", "avg_monthly_velocity",
                "seasonal_monthly_velocity", "forecast_profit", "on_order",
                "product_title", "vendor", "margin_pct", "total_cost",
            }
            if sort_field not in allowed_sorts:
                sort_field = "replenish_qty"
            sort_dir = "DESC" if sort_dir.upper() == "DESC" else "ASC"

            # Margin is computed inline so it can be sorted. Use NULLIF to avoid
            # divide-by-zero on rows where price = 0.
            margin_expr = (
                "CASE WHEN pvc.price > 0 "
                "THEN CAST((pvc.price - pvc.cost) / pvc.price * 100 AS DECIMAL(8,2)) "
                "ELSE NULL END AS margin_pct"
            )
            # Computed sort fields map to a SQL expression; plain columns sort by
            # pvc.<field>. margin_pct is also exposed in the SELECT (above) since
            # the client displays it; total_cost (cost × replenish qty) is only
            # needed for ordering — the frontend computes its own display value.
            if sort_field == "margin_pct":
                sort_clause = "margin_pct"
            elif sort_field == "total_cost":
                sort_clause = "(pvc.cost * pvc.replenish_qty)"
            else:
                sort_clause = f"pvc.{sort_field}"

            # When replenish_qty = 0, explain WHY based on the actual forecasting
            # algorithm: replenish_qty = max(0, round(projected_demand - current_stock - on_order))
            # where projected_demand = seasonal_daily_velocity * (lead_time + safety_stock_days).
            #
            # So replenish_qty = 0 when EITHER:
            #   (A) seasonal_daily_velocity = 0 (no usable velocity → demand projects to 0)
            #   (B) current_stock + on_order >= projected_demand (already covered)
            #
            # Plus the listing-level filter: non_replenishable_skus exclusion.
            #
            # We don't know safety_stock_days here (it's in config), but lead_time
            # alone is a usable lower bound for the explanation. The reason text
            # surfaces the actual numbers so the user can sanity-check the math.
            no_replenish_reason_expr = """
                CASE
                    WHEN pvc.replenish_qty > 0 THEN NULL
                    WHEN nrs.sku IS NOT NULL THEN 'Marked non-replenishable'
                    WHEN LOWER(ISNULL(pvc.tags,'')) LIKE '%discontinued%'
                        THEN 'Tagged Discontinued in Shopify'
                    WHEN LOWER(ISNULL(pvc.tags,'')) LIKE '%replacement part%'
                        THEN 'Tagged Replacement Part (order on demand)'
                    WHEN ISNULL(pvc.inventory_policy,'') = 'DENY' AND pvc.current_stock <= 0
                        THEN 'Stop-selling-when-OOS + zero stock (effectively retired)'
                    WHEN pvc.total_sold_365d = 0 AND pvc.days_listed IS NOT NULL AND pvc.days_listed < 30
                        THEN 'Recently listed (' + CAST(pvc.days_listed AS NVARCHAR(10)) + 'd) — too new to forecast'
                    WHEN pvc.total_sold_365d = 0
                        THEN 'No sales in 365d'
                    WHEN pvc.seasonal_daily_velocity <= 0 AND pvc.avg_daily_velocity <= 0
                        THEN 'No velocity (no recent sales)'
                    WHEN pvc.seasonal_daily_velocity <= 0
                        THEN 'Seasonal velocity is 0 this period'
                    WHEN pvc.current_stock + pvc.on_order > 0
                         AND pvc.days_of_stock >= pvc.lead_time_days
                        THEN 'Covered: ' + CAST(CAST(pvc.days_of_stock AS INT) AS NVARCHAR(10))
                             + 'd stock vs ' + CAST(pvc.lead_time_days AS NVARCHAR(10)) + 'd lead time'
                             + CASE WHEN pvc.on_order > 0
                                    THEN ' (incl ' + CAST(pvc.on_order AS NVARCHAR(10)) + ' on order)'
                                    ELSE '' END
                    WHEN pvc.current_stock + pvc.on_order > 0
                        THEN 'Stock + on-order (' + CAST(pvc.current_stock + pvc.on_order AS NVARCHAR(10))
                             + ') covers projected demand'
                    ELSE 'Projected demand rounds to 0 (very low velocity)'
                END AS no_replenish_reason
            """

            # Get total count
            cursor.execute(
                f"SELECT COUNT(*) FROM product_velocity_cache pvc {join_sql} WHERE {where_sql}",
                *params,
            )
            total = cursor.fetchone()[0]

            # Get paginated results
            offset = (page - 1) * per_page
            cursor.execute(
                f"""SELECT pvc.*,
                       CASE WHEN nrs.sku IS NOT NULL THEN 1 ELSE 0 END as is_non_replenishable,
                       {margin_expr},
                       {no_replenish_reason_expr}
                    FROM product_velocity_cache pvc
                    {join_sql}
                    WHERE {where_sql}
                    ORDER BY {sort_clause} {sort_dir}
                    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
                *params, offset, per_page,
            )
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
            # Convert margin_pct from Decimal to float for JSON; serialize datetime
            for item in items:
                if item.get('margin_pct') is not None:
                    item['margin_pct'] = float(item['margin_pct'])
                if item.get('listed_at') is not None:
                    item['listed_at'] = item['listed_at'].isoformat()
                # listing_adjusted is BIT (0/1) — coerce to bool for cleaner JSON
                if 'listing_adjusted' in item and item['listing_adjusted'] is not None:
                    item['listing_adjusted'] = bool(item['listing_adjusted'])
                # Seasonal alt — int passthrough; default 0 if column absent
                # (e.g. cache from before the migration ran).
                item['seasonal_replenish_qty'] = int(item.get('seasonal_replenish_qty') or 0)

            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page,
            }
        finally:
            conn.close()

    def get_distinct_vendors(self) -> List[str]:
        """Get distinct vendor names from cached data."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT vendor FROM product_velocity_cache ORDER BY vendor"
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    # ─── BACKORDERS ──────────────────────────────────────────────

    def get_backorder_items(self) -> List[Dict]:
        """
        Get products where Shopify stock is negative (customer backorders exist)
        and on-order quantity from open stock orders doesn't fully cover it.
        Returns items sorted by severity (most negative first).
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    pvc.sku,
                    pvc.product_title,
                    pvc.variant_title,
                    pvc.vendor,
                    pvc.current_stock,
                    pvc.price,
                    pvc.cost,
                    pvc.barcode,
                    pvc.image_url,
                    ISNULL(oo.on_order, 0) as on_order,
                    pvc.current_stock + ISNULL(oo.on_order, 0) as net_position,
                    pvc.seasonal_monthly_velocity,
                    pvc.total_sold_365d,
                    pvc.cached_at
                FROM product_velocity_cache pvc
                LEFT JOIN (
                    SELECT soi.sku, SUM(soi.ordered_qty - soi.received_qty) as on_order
                    FROM stock_order_items soi
                    JOIN stock_orders so ON soi.stock_order_id = so.id
                    WHERE so.status NOT IN ('closed', 'cancelled')
                      AND soi.ordered_qty > soi.received_qty
                    GROUP BY soi.sku
                ) oo ON pvc.sku = oo.sku
                WHERE pvc.current_stock < 0
                  AND (pvc.current_stock + ISNULL(oo.on_order, 0)) < 0
                ORDER BY pvc.current_stock ASC
            """)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def update_inventory_levels(self, inventory_updates: List[Dict]) -> int:
        """
        Lightweight update of just the current_stock column in the cache.
        inventory_updates: [{"sku": "ABC", "inventory_quantity": 5}, ...]
        Returns number of rows updated.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            count = 0
            # Batch update in groups
            for item in inventory_updates:
                cursor.execute(
                    "UPDATE product_velocity_cache SET current_stock = ?, cached_at = GETUTCDATE() WHERE sku = ?",
                    item["inventory_quantity"], item["sku"],
                )
                count += cursor.rowcount
            conn.commit()
            return count
        finally:
            conn.close()

    # ─── OVERVIEW DASHBOARD ──────────────────────────────────────

    def get_overview_data(self) -> Dict:
        """Get all data needed for the Overview dashboard."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Stock summary
            cursor.execute("""
                SELECT COUNT(*) as tv, SUM(CASE WHEN current_stock > 0 THEN 1 ELSE 0 END) as vis,
                    SUM(CASE WHEN current_stock > 0 THEN current_stock ELSE 0 END) as su,
                    SUM(CASE WHEN current_stock > 0 THEN cost * current_stock ELSE 0 END) as sc,
                    SUM(CASE WHEN current_stock > 0 THEN price * current_stock ELSE 0 END) as sr
                FROM product_velocity_cache
            """)
            row = cursor.fetchone()
            stock = {"variants": row[0] or 0, "in_stock": row[1] or 0, "units": row[2] or 0,
                     "cost": round(row[3] or 0, 2), "retail": round(row[4] or 0, 2)}

            cursor.execute("""SELECT vendor, SUM(price * current_stock) as r FROM product_velocity_cache
                WHERE current_stock > 0 GROUP BY vendor ORDER BY r DESC""")
            stock["vendors"] = [{"vendor": r[0], "value": round(r[1], 2)} for r in cursor.fetchall()]

            # Purchase orders
            cursor.execute("""
                SELECT COUNT(*) as active,
                    SUM(CASE WHEN expected_date < GETDATE() AND status != 'closed' THEN 1 ELSE 0 END) as overdue
                FROM stock_orders WHERE status != 'closed'
            """)
            row = cursor.fetchone()
            purchases = {"active": row[0] or 0, "overdue": row[1] or 0}

            cursor.execute("""
                SELECT SUM(soi.ordered_qty - soi.received_qty) as units,
                    SUM((soi.ordered_qty - soi.received_qty) * soi.unit_cost) as cost
                FROM stock_order_items soi JOIN stock_orders so ON soi.stock_order_id = so.id
                WHERE so.status != 'closed' AND soi.ordered_qty > soi.received_qty
            """)
            row = cursor.fetchone()
            purchases["on_order_units"] = row[0] or 0
            purchases["on_order_cost"] = round(row[1] or 0, 2)

            cursor.execute("""
                SELECT so.vendor, SUM((soi.ordered_qty - soi.received_qty) * soi.unit_cost) as cost
                FROM stock_order_items soi JOIN stock_orders so ON soi.stock_order_id = so.id
                WHERE so.status != 'closed' AND soi.ordered_qty > soi.received_qty
                GROUP BY so.vendor ORDER BY cost DESC
            """)
            purchases["vendors"] = [{"vendor": r[0], "value": round(r[1], 2)} for r in cursor.fetchall()]

            # Top replenish
            cursor.execute("""SELECT TOP 5 product_title, current_stock, replenish_qty,
                cost * replenish_qty as rc, price * replenish_qty as rr, planning_start, planning_end
                FROM product_velocity_cache WHERE replenish_qty > 0 ORDER BY price * replenish_qty DESC""")
            top_replenish = [{"title": r[0], "stock": r[1], "replenish": r[2],
                              "cost": round(r[3], 2), "retail": round(r[4], 2),
                              "planning_start": r[5], "planning_end": r[6]} for r in cursor.fetchall()]

            # Replenishment totals
            cursor.execute("""SELECT SUM(replenish_qty), SUM(cost * replenish_qty), SUM(price * replenish_qty)
                FROM product_velocity_cache WHERE replenish_qty > 0""")
            row = cursor.fetchone()
            replenishment = {"units": row[0] or 0, "cost": round(row[1] or 0, 2), "retail": round(row[2] or 0, 2)}

            # Overstock (days_of_stock > 180)
            cursor.execute("""SELECT COUNT(*), SUM(current_stock), SUM(cost * current_stock), SUM(price * current_stock)
                FROM product_velocity_cache WHERE current_stock > 0 AND days_of_stock > 180""")
            row = cursor.fetchone()
            overstock = {"variants": row[0] or 0, "units": row[1] or 0,
                         "cost": round(row[2] or 0, 2), "retail": round(row[3] or 0, 2)}

            cursor.execute("""SELECT TOP 5 product_title, current_stock, days_of_stock,
                cost * current_stock as oc, price * current_stock as or2
                FROM product_velocity_cache WHERE current_stock > 0 AND days_of_stock > 180
                ORDER BY price * current_stock DESC""")
            top_overstock = [{"title": r[0], "stock": r[1], "days": round(r[2], 0),
                              "cost": round(r[3], 2), "retail": round(r[4], 2)} for r in cursor.fetchall()]

            # Best sellers
            cursor.execute("""SELECT TOP 5 product_title, total_sold_365d,
                cost * total_sold_365d as cogs, price * total_sold_365d as rev
                FROM product_velocity_cache WHERE total_sold_365d > 0
                ORDER BY price * total_sold_365d DESC""")
            best_sellers = [{"title": r[0], "sales": r[1], "cogs": round(r[2], 2),
                             "revenue": round(r[3], 2)} for r in cursor.fetchall()]

            # Sales KPIs
            cursor.execute("""SELECT SUM(total_sold_365d), SUM(cost * total_sold_365d), SUM(price * total_sold_365d)
                FROM product_velocity_cache""")
            row = cursor.fetchone()
            sales = {"units_sold": row[0] or 0, "cogs": round(row[1] or 0, 2), "revenue": round(row[2] or 0, 2)}

            # Forecast
            cursor.execute("""SELECT SUM(seasonal_monthly_velocity * 12), SUM(price * seasonal_monthly_velocity * 12)
                FROM product_velocity_cache WHERE seasonal_monthly_velocity > 0""")
            row = cursor.fetchone()
            forecast = {"sales": round(row[0] or 0), "revenue": round(row[1] or 0, 2)}

            return {"stock": stock, "purchases": purchases, "top_replenish": top_replenish,
                    "replenishment": replenishment, "overstock": overstock, "top_overstock": top_overstock,
                    "best_sellers": best_sellers, "sales": sales, "forecast": forecast}
        finally:
            conn.close()

    # ─── REPLENISHABLE TOGGLE ────────────────────────────────────

    def set_non_replenishable(self, skus: List[str]) -> int:
        """Mark SKUs as non-replenishable."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            count = 0
            for sku in skus:
                cursor.execute(
                    """IF NOT EXISTS (SELECT 1 FROM non_replenishable_skus WHERE sku = ?)
                       INSERT INTO non_replenishable_skus (sku) VALUES (?)""",
                    sku, sku,
                )
                count += cursor.rowcount
            conn.commit()
            return count
        finally:
            conn.close()

    def set_replenishable(self, skus: List[str]) -> int:
        """Remove SKUs from non-replenishable list (make them replenishable again)."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(skus))
            cursor.execute(
                f"DELETE FROM non_replenishable_skus WHERE sku IN ({placeholders})",
                *skus,
            )
            count = cursor.rowcount
            conn.commit()
            return count
        finally:
            conn.close()

    def get_non_replenishable_skus(self) -> List[str]:
        """Get all non-replenishable SKUs."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT sku FROM non_replenishable_skus ORDER BY sku")
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()


# Singleton
db = Database()
