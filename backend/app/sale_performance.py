"""
Sale Performance Module

Combines the FIFO journal (revenue + COGS) with allocated operating expenses
to produce fully-loaded margin reports per SKU and per order.

Allocation methods:
  - ``revenue``: each SKU/order absorbs overhead in proportion to its share
    of total revenue in the window. The natural default for retail.
  - ``units``: each SKU/order absorbs overhead in proportion to its share
    of units sold in the window. Better when SKUs have wildly different
    price points but similar handling cost.
"""
import logging
from typing import Optional

from .expenses import compute_allocated_overhead

logger = logging.getLogger(__name__)


def _f(val):
    if val is None:
        return 0.0
    return float(val)


def _date_filter(start_date, end_date, alias=""):
    """Build a 'AND <alias>order_date BETWEEN …' fragment + params."""
    pre = (alias + ".") if alias else ""
    clauses = []
    params = []
    if start_date:
        clauses.append(pre + "order_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append(pre + "order_date <= ?")
        params.append(end_date)
    sql = (" AND " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def get_sku_leaderboard(db, start_date=None, end_date=None, vendor=None,
                        method='revenue', sort_by='profit', limit=50):
    """Per-SKU revenue/COGS/overhead/margin in a window, sorted for a leaderboard.

    sort_by: ``profit`` (fully-loaded), ``loss`` (worst margin %), ``margin``
    (best margin %), ``revenue``, ``units``.
    """
    if method not in ('revenue', 'units'):
        raise ValueError("method must be 'revenue' or 'units'")
    if sort_by not in ('profit', 'loss', 'margin', 'revenue', 'units'):
        raise ValueError("invalid sort_by")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Per-SKU matched aggregates from fifo_journal. Vendor filter goes
        # through purchase_lots (the journal itself has no vendor column).
        sql_filter, params = _date_filter(start_date, end_date, alias="fj")
        vendor_join = ""
        if vendor:
            vendor_join = " INNER JOIN purchase_lots pl ON pl.id = fj.lot_id "
            sql_filter += " AND pl.vendor = ?"
            params.append(vendor)

        cursor.execute("""
            SELECT fj.sku,
                   SUM(fj.quantity_sold) AS units,
                   SUM(fj.total_revenue) AS revenue,
                   SUM(fj.total_cogs) AS cogs,
                   SUM(CASE WHEN fj.is_vendor_sale_cost = 1 THEN fj.quantity_sold ELSE 0 END) AS sale_units,
                   COUNT(DISTINCT fj.order_number) AS order_count
            FROM fifo_journal fj %s
            WHERE 1=1 %s
            GROUP BY fj.sku
        """ % (vendor_join, sql_filter), *params)

        skus = {}
        for row in cursor.fetchall():
            skus[row[0]] = {
                'sku': row[0],
                'units': row[1] or 0,
                'revenue': _f(row[2]),
                'cogs': _f(row[3]),
                'vendor_sale_units': row[4] or 0,
                'order_count': row[5] or 0,
                'unmatched_units': 0,
                'unmatched_revenue': 0.0,
            }

        # Layer in unmatched lines (revenue without cost basis).
        un_filter, un_params = _date_filter(start_date, end_date, alias="ul")
        cursor.execute("""
            SELECT ul.sku,
                   SUM(ul.quantity) AS units,
                   SUM(ul.total_revenue) AS revenue
            FROM fifo_unmatched_lines ul
            WHERE 1=1 %s
            GROUP BY ul.sku
        """ % un_filter, *un_params)
        for row in cursor.fetchall():
            sku, units, rev = row[0], row[1] or 0, _f(row[2])
            entry = skus.get(sku)
            if entry is None:
                entry = {
                    'sku': sku, 'units': 0, 'revenue': 0.0, 'cogs': 0.0,
                    'vendor_sale_units': 0, 'order_count': 0,
                    'unmatched_units': 0, 'unmatched_revenue': 0.0,
                }
                skus[sku] = entry
            entry['unmatched_units'] = units
            entry['unmatched_revenue'] = rev
            entry['units'] += units
            entry['revenue'] += rev

        # Look up titles + vendors from velocity cache.
        if skus:
            placeholders = ','.join('?' * len(skus))
            cursor.execute(
                "SELECT sku, product_title, vendor FROM product_velocity_cache "
                "WHERE sku IN (" + placeholders + ")",
                *list(skus.keys())
            )
            for sku, title, vend in cursor.fetchall():
                if sku in skus:
                    skus[sku]['title'] = title or ''
                    skus[sku]['vendor'] = vend or ''

        # Allocate overhead.
        overhead = compute_allocated_overhead(db, start_date, end_date) \
            if (start_date and end_date) else {'total_cad': 0.0, 'by_category': []}
        total_overhead = overhead['total_cad']

        if method == 'revenue':
            total_basis = sum(s['revenue'] for s in skus.values())
        else:
            total_basis = sum(s['units'] for s in skus.values())

        for s in skus.values():
            basis = s['revenue'] if method == 'revenue' else s['units']
            alloc = (total_overhead * basis / total_basis) if total_basis > 0 else 0
            s['allocated_overhead'] = round(alloc, 2)
            s['fully_loaded_cogs'] = round(s['cogs'] + alloc, 2)
            s['gross_profit'] = round(s['revenue'] - s['cogs'], 2)
            s['fully_loaded_profit'] = round(s['revenue'] - s['cogs'] - alloc, 2)
            s['gross_margin_pct'] = (
                round((s['revenue'] - s['cogs']) / s['revenue'] * 100, 1)
                if s['revenue'] > 0 else 0
            )
            s['fully_loaded_margin_pct'] = (
                round((s['revenue'] - s['cogs'] - alloc) / s['revenue'] * 100, 1)
                if s['revenue'] > 0 else 0
            )
            s['vendor_sale_share_pct'] = (
                round(s['vendor_sale_units'] / s['units'] * 100, 1)
                if s['units'] > 0 else 0
            )
            s.setdefault('title', '')
            s.setdefault('vendor', '')

        rows = list(skus.values())
        if sort_by == 'profit':
            rows.sort(key=lambda x: -x['fully_loaded_profit'])
        elif sort_by == 'loss':
            rows.sort(key=lambda x: x['fully_loaded_profit'])
        elif sort_by == 'margin':
            rows.sort(key=lambda x: -x['fully_loaded_margin_pct'])
        elif sort_by == 'revenue':
            rows.sort(key=lambda x: -x['revenue'])
        elif sort_by == 'units':
            rows.sort(key=lambda x: -x['units'])

        # Totals across the entire window (not the sliced page).
        totals = {
            'sku_count': len(rows),
            'units': sum(s['units'] for s in rows),
            'revenue': round(sum(s['revenue'] for s in rows), 2),
            'cogs': round(sum(s['cogs'] for s in rows), 2),
            'allocated_overhead': round(total_overhead, 2),
            'unmatched_units': sum(s['unmatched_units'] for s in rows),
            'unmatched_revenue': round(sum(s['unmatched_revenue'] for s in rows), 2),
        }
        totals['gross_profit'] = round(totals['revenue'] - totals['cogs'], 2)
        totals['fully_loaded_profit'] = round(
            totals['revenue'] - totals['cogs'] - totals['allocated_overhead'], 2
        )
        totals['fully_loaded_margin_pct'] = (
            round(totals['fully_loaded_profit'] / totals['revenue'] * 100, 1)
            if totals['revenue'] > 0 else 0
        )
        totals['gross_margin_pct'] = (
            round(totals['gross_profit'] / totals['revenue'] * 100, 1)
            if totals['revenue'] > 0 else 0
        )

        return {
            'method': method,
            'sort_by': sort_by,
            'start_date': start_date,
            'end_date': end_date,
            'vendor': vendor,
            'totals': totals,
            'overhead_by_category': overhead['by_category'],
            'skus': rows[:limit],
        }
    finally:
        conn.close()


def list_orders_with_overhead(db, start_date=None, end_date=None, vendor=None,
                              method='revenue', limit=200, offset=0):
    """Per-order P&L with allocated overhead added on top of FIFO COGS."""
    if method not in ('revenue', 'units'):
        raise ValueError("method must be 'revenue' or 'units'")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        where = []
        params = []
        if start_date:
            where.append("order_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("order_date <= ?")
            params.append(end_date)

        match_where = list(where)
        match_params = list(params)
        if vendor:
            match_where.append("""order_number IN (
                SELECT DISTINCT fj2.order_number FROM fifo_journal fj2
                INNER JOIN purchase_lots pl ON pl.id = fj2.lot_id
                WHERE pl.vendor = ?
            )""")
            match_params.append(vendor)
        match_clause = (" WHERE " + " AND ".join(match_where)) if match_where else ""

        cursor.execute("""
            SELECT order_number, MIN(order_date) AS order_date,
                   SUM(total_revenue) AS revenue,
                   SUM(total_cogs) AS cogs,
                   SUM(quantity_sold) AS units,
                   SUM(CASE WHEN is_vendor_sale_cost = 1 THEN quantity_sold ELSE 0 END) AS sale_units
            FROM fifo_journal
            %s
            GROUP BY order_number
        """ % match_clause, *match_params)

        orders = {}
        for r in cursor.fetchall():
            orders[r[0]] = {
                'order_number': r[0],
                'order_date': r[1].isoformat() if r[1] else None,
                'revenue': _f(r[2]), 'cogs': _f(r[3]),
                'units': r[4] or 0, 'vendor_sale_units': r[5] or 0,
                'unmatched_units': 0, 'unmatched_revenue': 0.0,
            }

        un_clause = (" WHERE " + " AND ".join(where)) if where else ""
        cursor.execute("""
            SELECT order_number, MIN(order_date),
                   SUM(total_revenue), SUM(quantity)
            FROM fifo_unmatched_lines
            %s
            GROUP BY order_number
        """ % un_clause, *params)
        for r in cursor.fetchall():
            order_number = r[0]
            entry = orders.get(order_number)
            if entry is None:
                entry = {
                    'order_number': order_number,
                    'order_date': r[1].isoformat() if r[1] else None,
                    'revenue': 0.0, 'cogs': 0.0, 'units': 0,
                    'vendor_sale_units': 0, 'unmatched_units': 0,
                    'unmatched_revenue': 0.0,
                }
                orders[order_number] = entry
            entry['unmatched_revenue'] = _f(r[2])
            entry['unmatched_units'] = r[3] or 0
            entry['revenue'] += _f(r[2])
            entry['units'] += r[3] or 0

        overhead = (
            compute_allocated_overhead(db, start_date, end_date)
            if (start_date and end_date) else {'total_cad': 0.0, 'by_category': []}
        )
        total_overhead = overhead['total_cad']

        if method == 'revenue':
            total_basis = sum(o['revenue'] for o in orders.values())
        else:
            total_basis = sum(o['units'] for o in orders.values())

        for o in orders.values():
            basis = o['revenue'] if method == 'revenue' else o['units']
            alloc = (total_overhead * basis / total_basis) if total_basis > 0 else 0
            o['allocated_overhead'] = round(alloc, 2)
            o['gross_profit'] = round(o['revenue'] - o['cogs'], 2)
            o['fully_loaded_profit'] = round(o['revenue'] - o['cogs'] - alloc, 2)
            o['margin_pct'] = (
                round((o['revenue'] - o['cogs']) / o['revenue'] * 100, 1)
                if o['revenue'] > 0 else 0
            )
            o['fully_loaded_margin_pct'] = (
                round((o['revenue'] - o['cogs'] - alloc) / o['revenue'] * 100, 1)
                if o['revenue'] > 0 else 0
            )
            o['vendor_sale_share_pct'] = (
                round(o['vendor_sale_units'] / o['units'] * 100, 1)
                if o['units'] > 0 else 0
            )

        ordered = sorted(orders.values(), key=lambda x: x['order_date'] or '', reverse=True)
        total = len(ordered)
        return {
            'method': method,
            'allocated_overhead_total': round(total_overhead, 2),
            'overhead_by_category': overhead['by_category'],
            'total': total,
            'limit': limit,
            'offset': offset,
            'orders': ordered[offset:offset + limit],
        }
    finally:
        conn.close()
