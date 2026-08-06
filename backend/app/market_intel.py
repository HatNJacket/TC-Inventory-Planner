"""
TC Inventory Planner - Market Intelligence Module
Competitor price tracking, price gap analysis, and market positioning.
"""
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COMPETITORS = [
    'All-Star Telescope',
    'Astronomy Plus',
    'Ontario Telescope',
    'Khan Scope',
    'David Astro',
]


def _f(val):
    """Convert Decimal/int/None to float."""
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


def get_products_for_comparison(db, vendor: Optional[str] = None, min_revenue: float = 0, limit: int = 100) -> List[Dict]:
    """
    Get top products suitable for competitor price comparison.
    Prioritizes by revenue (what matters most to track competitively).
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        where = ["pvc.vendor IS NOT NULL AND pvc.vendor != ''"]
        params = []
        if vendor:
            where.append("pvc.vendor = ?")
            params.append(vendor)
        if min_revenue > 0:
            where.append("(pvc.total_sold_365d * pvc.price) >= ?")
            params.append(min_revenue)

        where_sql = " AND ".join(where)

        cursor.execute(f"""
            SELECT TOP {int(limit)}
                pvc.sku,
                pvc.product_title,
                pvc.vendor,
                pvc.price AS our_price,
                pvc.cost,
                pvc.total_sold_365d,
                (pvc.total_sold_365d * pvc.price) AS revenue_365d,
                pvc.current_stock,
                CASE WHEN pvc.price > 0 THEN (pvc.price - pvc.cost) / pvc.price * 100 ELSE 0 END AS margin_pct
            FROM product_velocity_cache pvc
            WHERE {where_sql}
            ORDER BY (pvc.total_sold_365d * pvc.price) DESC
        """, *params)

        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row))
            results.append({
                'sku': d['sku'],
                'product_title': d['product_title'],
                'vendor': d['vendor'],
                'our_price': round(_f(d['our_price']), 2),
                'cost': round(_f(d['cost']), 2),
                'revenue_365d': round(_f(d['revenue_365d']), 2),
                'sold_365d': int(_f(d['total_sold_365d'])),
                'current_stock': int(_f(d['current_stock'])),
                'margin_pct': round(_f(d['margin_pct']), 1),
            })
        return results
    finally:
        conn.close()


def get_competitor_prices(db, sku: Optional[str] = None, competitor: Optional[str] = None) -> List[Dict]:
    """Get stored competitor prices, optionally filtered."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = ["1=1"]
        params = []
        if sku:
            where.append("cp.sku = ?")
            params.append(sku)
        if competitor:
            where.append("cp.competitor = ?")
            params.append(competitor)

        cursor.execute(f"""
            SELECT
                cp.id, cp.sku, cp.product_title, cp.competitor,
                cp.competitor_price, cp.competitor_url, cp.competitor_in_stock,
                cp.our_price, cp.price_diff, cp.price_diff_pct,
                cp.last_checked, cp.notes
            FROM competitor_prices cp
            WHERE {" AND ".join(where)}
            ORDER BY cp.sku, cp.competitor
        """, *params)

        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row))
            results.append({
                'id': d['id'],
                'sku': d['sku'],
                'product_title': d['product_title'],
                'competitor': d['competitor'],
                'competitor_price': _f(d['competitor_price']) if d['competitor_price'] is not None else None,
                'competitor_url': d['competitor_url'],
                'competitor_in_stock': d['competitor_in_stock'],
                'our_price': round(_f(d['our_price']), 2),
                'price_diff': round(_f(d['price_diff']), 2) if d['price_diff'] is not None else None,
                'price_diff_pct': round(_f(d['price_diff_pct']), 1) if d['price_diff_pct'] is not None else None,
                'last_checked': d['last_checked'].isoformat() if d['last_checked'] else None,
                'notes': d['notes'],
            })
        return results
    finally:
        conn.close()


