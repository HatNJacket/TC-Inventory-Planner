"""
TC Inventory Planner - Analytics Module
Brand scorecards, inventory health, aging analysis, and action lists.
Reads from the product_velocity_cache and stock_orders tables.
"""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _f(val):
    """Convert Decimal/int/None to float for safe arithmetic."""
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


def compute_brand_scorecards(db) -> List[Dict]:
    """
    Compute brand-level scorecards from velocity cache.
    Returns list of brand summaries sorted by inventory value descending.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                vendor,
                COUNT(*) AS total_skus,
                SUM(CASE WHEN total_sold_365d > 0 THEN 1 ELSE 0 END) AS active_skus,
                SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0 THEN 1 ELSE 0 END) AS dead_skus,
                SUM(CASE WHEN current_stock > 0 THEN 1 ELSE 0 END) AS stocked_skus,

                -- Inventory
                SUM(current_stock * cost) AS inventory_value,
                SUM(current_stock) AS total_units,

                -- Sales (365d)
                SUM(total_sold_365d) AS units_sold_365d,
                SUM(total_sold_365d * price) AS revenue_365d,
                SUM(total_sold_365d * cost) AS cogs_365d,

                -- Sales (90d)
                SUM(total_sold_90d) AS units_sold_90d,
                SUM(total_sold_90d * price) AS revenue_90d,

                -- Sales (30d)
                SUM(total_sold_30d) AS units_sold_30d,
                SUM(total_sold_30d * price) AS revenue_30d,

                -- Avg metrics (weighted by stock value)
                AVG(CASE WHEN current_stock > 0 AND avg_daily_velocity > 0
                    THEN days_of_stock ELSE NULL END) AS avg_doi,

                -- On order
                SUM(on_order) AS total_on_order,
                SUM(on_order * cost) AS on_order_value

            FROM product_velocity_cache
            WHERE vendor IS NOT NULL AND vendor != ''
            GROUP BY vendor
            ORDER BY SUM(current_stock * cost) DESC
        """)

        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        scorecards = []
        total_inventory_value = sum(_f(r[columns.index('inventory_value')]) for r in rows)

        for row in rows:
            d = dict(zip(columns, row))

            inv_value = _f(d['inventory_value'])
            revenue = _f(d['revenue_365d'])
            cogs = _f(d['cogs_365d'])
            margin = revenue - cogs
            margin_pct = (margin / revenue * 100) if revenue > 0 else 0

            # Capital efficiency: annual revenue per $ of inventory
            capital_efficiency = (revenue / inv_value) if inv_value > 0 else 0

            # Sell-through rate: units sold / (units sold + current stock)
            units_sold = _f(d['units_sold_365d'])
            total_units = _f(d['total_units'])
            sell_through = (units_sold / (units_sold + total_units) * 100) if (units_sold + total_units) > 0 else 0

            # Inventory share
            inv_share = (inv_value / total_inventory_value * 100) if total_inventory_value > 0 else 0

            # Revenue trend (30d annualized vs 365d)
            revenue_30d_annual = _f(d['revenue_30d']) * 12
            trend = 'growing' if revenue_30d_annual > revenue * 1.1 else (
                'declining' if revenue_30d_annual < revenue * 0.8 else 'stable'
            )

            scorecards.append({
                'vendor': d['vendor'],
                'total_skus': d['total_skus'],
                'active_skus': d['active_skus'],
                'dead_skus': d['dead_skus'],
                'stocked_skus': d['stocked_skus'],

                'inventory_value': round(inv_value, 2),
                'inventory_share_pct': round(inv_share, 1),
                'total_units': int(total_units),

                'revenue_365d': round(revenue, 2),
                'revenue_90d': round(_f(d['revenue_90d']), 2),
                'revenue_30d': round(_f(d['revenue_30d']), 2),
                'cogs_365d': round(cogs, 2),
                'gross_margin': round(margin, 2),
                'gross_margin_pct': round(margin_pct, 1),

                'units_sold_365d': int(units_sold),
                'units_sold_90d': int(_f(d['units_sold_90d'])),
                'units_sold_30d': int(_f(d['units_sold_30d'])),

                'sell_through_pct': round(sell_through, 1),
                'avg_doi': round(_f(d['avg_doi']), 0),
                'capital_efficiency': round(capital_efficiency, 2),

                'on_order_units': int(_f(d['total_on_order'])),
                'on_order_value': round(_f(d['on_order_value']), 2),

                'trend': trend,
            })

        return scorecards
    finally:
        conn.close()


