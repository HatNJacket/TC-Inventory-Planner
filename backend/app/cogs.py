"""
COGS Tracking Module - FIFO Inventory Costing with SKU Matching

Tracks purchase invoices as inventory lots and matches sales against
the oldest available lot (FIFO) to compute true cost of goods sold.

Includes vendor SKU -> Shopify SKU matching with saved mappings and
fuzzy title-based suggestions.
"""
import csv
import io
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _f(val):
    if val is None:
        return 0.0
    return float(val)


def _parse_num(val):
    if not val:
        return 0
    try:
        return float(str(val).strip().replace('$', '').replace(',', ''))
    except (ValueError, TypeError):
        return 0


# --- SKU MATCHING ---

def _get_saved_mappings(cursor, vendor):
    cursor.execute(
        "SELECT vendor_sku, shopify_sku FROM sku_mappings WHERE vendor = ?", vendor
    )
    return {row[0].upper(): row[1] for row in cursor.fetchall()}


def _get_shopify_products(cursor, vendor):
    cursor.execute("""
        SELECT sku, product_title, price, cost, current_stock
        FROM product_velocity_cache
        WHERE vendor = ? AND sku IS NOT NULL AND sku != ''
    """, vendor)
    products = {}
    for row in cursor.fetchall():
        products[row[0].upper()] = {
            'sku': row[0],
            'title': row[1],
            'price': _f(row[2]),
            'cost': _f(row[3]),
            'stock': row[4],
        }
    return products


def _fuzzy_title_match(title, products, top_n=3):
    if not title:
        return []
    title_words = set(title.upper().split())
    scores = []
    for sku_upper, prod in products.items():
        prod_words = set(prod['title'].upper().split())
        if not prod_words:
            continue
        common = title_words & prod_words
        score = len(common) / max(len(title_words), len(prod_words))
        if score > 0.2:
            scores.append((score, prod))
    scores.sort(key=lambda x: -x[0])
    return [s[1] for s in scores[:top_n]]