def upsert_competitor_price(db, data: Dict) -> Dict:
    """
    Insert or update a competitor price entry.
    Calculates price_diff and price_diff_pct automatically.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        sku = data['sku']
        competitor = data['competitor']
        comp_price = data.get('competitor_price')
        our_price = data.get('our_price', 0)

        # Calculate diff
        price_diff = None
        price_diff_pct = None
        if comp_price is not None and our_price > 0:
            price_diff = round(comp_price - our_price, 2)
            price_diff_pct = round(price_diff / our_price * 100, 1)

        cursor.execute("""
            MERGE competitor_prices AS target
            USING (SELECT ? AS sku, ? AS competitor) AS source
            ON target.sku = source.sku AND target.competitor = source.competitor
            WHEN MATCHED THEN UPDATE SET
                product_title = ?,
                competitor_price = ?,
                competitor_url = ?,
                competitor_in_stock = ?,
                our_price = ?,
                price_diff = ?,
                price_diff_pct = ?,
                last_checked = GETUTCDATE(),
                notes = ?
            WHEN NOT MATCHED THEN INSERT (
                sku, product_title, competitor, competitor_price, competitor_url,
                competitor_in_stock, our_price, price_diff, price_diff_pct, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
            sku, competitor,
            # UPDATE values
            data.get('product_title', ''),
            comp_price,
            data.get('competitor_url'),
            data.get('competitor_in_stock'),
            our_price,
            price_diff,
            price_diff_pct,
            data.get('notes'),
            # INSERT values
            sku, data.get('product_title', ''), competitor, comp_price,
            data.get('competitor_url'),
            data.get('competitor_in_stock'),
            our_price, price_diff, price_diff_pct, data.get('notes'),
        )
        conn.commit()
        return {'status': 'ok', 'sku': sku, 'competitor': competitor}
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_price_comparison_summary(db) -> Dict:
    """
    Summary of competitive positioning across all tracked products.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) AS total_entries,
                COUNT(DISTINCT sku) AS tracked_skus,
                COUNT(DISTINCT competitor) AS competitors_tracked,
                SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) AS we_are_cheaper,
                SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) AS they_are_cheaper,
                SUM(CASE WHEN price_diff = 0 THEN 1 ELSE 0 END) AS same_price,
                SUM(CASE WHEN competitor_price IS NULL THEN 1 ELSE 0 END) AS not_found,
                AVG(price_diff_pct) AS avg_price_diff_pct,
                SUM(CASE WHEN competitor_in_stock = 0 THEN 1 ELSE 0 END) AS competitor_out_of_stock
            FROM competitor_prices
            WHERE competitor_price IS NOT NULL
        """)

        row = cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        d = dict(zip(columns, row))

        # Per-competitor breakdown
        cursor.execute("""
            SELECT
                competitor,
                COUNT(*) AS entries,
                AVG(price_diff_pct) AS avg_diff_pct,
                SUM(CASE WHEN price_diff > 0 THEN 1 ELSE 0 END) AS we_cheaper,
                SUM(CASE WHEN price_diff < 0 THEN 1 ELSE 0 END) AS they_cheaper,
                SUM(CASE WHEN price_diff = 0 THEN 1 ELSE 0 END) AS same
            FROM competitor_prices
            WHERE competitor_price IS NOT NULL
            GROUP BY competitor
        """)

        by_competitor = []
        for row in cursor.fetchall():
            cols = [desc[0] for desc in cursor.description]
            cd = dict(zip(cols, row))
            by_competitor.append({
                'competitor': cd['competitor'],
                'entries': cd['entries'],
                'avg_diff_pct': round(_f(cd['avg_diff_pct']), 1),
                'we_cheaper': cd['we_cheaper'],
                'they_cheaper': cd['they_cheaper'],
                'same': cd['same'],
            })

        return {
            'total_entries': d['total_entries'] or 0,
            'tracked_skus': d['tracked_skus'] or 0,
            'competitors_tracked': d['competitors_tracked'] or 0,
            'we_are_cheaper': d['we_are_cheaper'] or 0,
            'they_are_cheaper': d['they_are_cheaper'] or 0,
            'same_price': d['same_price'] or 0,
            'not_found': d['not_found'] or 0,
            'avg_price_diff_pct': round(_f(d['avg_price_diff_pct']), 1),
            'competitor_out_of_stock': d['competitor_out_of_stock'] or 0,
            'by_competitor': by_competitor,
        }
    finally:
        conn.close()


def delete_competitor_price(db, entry_id: int) -> Dict:
    """Delete a competitor price entry."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM competitor_prices WHERE id = ?", entry_id)
        conn.commit()
        return {'status': 'ok', 'deleted_id': entry_id}
    finally:
        conn.close()