def compute_sku_details(db, vendor: Optional[str] = None, aging_bucket: Optional[str] = None) -> List[Dict]:
    """
    Compute SKU-level detail with aging classification and action recommendations.
    Optionally filter by vendor and/or aging bucket.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        where_clauses = ["vendor IS NOT NULL AND vendor != ''"]
        params = []
        if vendor:
            where_clauses.append("vendor = ?")
            params.append(vendor)

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"""
            SELECT
                sku, variant_id, product_id, product_title, variant_title,
                vendor, product_type, price, cost, current_stock,
                total_sold_365d, total_sold_90d, total_sold_30d,
                avg_daily_velocity, days_of_stock, on_order,
                replenish_qty, trend_direction, monthly_sales_json,
                image_url, barcode
            FROM product_velocity_cache
            WHERE {where_sql}
            ORDER BY (current_stock * cost) DESC
        """, *params)

        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        results = []
        for row in rows:
            d = dict(zip(columns, row))

            stock = int(_f(d['current_stock']))
            cost = _f(d['cost'])
            price = _f(d['price'])
            sold_365 = int(_f(d['total_sold_365d']))
            sold_90 = int(_f(d['total_sold_90d']))
            sold_30 = int(_f(d['total_sold_30d']))
            velocity = _f(d['avg_daily_velocity'])
            doi = _f(d['days_of_stock'])
            inv_value = stock * cost

            # Margin
            margin_per_unit = price - cost
            margin_pct = (margin_per_unit / price * 100) if price > 0 else 0

            # Sell-through
            sell_through = (sold_365 / (sold_365 + stock) * 100) if (sold_365 + stock) > 0 else 0

            # Aging bucket
            if stock == 0:
                bucket = 'no_stock'
            elif sold_30 > 0:
                bucket = '0-30'
            elif sold_90 > 0:
                bucket = '31-90'
            elif sold_365 > 0:
                bucket = '91-365'
            else:
                bucket = '365+'

            # Action recommendation
            action = _recommend_action(stock, sold_365, sold_90, sold_30, doi, margin_pct, velocity)

            # Apply aging filter if requested
            if aging_bucket and bucket != aging_bucket:
                continue

            results.append({
                'sku': d['sku'],
                'product_title': d['product_title'],
                'variant_title': d['variant_title'],
                'vendor': d['vendor'],
                'product_type': d['product_type'],
                'price': round(price, 2),
                'cost': round(cost, 2),
                'margin_per_unit': round(margin_per_unit, 2),
                'margin_pct': round(margin_pct, 1),
                'current_stock': stock,
                'inventory_value': round(inv_value, 2),
                'sold_365d': sold_365,
                'sold_90d': sold_90,
                'sold_30d': sold_30,
                'velocity_daily': round(velocity, 3),
                'doi': round(doi, 0),
                'on_order': int(_f(d['on_order'])),
                'sell_through_pct': round(sell_through, 1),
                'aging_bucket': bucket,
                'action': action,
                'trend': d['trend_direction'],
                'image_url': d['image_url'],
            })

        return results
    finally:
        conn.close()


def compute_inventory_summary(db) -> Dict:
    """
    Compute high-level inventory health summary.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) AS total_skus,
                SUM(CASE WHEN current_stock > 0 THEN 1 ELSE 0 END) AS stocked_skus,
                SUM(current_stock * cost) AS total_inventory_value,
                SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0
                    THEN current_stock * cost ELSE 0 END) AS dead_stock_value,
                SUM(CASE WHEN total_sold_365d > 0 AND total_sold_90d = 0 AND current_stock > 0
                    THEN current_stock * cost ELSE 0 END) AS slow_stock_value,
                SUM(CASE WHEN days_of_stock > 365 AND current_stock > 0
                    THEN current_stock * cost ELSE 0 END) AS overstock_value,
                SUM(CASE WHEN days_of_stock > 0 AND days_of_stock < 14 AND avg_daily_velocity > 0
                    THEN current_stock * cost ELSE 0 END) AS low_stock_value,
                SUM(total_sold_365d * price) AS total_revenue_365d,
                SUM(total_sold_365d * cost) AS total_cogs_365d,
                SUM(total_sold_30d * price) AS total_revenue_30d,
                COUNT(DISTINCT vendor) AS vendor_count,
                SUM(on_order * cost) AS total_on_order_value
            FROM product_velocity_cache
            WHERE vendor IS NOT NULL AND vendor != ''
        """)

        row = cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        d = dict(zip(columns, row))

        total_inv = _f(d['total_inventory_value'])
        revenue = _f(d['total_revenue_365d'])
        cogs = _f(d['total_cogs_365d'])
        dead = _f(d['dead_stock_value'])
        slow = _f(d['slow_stock_value'])

        # Aging buckets
        cursor.execute("""
            SELECT
                SUM(CASE WHEN current_stock > 0 AND total_sold_30d > 0
                    THEN current_stock * cost ELSE 0 END) AS bucket_0_30,
                SUM(CASE WHEN current_stock > 0 AND total_sold_30d = 0 AND total_sold_90d > 0
                    THEN current_stock * cost ELSE 0 END) AS bucket_31_90,
                SUM(CASE WHEN current_stock > 0 AND total_sold_90d = 0 AND total_sold_365d > 0
                    THEN current_stock * cost ELSE 0 END) AS bucket_91_365,
                SUM(CASE WHEN current_stock > 0 AND total_sold_365d = 0
                    THEN current_stock * cost ELSE 0 END) AS bucket_365_plus
            FROM product_velocity_cache
            WHERE vendor IS NOT NULL AND vendor != ''
        """)
        aging_row = cursor.fetchone()
        aging_cols = [desc[0] for desc in cursor.description]
        aging = dict(zip(aging_cols, aging_row))

        return {
            'total_skus': d['total_skus'],
            'stocked_skus': d['stocked_skus'],
            'vendor_count': d['vendor_count'],
            'total_inventory_value': round(total_inv, 2),
            'target_inventory_value': 1000000,  # $1M target
            'reduction_needed': round(max(0, total_inv - 1000000), 2),
            'dead_stock_value': round(dead, 2),
            'dead_stock_pct': round(dead / total_inv * 100, 1) if total_inv > 0 else 0,
            'slow_stock_value': round(slow, 2),
            'overstock_value': round(_f(d['overstock_value']), 2),
            'low_stock_value': round(_f(d['low_stock_value']), 2),
            'on_order_value': round(_f(d['total_on_order_value']), 2),
            'revenue_365d': round(revenue, 2),
            'cogs_365d': round(cogs, 2),
            'gross_margin_pct': round((revenue - cogs) / revenue * 100, 1) if revenue > 0 else 0,
            'inventory_turns': round(cogs / total_inv, 2) if total_inv > 0 else 0,
            'revenue_30d': round(_f(d['total_revenue_30d']), 2),
            'aging_buckets': {
                '0-30': round(_f(aging['bucket_0_30']), 2),
                '31-90': round(_f(aging['bucket_31_90']), 2),
                '91-365': round(_f(aging['bucket_91_365']), 2),
                '365+': round(_f(aging['bucket_365_plus']), 2),
            },
        }
    finally:
        conn.close()


def compute_action_lists(db) -> Dict:
    """
    Generate prioritized action lists for purchasing decisions.
    Thresholds are read from app_settings with safe defaults.
    """
    # Read thresholds (all stored as strings in app_settings; typed conversion here)
    stop_buying_min_dos = db.get_float_setting('stop_buying_min_dos', 180.0)
    buy_more_max_dos = db.get_float_setting('buy_more_max_dos', 60.0)
    buy_more_min_velocity = db.get_float_setting('buy_more_min_velocity', 0.03)
    buy_more_min_margin_pct = db.get_float_setting('buy_more_min_margin_pct', 10.0)
    # Store margin as a percent (0-100); convert to ratio for SQL
    buy_more_min_margin_ratio = buy_more_min_margin_pct / 100.0

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Filter clause used by lists that should NOT show items that are
        # already in a "we don't sell this anymore" state. These items get
        # their own dedicated tabs further down.
        # Note: kept as a string; spliced into queries below.
        not_retired_clause = (
            " AND LOWER(ISNULL(tags,'')) NOT LIKE '%discontinued%'"
            " AND LOWER(ISNULL(tags,'')) NOT LIKE '%replacement part%'"
            " AND NOT (ISNULL(inventory_policy,'') = 'DENY' AND current_stock <= 0)"
        )

        # STOP BUYING: high DOI, low velocity. Excludes already-retired items.
        cursor.execute(f"""
            SELECT TOP 50
                sku, product_id, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity, tags
            FROM product_velocity_cache
            WHERE current_stock > 0 AND days_of_stock > ?
                AND vendor IS NOT NULL AND vendor != ''
                {not_retired_clause}
            ORDER BY (current_stock * cost) DESC
        """, stop_buying_min_dos)
        stop_buying = _rows_to_dicts(cursor)

        # LIQUIDATE: dead stock (no sales in 365d, has stock). Excludes retired.
        cursor.execute(f"""
            SELECT TOP 50
                sku, product_id, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity, tags
            FROM product_velocity_cache
            WHERE current_stock > 0 AND total_sold_365d = 0
                AND vendor IS NOT NULL AND vendor != ''
                {not_retired_clause}
            ORDER BY (current_stock * cost) DESC
        """)
        liquidate = _rows_to_dicts(cursor)

        # INCREASE BUYING: low DOI, healthy margin, selling well (thresholds configurable).
        # Excludes retired/replacement-part items so they don't pollute reorder lists.
        cursor.execute(f"""
            SELECT TOP 50
                sku, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity,
                CASE WHEN price > 0 THEN (price - cost) / price * 100 ELSE 0 END AS margin_pct
            FROM product_velocity_cache
            WHERE current_stock > 0 AND days_of_stock < ?
                AND avg_daily_velocity > ?
                AND (price - cost) / NULLIF(price, 0) > ?
                AND vendor IS NOT NULL AND vendor != ''
                {not_retired_clause}
            ORDER BY (total_sold_365d * (price - cost)) DESC
        """, buy_more_max_dos, buy_more_min_velocity, buy_more_min_margin_ratio)
        increase_buying = _rows_to_dicts(cursor)

        # NO COST: products with $0 or null cost
        cursor.execute("""
            SELECT TOP 100
                sku, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity
            FROM product_velocity_cache
            WHERE (cost IS NULL OR cost = 0)
                AND vendor IS NOT NULL AND vendor != ''
            ORDER BY (total_sold_365d * price) DESC
        """)
        no_cost = _rows_to_dicts(cursor)

        # PRICELIST MISMATCH: cost in Shopify differs from supplier pricelist
        cursor.execute("""
            SELECT TOP 100
                pvc.sku, pvc.product_title, pvc.vendor, pvc.price, pvc.cost,
                pvc.current_stock,
                pvc.current_stock * pvc.cost AS inventory_value,
                pvc.total_sold_365d, pvc.total_sold_90d, pvc.total_sold_30d,
                pvc.days_of_stock, pvc.avg_daily_velocity,
                vpi.supplier_cost,
                vpi.supplier_currency,
                ABS(pvc.cost - vpi.supplier_cost) AS cost_diff
            FROM product_velocity_cache pvc
            INNER JOIN vendor_pricelist_items vpi
                ON UPPER(pvc.sku) = UPPER(vpi.supplier_sku)
                AND pvc.vendor = vpi.vendor
            WHERE vpi.match_status = 'matched'
                AND ABS(pvc.cost - vpi.supplier_cost) > 0.01
            ORDER BY ABS(pvc.cost - vpi.supplier_cost) DESC
        """)
        pricelist_mismatch = _rows_to_dicts(cursor)

        # MARGIN OUTLIERS: margins outside 20%-100% range
        cursor.execute("""
            SELECT TOP 100
                sku, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity,
                CASE WHEN price > 0 THEN (price - cost) / price * 100 ELSE 0 END AS margin_pct
            FROM product_velocity_cache
            WHERE price > 0 AND cost > 0
                AND vendor IS NOT NULL AND vendor != ''
                AND (
                    (price - cost) / price < 0.20
                    OR (price - cost) / price > 1.0
                )
            ORDER BY ABS((price - cost) / price - 0.35) DESC
        """)
        margin_outliers = _rows_to_dicts(cursor)

        # DISCONTINUED: Shopify-tagged Discontinued items still showing as
        # active. Useful to surface remaining inventory and decide whether
        # to liquidate or archive the listing.
        cursor.execute("""
            SELECT TOP 100
                sku, product_id, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity, tags, inventory_policy
            FROM product_velocity_cache
            WHERE LOWER(ISNULL(tags,'')) LIKE '%discontinued%'
            ORDER BY (current_stock * cost) DESC
        """)
        discontinued = _rows_to_dicts(cursor)

        # REPLACEMENT PARTS: tagged Replacement Part — ordered on demand.
        # Surfacing the list so the user can review periodically (e.g. retag,
        # promote to regular stock if velocity warrants).
        cursor.execute("""
            SELECT TOP 100
                sku, product_id, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity, tags, inventory_policy
            FROM product_velocity_cache
            WHERE LOWER(ISNULL(tags,'')) LIKE '%replacement part%'
            ORDER BY total_sold_365d DESC, (current_stock * cost) DESC
        """)
        replacement_parts = _rows_to_dicts(cursor)

        # OOS STOP-SELLING: inventory_policy = DENY AND zero stock.
        # These are effectively retired but not yet tagged Discontinued.
        # Useful list for: tag them as Discontinued, archive listing, or
        # bring them back if the OOS was a mistake.
        cursor.execute("""
            SELECT TOP 100
                sku, product_id, product_title, vendor, price, cost, current_stock,
                current_stock * cost AS inventory_value,
                total_sold_365d, total_sold_90d, total_sold_30d,
                days_of_stock, avg_daily_velocity, tags, inventory_policy
            FROM product_velocity_cache
            WHERE ISNULL(inventory_policy,'') = 'DENY'
                AND current_stock <= 0
                AND LOWER(ISNULL(tags,'')) NOT LIKE '%discontinued%'
                AND LOWER(ISNULL(tags,'')) NOT LIKE '%replacement part%'
            ORDER BY total_sold_365d DESC
        """)
        oos_stop_selling = _rows_to_dicts(cursor)

        return {
            'stop_buying': stop_buying,
            'liquidate': liquidate,
            'increase_buying': increase_buying,
            'no_cost': no_cost,
            'pricelist_mismatch': pricelist_mismatch,
            'margin_outliers': margin_outliers,
            'discontinued': discontinued,
            'replacement_parts': replacement_parts,
            'oos_stop_selling': oos_stop_selling,
            'thresholds': {
                'stop_buying_min_dos': stop_buying_min_dos,
                'buy_more_max_dos': buy_more_max_dos,
                'buy_more_min_velocity': buy_more_min_velocity,
                'buy_more_min_margin_pct': buy_more_min_margin_pct,
            },
        }
    finally:
        conn.close()


def compute_purchase_history(db, vendor: Optional[str] = None, months: int = 12) -> List[Dict]:
    """
    Summarize purchase orders by vendor over the last N months.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        where_clauses = ["so.created_at >= ?"]
        params = [datetime.utcnow() - timedelta(days=months * 30)]
        if vendor:
            where_clauses.append("so.vendor = ?")
            params.append(vendor)

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"""
            SELECT
                so.vendor,
                COUNT(DISTINCT so.id) AS po_count,
                SUM(soi.ordered_qty) AS total_ordered,
                SUM(soi.received_qty) AS total_received,
                SUM(soi.ordered_qty * soi.unit_cost) AS total_cost,
                MIN(so.created_at) AS first_po,
                MAX(so.created_at) AS last_po
            FROM stock_orders so
            JOIN stock_order_items soi ON soi.stock_order_id = so.id
            WHERE {where_sql}
            GROUP BY so.vendor
            ORDER BY SUM(soi.ordered_qty * soi.unit_cost) DESC
        """, *params)

        return _rows_to_dicts(cursor)
    finally:
        conn.close()