def preview_invoice(db, vendor, csv_content, sku_column, qty_column,
                    cost_column, currency='USD', description_column=None):
    """Parse an invoice and return matching preview with suggestions."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Get FX rate
        fx_rate = 1.0
        if currency.upper() != 'CAD':
            pair = currency.upper() + 'CAD'
            cursor.execute("SELECT effective_rate FROM fx_rates WHERE currency_pair = ?", pair)
            fx_row = cursor.fetchone()
            if fx_row and fx_row[0]:
                fx_rate = _f(fx_row[0])
            else:
                raise ValueError("No FX rate stored for %s. Set it on the Vendors page first." % pair)

        saved_mappings = _get_saved_mappings(cursor, vendor)
        shopify_products = _get_shopify_products(cursor, vendor)

        reader = csv.DictReader(io.StringIO(csv_content))
        items = []
        for row in reader:
            vendor_sku = (row.get(sku_column) or '').strip()
            if not vendor_sku:
                continue
            qty = int(_parse_num(row.get(qty_column)))
            cost = _parse_num(row.get(cost_column))
            if qty <= 0 or cost <= 0:
                continue
            desc = (row.get(description_column) or '').strip() if description_column else ''
            cost_cad = round(cost * fx_rate, 2)

            vendor_sku_upper = vendor_sku.upper()
            match_type = 'unmatched'
            matched_sku = ''
            matched_title = ''
            suggestions = []

            # 1. Check saved mappings
            if vendor_sku_upper in saved_mappings:
                mapped_sku = saved_mappings[vendor_sku_upper]
                if mapped_sku.upper() in shopify_products:
                    match_type = 'mapped'
                    matched_sku = shopify_products[mapped_sku.upper()]['sku']
                    matched_title = shopify_products[mapped_sku.upper()]['title']

            # 2. Exact SKU match
            if match_type == 'unmatched' and vendor_sku_upper in shopify_products:
                match_type = 'exact'
                matched_sku = shopify_products[vendor_sku_upper]['sku']
                matched_title = shopify_products[vendor_sku_upper]['title']

            # 3. Fuzzy title match
            if match_type == 'unmatched' and desc:
                fuzzy = _fuzzy_title_match(desc, shopify_products)
                if fuzzy:
                    suggestions = [{'sku': s['sku'], 'title': s['title']} for s in fuzzy]
                    match_type = 'suggested'
                    matched_sku = suggestions[0]['sku']
                    matched_title = suggestions[0]['title']

            items.append({
                'vendor_sku': vendor_sku,
                'description': desc,
                'quantity': qty,
                'unit_cost_foreign': round(cost, 2),
                'unit_cost_cad': cost_cad,
                'match_type': match_type,
                'matched_sku': matched_sku,
                'matched_title': matched_title,
                'suggestions': suggestions,
            })

        if not items:
            return {'status': 'error', 'message': 'No valid line items found'}

        return {
            'status': 'ok',
            'items': items,
            'fx_rate': fx_rate,
            'currency': currency,
            'matched_count': sum(1 for i in items if i['match_type'] in ('exact', 'mapped')),
            'suggested_count': sum(1 for i in items if i['match_type'] == 'suggested'),
            'unmatched_count': sum(1 for i in items if i['match_type'] == 'unmatched'),
            'total_items': len(items),
        }
    finally:
        conn.close()


def confirm_invoice(db, vendor, invoice_number, invoice_date,
                    currency, fx_rate, items, is_vendor_sale=False):
    """Confirm matched invoice items and create purchase lots + save mappings.

    is_vendor_sale flags the whole invoice as a vendor promotion. Lots inherit
    the flag, and (if a per-line ``regular_unit_cost_foreign`` is supplied) the
    converted regular cost is stored so margin reports can show the discount.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM purchase_invoices WHERE vendor = ? AND invoice_number = ?",
            vendor, invoice_number
        )
        if cursor.fetchone():
            return {'status': 'error',
                    'message': 'Invoice %s already exists for %s.' % (invoice_number, vendor)}

        matched_items = [i for i in items if i.get('matched_sku')]
        if not matched_items:
            return {'status': 'error', 'message': 'No matched items to import.'}

        total_cost = sum(i['unit_cost_cad'] * i['quantity'] for i in matched_items)
        total_units = sum(i['quantity'] for i in matched_items)

        cursor.execute("""
            INSERT INTO purchase_invoices
            (vendor, invoice_number, invoice_date, currency, fx_rate, total_items, total_cost_cad, is_vendor_sale)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, vendor, invoice_number, invoice_date, currency, fx_rate,
            total_units, round(total_cost, 2), 1 if is_vendor_sale else 0)
        invoice_id = cursor.fetchone()[0]

        for item in matched_items:
            reg_cad = None
            reg_foreign = item.get('regular_unit_cost_foreign')
            if reg_foreign:
                reg_cad = round(_f(reg_foreign) * _f(fx_rate), 2)
            lot_is_sale = bool(item.get('is_vendor_sale', is_vendor_sale))
            cursor.execute("""
                INSERT INTO purchase_lots
                (invoice_id, sku, vendor, quantity_purchased, quantity_remaining,
                 unit_cost_foreign, unit_cost_cad, invoice_date,
                 is_vendor_sale, regular_unit_cost_cad)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, invoice_id, item['matched_sku'], vendor,
                item['quantity'], item['quantity'],
                item['unit_cost_foreign'], item['unit_cost_cad'], invoice_date,
                1 if lot_is_sale else 0, reg_cad)

        # Save SKU mappings
        for item in matched_items:
            if item.get('vendor_sku') and item.get('matched_sku'):
                cursor.execute("""
                    MERGE sku_mappings AS target
                    USING (SELECT ? AS vendor, ? AS vendor_sku, ? AS shopify_sku) AS source
                    ON target.vendor = source.vendor AND target.vendor_sku = source.vendor_sku
                    WHEN MATCHED THEN UPDATE SET shopify_sku = source.shopify_sku
                    WHEN NOT MATCHED THEN INSERT (vendor, vendor_sku, shopify_sku)
                        VALUES (source.vendor, source.vendor_sku, source.shopify_sku);
                """, vendor, item['vendor_sku'], item['matched_sku'])

        conn.commit()
        return {
            'status': 'ok',
            'invoice_id': invoice_id,
            'vendor': vendor,
            'invoice_number': invoice_number,
            'invoice_date': invoice_date,
            'line_items': len(matched_items),
            'skipped_items': len(items) - len(matched_items),
            'total_units': total_units,
            'total_cost_cad': round(total_cost, 2),
            'fx_rate': fx_rate,
            'currency': currency,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- INVOICE MANAGEMENT ---

def list_invoices(db, vendor=None):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        sql = """SELECT id, vendor, invoice_number, invoice_date, currency, fx_rate,
                       total_items, total_cost_cad, notes, created_at
                FROM purchase_invoices"""
        if vendor:
            sql += " WHERE vendor = ? ORDER BY invoice_date DESC"
            cursor.execute(sql, vendor)
        else:
            sql += " ORDER BY invoice_date DESC"
            cursor.execute(sql)

        columns = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row))
            d['total_cost_cad'] = _f(d['total_cost_cad'])
            d['fx_rate'] = _f(d['fx_rate'])
            d['invoice_date'] = d['invoice_date'].isoformat() if d['invoice_date'] else None
            d['created_at'] = d['created_at'].isoformat() if d['created_at'] else None
            results.append(d)
        return results
    finally:
        conn.close()