def compute_vendor_purchasing_analysis(db, months: int = 12) -> Dict:
    """
    Cross-reference purchasing spend with inventory health per vendor.
    Answers: "Am I buying proportionally to what sells, or over-buying?"
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cutoff = datetime.utcnow() - timedelta(days=months * 30)

        # Monthly purchasing by vendor
        cursor.execute("""
            SELECT
                so.vendor,
                FORMAT(so.created_at, 'yyyy-MM') AS month,
                SUM(soi.ordered_qty * soi.unit_cost) AS spend
            FROM stock_orders so
            JOIN stock_order_items soi ON soi.stock_order_id = so.id
            WHERE so.created_at >= ?
            GROUP BY so.vendor, FORMAT(so.created_at, 'yyyy-MM')
            ORDER BY so.vendor, FORMAT(so.created_at, 'yyyy-MM')
        """, cutoff)

        monthly_spend = {}
        all_months = set()
        for row in cursor.fetchall():
            vendor = row[0]
            month = row[1]
            spend = _f(row[2])
            all_months.add(month)
            if vendor not in monthly_spend:
                monthly_spend[vendor] = {}
            monthly_spend[vendor][month] = spend

        all_months = sorted(all_months)

        # Current inventory health per vendor (from velocity cache)
        cursor.execute("""
            SELECT
                vendor,
                SUM(current_stock * cost) AS inventory_value,
                SUM(total_sold_365d * price) AS revenue_365d,
                SUM(total_sold_365d * cost) AS cogs_365d,
                SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0
                    THEN current_stock * cost ELSE 0 END) AS dead_stock_value,
                AVG(CASE WHEN current_stock > 0 AND avg_daily_velocity > 0
                    THEN days_of_stock ELSE NULL END) AS avg_doi,
                SUM(CASE WHEN total_sold_365d > 0 THEN 1 ELSE 0 END) AS active_skus,
                SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0 THEN 1 ELSE 0 END) AS dead_skus,
                CASE WHEN SUM(current_stock * cost) > 0
                    THEN SUM(total_sold_365d * price) / SUM(current_stock * cost)
                    ELSE 0 END AS capital_efficiency
            FROM product_velocity_cache
            WHERE vendor IS NOT NULL AND vendor != ''
            GROUP BY vendor
        """)

        health = {}
        for row in cursor.fetchall():
            cols = [desc[0] for desc in cursor.description]
            d = dict(zip(cols, row))
            health[d['vendor']] = {
                'inventory_value': _f(d['inventory_value']),
                'revenue_365d': _f(d['revenue_365d']),
                'cogs_365d': _f(d['cogs_365d']),
                'dead_stock_value': _f(d['dead_stock_value']),
                'avg_doi': _f(d['avg_doi']),
                'active_skus': int(_f(d['active_skus'])),
                'dead_skus': int(_f(d['dead_skus'])),
                'capital_efficiency': _f(d['capital_efficiency']),
            }

        # Build combined vendor analysis
        vendors = []
        for vendor in sorted(set(list(monthly_spend.keys()) + list(health.keys()))):
            h = health.get(vendor, {})
            inv = h.get('inventory_value', 0)
            rev = h.get('revenue_365d', 0)
            cogs = h.get('cogs_365d', 0)
            doi = h.get('avg_doi', 0)
            dead = h.get('dead_stock_value', 0)

            spend_data = monthly_spend.get(vendor, {})
            total_spend = sum(spend_data.values())
            avg_monthly_spend = total_spend / max(len(all_months), 1)
            monthly_values = [spend_data.get(m, 0) for m in all_months]

            # Purchasing alignment: compare spend rate to sales rate
            # If spending faster than selling, you're over-buying
            monthly_cogs = cogs / 12 if cogs > 0 else 0
            if monthly_cogs > 0 and avg_monthly_spend > 0:
                purchase_ratio = avg_monthly_spend / monthly_cogs
                if purchase_ratio > 1.5:
                    alignment = 'over_buying'
                elif purchase_ratio < 0.7:
                    alignment = 'under_buying'
                else:
                    alignment = 'balanced'
            elif avg_monthly_spend > 0 and monthly_cogs == 0:
                alignment = 'over_buying'
            else:
                alignment = 'no_data'

            # Risk score: high DOI + high spend + high dead stock = bad
            risk_score = 0
            if doi > 180:
                risk_score += 3
            elif doi > 90:
                risk_score += 1
            if dead > inv * 0.3 and inv > 0:
                risk_score += 2
            if alignment == 'over_buying':
                risk_score += 2

            vendors.append({
                'vendor': vendor,
                'total_spend': round(total_spend, 2),
                'avg_monthly_spend': round(avg_monthly_spend, 2),
                'monthly_spend': monthly_values,
                'inventory_value': round(inv, 2),
                'revenue_365d': round(rev, 2),
                'margin_pct': round((rev - cogs) / rev * 100, 1) if rev > 0 else 0,
                'dead_stock_value': round(dead, 2),
                'avg_doi': round(doi, 0),
                'capital_efficiency': round(h.get('capital_efficiency', 0), 2),
                'active_skus': h.get('active_skus', 0),
                'dead_skus': h.get('dead_skus', 0),
                'alignment': alignment,
                'risk_score': risk_score,
            })

        # Sort by risk score desc, then inventory value desc
        vendors.sort(key=lambda v: (-v['risk_score'], -v['inventory_value']))

        return {
            'months': all_months,
            'vendors': vendors,
        }
    finally:
        conn.close()


# ─── HELPERS ─────────────────────────────────────────────────────

def _recommend_action(stock, sold_365, sold_90, sold_30, doi, margin_pct, velocity):
    """Classify a SKU into an action recommendation."""
    if stock == 0:
        return 'no_stock'

    # Dead stock: no sales in a year
    if sold_365 == 0:
        return 'liquidate'

    # Very slow: no sales in 90 days but sold sometime in last year
    if sold_90 == 0:
        return 'clearance'

    # Overstocked: more than 365 days of supply
    if doi > 365:
        return 'stop_buying'

    # Overstocked: 180-365 days, consider reducing
    if doi > 180:
        return 'reduce_buying'

    # Low margin: selling but not profitable
    if margin_pct < 10:
        return 'review_pricing'

    # Healthy but lots of stock
    if doi > 90:
        return 'maintain'

    # Running low with good velocity
    if doi < 14 and velocity > 0.05:
        return 'increase_buying'

    # Normal
    return 'maintain'


def _rows_to_dicts(cursor) -> List[Dict]:
    """Convert cursor results to list of dicts with rounded numbers."""
    columns = [desc[0] for desc in cursor.description]
    results = []
    for row in cursor.fetchall():
        d = {}
        for col, val in zip(columns, row):
            if isinstance(val, Decimal):
                d[col] = round(float(val), 2)
            elif isinstance(val, float):
                d[col] = round(val, 2)
            elif isinstance(val, datetime):
                d[col] = val.isoformat()
            else:
                d[col] = val
        results.append(d)
    return results

# ─── INVENTORY SNAPSHOTS ─────────────────────────────────────────

def capture_daily_snapshot(db) -> Dict:
    """
    Capture today's inventory snapshot (overall + per vendor).
    Safe to call multiple times per day — uses MERGE to upsert.
    """
    from datetime import date
    today = date.today()
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Overall snapshot
        cursor.execute("""
            MERGE inventory_snapshots AS target
            USING (
                SELECT
                    CAST(GETDATE() AS DATE) AS snapshot_date,
                    ISNULL(SUM(current_stock * cost), 0) AS total_inventory_value,
                    ISNULL(SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0
                        THEN current_stock * cost ELSE 0 END), 0) AS dead_stock_value,
                    ISNULL(SUM(CASE WHEN total_sold_365d > 0 AND total_sold_90d = 0 AND current_stock > 0
                        THEN current_stock * cost ELSE 0 END), 0) AS slow_stock_value,
                    ISNULL(SUM(CASE WHEN days_of_stock > 365 AND current_stock > 0
                        THEN current_stock * cost ELSE 0 END), 0) AS overstock_value,
                    ISNULL(SUM(on_order * cost), 0) AS on_order_value,
                    COUNT(*) AS total_skus,
                    SUM(CASE WHEN current_stock > 0 THEN 1 ELSE 0 END) AS stocked_skus,
                    ISNULL(SUM(total_sold_365d * price), 0) AS revenue_365d,
                    ISNULL(SUM(total_sold_365d * cost), 0) AS cogs_365d,
                    CASE WHEN SUM(current_stock * cost) > 0
                        THEN SUM(total_sold_365d * cost) / SUM(current_stock * cost)
                        ELSE 0 END AS inventory_turns
                FROM product_velocity_cache
                WHERE vendor IS NOT NULL AND vendor != ''
            ) AS source ON target.snapshot_date = source.snapshot_date
            WHEN MATCHED THEN UPDATE SET
                total_inventory_value = source.total_inventory_value,
                dead_stock_value = source.dead_stock_value,
                slow_stock_value = source.slow_stock_value,
                overstock_value = source.overstock_value,
                on_order_value = source.on_order_value,
                total_skus = source.total_skus,
                stocked_skus = source.stocked_skus,
                revenue_365d = source.revenue_365d,
                cogs_365d = source.cogs_365d,
                inventory_turns = source.inventory_turns
            WHEN NOT MATCHED THEN INSERT (
                snapshot_date, total_inventory_value, dead_stock_value,
                slow_stock_value, overstock_value, on_order_value,
                total_skus, stocked_skus, revenue_365d, cogs_365d, inventory_turns
            ) VALUES (
                source.snapshot_date, source.total_inventory_value, source.dead_stock_value,
                source.slow_stock_value, source.overstock_value, source.on_order_value,
                source.total_skus, source.stocked_skus, source.revenue_365d,
                source.cogs_365d, source.inventory_turns
            );
        """)

        # Vendor-level snapshots
        cursor.execute("""
            MERGE inventory_snapshots_vendor AS target
            USING (
                SELECT
                    CAST(GETDATE() AS DATE) AS snapshot_date,
                    vendor,
                    ISNULL(SUM(current_stock * cost), 0) AS inventory_value,
                    ISNULL(SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0
                        THEN current_stock * cost ELSE 0 END), 0) AS dead_stock_value,
                    ISNULL(SUM(current_stock), 0) AS units_in_stock,
                    SUM(CASE WHEN total_sold_365d = 0 AND current_stock > 0 THEN 1 ELSE 0 END) AS dead_sku_count,
                    SUM(CASE WHEN total_sold_365d > 0 THEN 1 ELSE 0 END) AS active_sku_count,
                    ISNULL(SUM(total_sold_365d * price), 0) AS revenue_365d,
                    CASE WHEN SUM(current_stock * cost) > 0
                        THEN SUM(total_sold_365d * price) / SUM(current_stock * cost)
                        ELSE 0 END AS capital_efficiency
                FROM product_velocity_cache
                WHERE vendor IS NOT NULL AND vendor != ''
                GROUP BY vendor
            ) AS source ON target.snapshot_date = source.snapshot_date AND target.vendor = source.vendor
            WHEN MATCHED THEN UPDATE SET
                inventory_value = source.inventory_value,
                dead_stock_value = source.dead_stock_value,
                units_in_stock = source.units_in_stock,
                dead_sku_count = source.dead_sku_count,
                active_sku_count = source.active_sku_count,
                revenue_365d = source.revenue_365d,
                capital_efficiency = source.capital_efficiency
            WHEN NOT MATCHED THEN INSERT (
                snapshot_date, vendor, inventory_value, dead_stock_value,
                units_in_stock, dead_sku_count, active_sku_count,
                revenue_365d, capital_efficiency
            ) VALUES (
                source.snapshot_date, source.vendor, source.inventory_value,
                source.dead_stock_value, source.units_in_stock,
                source.dead_sku_count, source.active_sku_count,
                source.revenue_365d, source.capital_efficiency
            );
        """)

        conn.commit()
        logger.info(f"Inventory snapshot captured for {today}")
        return {'status': 'ok', 'date': str(today)}
    except Exception as e:
        logger.error(f"Failed to capture inventory snapshot: {e}", exc_info=True)
        conn.rollback()
        raise
    finally:
        conn.close()


def get_inventory_trend(db, days: int = 90) -> Dict:
    """
    Retrieve inventory value trend over the last N days.
    Returns overall trend + top vendor trends.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Overall trend
        cursor.execute("""
            SELECT snapshot_date, total_inventory_value, dead_stock_value,
                   slow_stock_value, overstock_value, on_order_value,
                   revenue_365d, inventory_turns
            FROM inventory_snapshots
            WHERE snapshot_date >= DATEADD(day, ?, GETDATE())
            ORDER BY snapshot_date ASC
        """, -days)

        overall = []
        for row in cursor.fetchall():
            overall.append({
                'date': row[0].isoformat(),
                'total': _f(row[1]),
                'dead': _f(row[2]),
                'slow': _f(row[3]),
                'overstock': _f(row[4]),
                'on_order': _f(row[5]),
                'revenue_365d': _f(row[6]),
                'turns': _f(row[7]),
            })

        # Vendor trends (top 15 by latest inventory value)
        cursor.execute("""
            WITH latest AS (
                SELECT TOP 15 vendor
                FROM inventory_snapshots_vendor
                WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots_vendor)
                ORDER BY inventory_value DESC
            )
            SELECT sv.snapshot_date, sv.vendor, sv.inventory_value, sv.dead_stock_value
            FROM inventory_snapshots_vendor sv
            INNER JOIN latest l ON sv.vendor = l.vendor
            WHERE sv.snapshot_date >= DATEADD(day, ?, GETDATE())
            ORDER BY sv.vendor, sv.snapshot_date ASC
        """, -days)

        vendor_data = {}
        for row in cursor.fetchall():
            vendor = row[1]
            if vendor not in vendor_data:
                vendor_data[vendor] = []
            vendor_data[vendor].append({
                'date': row[0].isoformat(),
                'inventory_value': _f(row[2]),
                'dead_stock_value': _f(row[3]),
            })

        # Compute deltas if we have at least 2 data points
        delta = None
        if len(overall) >= 2:
            first = overall[0]
            last = overall[-1]
            delta = {
                'days': (datetime.fromisoformat(last['date']) - datetime.fromisoformat(first['date'])).days,
                'total_change': round(last['total'] - first['total'], 2),
                'dead_change': round(last['dead'] - first['dead'], 2),
                'total_pct_change': round((last['total'] - first['total']) / first['total'] * 100, 1) if first['total'] > 0 else 0,
            }

        return {
            'overall': overall,
            'vendors': vendor_data,
            'delta': delta,
            'target': 1000000,
        }
    finally:
        conn.close()