def delete_invoice(db, invoice_id):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM fifo_journal fj
            INNER JOIN purchase_lots pl ON fj.lot_id = pl.id
            WHERE pl.invoice_id = ?
        """, invoice_id)
        count = cursor.fetchone()[0]
        if count > 0:
            return {'status': 'error',
                    'message': 'Cannot delete: %d sales matched to this invoice.' % count}

        cursor.execute("DELETE FROM purchase_lots WHERE invoice_id = ?", invoice_id)
        cursor.execute("DELETE FROM purchase_invoices WHERE id = ?", invoice_id)
        conn.commit()
        return {'status': 'ok', 'deleted': invoice_id}
    finally:
        conn.close()


def get_invoice_lots(db, invoice_id):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sku, vendor, quantity_purchased, quantity_remaining,
                   unit_cost_foreign, unit_cost_cad, invoice_date,
                   ISNULL(is_vendor_sale, 0) AS is_vendor_sale,
                   regular_unit_cost_cad
            FROM purchase_lots WHERE invoice_id = ?
            ORDER BY sku
        """, invoice_id)
        columns = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row))
            d['unit_cost_foreign'] = _f(d['unit_cost_foreign'])
            d['unit_cost_cad'] = _f(d['unit_cost_cad'])
            d['regular_unit_cost_cad'] = _f(d['regular_unit_cost_cad']) if d['regular_unit_cost_cad'] is not None else None
            d['is_vendor_sale'] = bool(d['is_vendor_sale'])
            d['invoice_date'] = d['invoice_date'].isoformat() if d['invoice_date'] else None
            results.append(d)
        return results
    finally:
        conn.close()


# --- REPORTING ---

def get_cogs_summary(db, start_date=None, end_date=None):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        date_filter = ""
        params = []
        if start_date:
            date_filter += " AND order_date >= ?"
            params.append(start_date)
        if end_date:
            date_filter += " AND order_date <= ?"
            params.append(end_date)

        cursor.execute("""
            SELECT
                COUNT(DISTINCT order_number) AS total_orders,
                COUNT(*) AS journal_entries,
                ISNULL(SUM(total_revenue), 0) AS total_revenue,
                ISNULL(SUM(total_cogs), 0) AS total_cogs,
                ISNULL(SUM(total_revenue) - SUM(total_cogs), 0) AS gross_profit,
                ISNULL(SUM(quantity_sold), 0) AS total_units
            FROM fifo_journal
            WHERE 1=1 %s
        """ % date_filter, *params)

        row = cursor.fetchone()
        summary = {
            'total_orders': row[0] or 0,
            'journal_entries': row[1] or 0,
            'total_revenue': _f(row[2]),
            'total_cogs': _f(row[3]),
            'gross_profit': _f(row[4]),
            'total_units': row[5] or 0,
        }
        if summary['total_revenue'] > 0:
            summary['gross_margin_pct'] = round(summary['gross_profit'] / summary['total_revenue'] * 100, 1)
        else:
            summary['gross_margin_pct'] = 0

        # Monthly breakdown
        cursor.execute("""
            SELECT
                FORMAT(order_date, 'yyyy-MM') AS month,
                COUNT(DISTINCT order_number) AS orders,
                SUM(total_revenue) AS revenue,
                SUM(total_cogs) AS cogs,
                SUM(total_revenue) - SUM(total_cogs) AS profit,
                SUM(quantity_sold) AS units
            FROM fifo_journal
            WHERE 1=1 %s
            GROUP BY FORMAT(order_date, 'yyyy-MM')
            ORDER BY month
        """ % date_filter, *params)

        monthly = []
        for row in cursor.fetchall():
            rev = _f(row[2])
            cogs = _f(row[3])
            monthly.append({
                'month': row[0],
                'orders': row[1],
                'revenue': rev,
                'cogs': cogs,
                'profit': _f(row[4]),
                'margin_pct': round((rev - cogs) / rev * 100, 1) if rev > 0 else 0,
                'units': row[5],
            })

        # Lot stats
        cursor.execute("""
            SELECT COUNT(*), SUM(CASE WHEN quantity_remaining > 0 THEN 1 ELSE 0 END),
                   ISNULL(SUM(quantity_remaining * unit_cost_cad), 0)
            FROM purchase_lots
        """)
        lr = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) FROM purchase_invoices")
        inv_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sku_mappings")
        map_count = cursor.fetchone()[0]

        # Vendor-sale cost share
        cursor.execute("""
            SELECT
                ISNULL(SUM(CASE WHEN is_vendor_sale_cost = 1 THEN total_cogs ELSE 0 END), 0),
                ISNULL(SUM(CASE WHEN is_vendor_sale_cost = 1 THEN quantity_sold ELSE 0 END), 0)
            FROM fifo_journal
            WHERE 1=1 %s
        """ % date_filter, *params)
        vs_row = cursor.fetchone()
        summary['vendor_sale_cogs'] = _f(vs_row[0])
        summary['vendor_sale_units'] = vs_row[1] or 0

        # Unmatched lines (revenue with no cost basis)
        unmatched_filter = date_filter  # same param order
        cursor.execute("""
            SELECT COUNT(*), ISNULL(SUM(total_revenue), 0), ISNULL(SUM(quantity), 0)
            FROM fifo_unmatched_lines
            WHERE 1=1 %s
        """ % unmatched_filter, *params)
        un_row = cursor.fetchone()
        summary['unmatched_line_count'] = un_row[0] or 0
        summary['unmatched_revenue'] = _f(un_row[1])
        summary['unmatched_units'] = un_row[2] or 0

        return {
            'summary': summary,
            'monthly': monthly,
            'lot_stats': {
                'total_lots': lr[0] or 0,
                'open_lots': lr[1] or 0,
                'open_inventory_value': _f(lr[2]),
            },
            'invoice_count': inv_count,
            'mapping_count': map_count,
        }
    finally:
        conn.close()


# --- SALE-AWARE FIFO MATCHER -----------------------------------------------

def _processed_order_numbers(cursor):
    """Order numbers that already have any FIFO journal or unmatched rows."""
    cursor.execute("SELECT DISTINCT order_number FROM fifo_journal")
    out = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT order_number FROM fifo_unmatched_lines")
    out.update(row[0] for row in cursor.fetchall())
    return out


def run_fifo_matcher(db, line_items_by_order, reprocess=False):
    """Consume ``purchase_lots`` FIFO for the given Shopify line items.

    The caller is responsible for fetching ``line_items_by_order`` (a dict
    of ``order_number -> [line dicts]``); this keeps the function sync so
    it can run inside FastAPI's event loop. See
    ``ShopifyClient.fetch_orders_for_fifo`` for the expected line shape.

    Lots are consumed oldest-first by ``invoice_date``, restricted to lots
    whose ``invoice_date <= order.placed_at`` so we never use inventory
    that didn't exist yet at order time. Each order/sku/lot split writes
    one ``fifo_journal`` row; line items with no available lot at that
    date go into ``fifo_unmatched_lines``.

    Idempotent: orders already present in either table are skipped unless
    ``reprocess`` is True (in which case those rows are deleted and lot
    quantities are restored before matching).
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        if reprocess:
            order_numbers = list(line_items_by_order.keys())
            for order_number in order_numbers:
                cursor.execute("""
                    UPDATE pl SET pl.quantity_remaining = pl.quantity_remaining + fj.quantity_sold
                    FROM purchase_lots pl
                    INNER JOIN fifo_journal fj ON fj.lot_id = pl.id
                    WHERE fj.order_number = ?
                """, order_number)
                cursor.execute("DELETE FROM fifo_journal WHERE order_number = ?", order_number)
                cursor.execute("DELETE FROM fifo_unmatched_lines WHERE order_number = ?", order_number)
            already_processed = set()
        else:
            already_processed = _processed_order_numbers(cursor)

        stats = {
            'orders_processed': 0,
            'orders_skipped': 0,
            'journal_rows': 0,
            'unmatched_rows': 0,
            'matched_revenue': 0.0,
            'matched_cogs': 0.0,
            'unmatched_revenue': 0.0,
            'vendor_sale_units': 0,
        }

        for order_number, lines in line_items_by_order.items():
            if order_number in already_processed:
                stats['orders_skipped'] += 1
                continue
            stats['orders_processed'] += 1

            for line in lines:
                sku = line['sku']
                qty_needed = int(line['quantity'])
                unit_price = _f(line['sale_price'])
                line_revenue = _f(line.get('total_revenue', unit_price * qty_needed))
                order_date = line['order_date']

                # Find lots that existed at order time, oldest first.
                cursor.execute("""
                    SELECT id, quantity_remaining, unit_cost_cad,
                           ISNULL(is_vendor_sale, 0) AS is_vendor_sale
                    FROM purchase_lots
                    WHERE sku = ?
                      AND quantity_remaining > 0
                      AND invoice_date <= ?
                    ORDER BY invoice_date ASC, id ASC
                """, sku, order_date)
                lots = cursor.fetchall()

                consumed = 0
                for lot_id, lot_remaining, lot_cost, lot_is_sale in lots:
                    if qty_needed <= 0:
                        break
                    take = min(int(lot_remaining), qty_needed)
                    if take <= 0:
                        continue
                    cogs = round(_f(lot_cost) * take, 2)
                    # Revenue for this slice is proportional to quantity taken,
                    # so a partial lot match still ties to the right share.
                    slice_revenue = round(line_revenue * take / int(line['quantity']), 2)
                    cursor.execute("""
                        INSERT INTO fifo_journal
                        (order_number, order_date, sku, quantity_sold, sale_price,
                         lot_id, unit_cost_cad, total_cogs, total_revenue,
                         is_vendor_sale_cost)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, order_number, order_date, sku, take, unit_price,
                        lot_id, _f(lot_cost), cogs, slice_revenue,
                        1 if lot_is_sale else 0)
                    cursor.execute(
                        "UPDATE purchase_lots SET quantity_remaining = quantity_remaining - ? WHERE id = ?",
                        take, lot_id
                    )
                    qty_needed -= take
                    consumed += take
                    stats['journal_rows'] += 1
                    stats['matched_cogs'] += cogs
                    stats['matched_revenue'] += slice_revenue
                    if lot_is_sale:
                        stats['vendor_sale_units'] += take

                if qty_needed > 0:
                    # No lot covered (part of) the line at order time.
                    unmatched_revenue = round(
                        line_revenue * qty_needed / int(line['quantity']), 2
                    )
                    reason = 'no_lots' if consumed == 0 else 'partial_lots'
                    cursor.execute("""
                        INSERT INTO fifo_unmatched_lines
                        (order_number, order_date, sku, vendor, quantity,
                         sale_price, total_revenue, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, order_number, order_date, sku, line.get('vendor'),
                        qty_needed, unit_price, unmatched_revenue, reason)
                    stats['unmatched_rows'] += 1
                    stats['unmatched_revenue'] += unmatched_revenue

        conn.commit()

        # Round floats for display.
        for k in ('matched_revenue', 'matched_cogs', 'unmatched_revenue'):
            stats[k] = round(stats[k], 2)
        stats['gross_profit'] = round(stats['matched_revenue'] - stats['matched_cogs'], 2)
        stats['orders_total'] = len(line_items_by_order)
        return {'status': 'ok', **stats}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- PER-ORDER P&L ---------------------------------------------------------

def list_fifo_orders(db, start_date=None, end_date=None, vendor=None,
                     limit=200, offset=0):
    """Per-order P&L list combining matched and unmatched rows."""
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
            SELECT
                order_number, MIN(order_date) AS order_date,
                SUM(total_revenue) AS revenue,
                SUM(total_cogs) AS cogs,
                SUM(quantity_sold) AS units,
                SUM(CASE WHEN is_vendor_sale_cost = 1 THEN quantity_sold ELSE 0 END) AS vendor_sale_units,
                COUNT(DISTINCT sku) AS sku_count,
                CAST(0 AS BIT) AS has_unmatched_only
            FROM fifo_journal
            %s
            GROUP BY order_number
        """ % match_clause, *match_params)
        rows = cursor.fetchall()

        results = {}
        for r in rows:
            results[r[0]] = {
                'order_number': r[0],
                'order_date': r[1].isoformat() if r[1] else None,
                'revenue': _f(r[2]),
                'cogs': _f(r[3]),
                'units': r[4] or 0,
                'vendor_sale_units': r[5] or 0,
                'sku_count': r[6] or 0,
                'unmatched_units': 0,
                'unmatched_revenue': 0.0,
            }

        # Layer in unmatched rows.
        un_clause = (" WHERE " + " AND ".join(where)) if where else ""
        cursor.execute("""
            SELECT order_number, MIN(order_date) AS order_date,
                   SUM(total_revenue) AS revenue, SUM(quantity) AS units,
                   COUNT(DISTINCT sku) AS sku_count
            FROM fifo_unmatched_lines
            %s
            GROUP BY order_number
        """ % un_clause, *params)
        for r in cursor.fetchall():
            order_number = r[0]
            entry = results.get(order_number)
            if entry is None:
                entry = {
                    'order_number': order_number,
                    'order_date': r[1].isoformat() if r[1] else None,
                    'revenue': 0.0,
                    'cogs': 0.0,
                    'units': 0,
                    'vendor_sale_units': 0,
                    'sku_count': r[4] or 0,
                    'unmatched_units': 0,
                    'unmatched_revenue': 0.0,
                }
                results[order_number] = entry
            entry['unmatched_units'] = r[3] or 0
            entry['unmatched_revenue'] = _f(r[2])
            entry['revenue'] += _f(r[2])  # full order revenue includes uncosted lines
            entry['units'] += r[3] or 0

        ordered = sorted(results.values(), key=lambda x: x['order_date'] or '', reverse=True)
        total = len(ordered)
        page = ordered[offset:offset + limit]
        for entry in page:
            entry['gross_profit'] = round(entry['revenue'] - entry['cogs'], 2)
            entry['margin_pct'] = (
                round((entry['revenue'] - entry['cogs']) / entry['revenue'] * 100, 1)
                if entry['revenue'] > 0 else 0
            )
            entry['vendor_sale_share_pct'] = (
                round(entry['vendor_sale_units'] / entry['units'] * 100, 1)
                if entry['units'] > 0 else 0
            )

        return {
            'total': total,
            'limit': limit,
            'offset': offset,
            'orders': page,
        }
    finally:
        conn.close()


def get_fifo_order_detail(db, order_number):
    """Per-line breakdown for one order: matched journal rows + unmatched."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fj.id, fj.sku, fj.quantity_sold, fj.sale_price,
                   fj.unit_cost_cad, fj.total_cogs, fj.total_revenue,
                   fj.is_vendor_sale_cost, fj.order_date,
                   fj.lot_id, pl.invoice_id, pl.invoice_date,
                   pi.vendor, pi.invoice_number, pl.regular_unit_cost_cad
            FROM fifo_journal fj
            LEFT JOIN purchase_lots pl ON pl.id = fj.lot_id
            LEFT JOIN purchase_invoices pi ON pi.id = pl.invoice_id
            WHERE fj.order_number = ?
            ORDER BY fj.sku, pl.invoice_date
        """, order_number)
        cols = [d[0] for d in cursor.description]
        matched = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d['sale_price'] = _f(d['sale_price'])
            d['unit_cost_cad'] = _f(d['unit_cost_cad'])
            d['total_cogs'] = _f(d['total_cogs'])
            d['total_revenue'] = _f(d['total_revenue'])
            d['regular_unit_cost_cad'] = _f(d['regular_unit_cost_cad']) if d['regular_unit_cost_cad'] is not None else None
            d['is_vendor_sale_cost'] = bool(d['is_vendor_sale_cost'])
            d['order_date'] = d['order_date'].isoformat() if d['order_date'] else None
            d['invoice_date'] = d['invoice_date'].isoformat() if d['invoice_date'] else None
            matched.append(d)

        cursor.execute("""
            SELECT id, sku, vendor, quantity, sale_price, total_revenue, reason, order_date
            FROM fifo_unmatched_lines
            WHERE order_number = ?
            ORDER BY sku
        """, order_number)
        cols = [d[0] for d in cursor.description]
        unmatched = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d['sale_price'] = _f(d['sale_price'])
            d['total_revenue'] = _f(d['total_revenue'])
            d['order_date'] = d['order_date'].isoformat() if d['order_date'] else None
            unmatched.append(d)

        revenue = sum(r['total_revenue'] for r in matched) + sum(r['total_revenue'] for r in unmatched)
        cogs = sum(r['total_cogs'] for r in matched)
        units = sum(r['quantity_sold'] for r in matched) + sum(r['quantity'] for r in unmatched)
        vendor_sale_units = sum(r['quantity_sold'] for r in matched if r['is_vendor_sale_cost'])

        return {
            'order_number': order_number,
            'matched_lines': matched,
            'unmatched_lines': unmatched,
            'totals': {
                'revenue': round(revenue, 2),
                'cogs': round(cogs, 2),
                'gross_profit': round(revenue - cogs, 2),
                'margin_pct': round((revenue - cogs) / revenue * 100, 1) if revenue > 0 else 0,
                'units': units,
                'vendor_sale_units': vendor_sale_units,
                'unmatched_units': sum(r['quantity'] for r in unmatched),
            },
        }
    finally:
        conn.close()
