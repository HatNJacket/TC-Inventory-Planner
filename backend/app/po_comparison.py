"""
Inventory Planner vs TC Planner recommendation comparison.

Parses an IP purchase order export (CSV or XLSX) and joins it to TC's
velocity cache to produce a per-SKU verdict showing which system
recommended what and why, using each SKU's actual velocity and margin.
"""
import csv
import io
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _to_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(float(str(val).strip().replace(',', '')))
    except (ValueError, TypeError):
        return 0


def _to_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).strip().replace('$', '').replace(',', ''))
    except (ValueError, TypeError):
        return 0.0


def _parse_ip_export(content: str) -> List[Dict]:
    """
    Parse an IP PO export CSV. Expected columns (flexible matching):
    SKU, Ordered (or Quantity), Name (or Title or Description),
    optionally: Ordered Units Cost or Cost.
    """
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("Empty or unreadable file")

    col_map = {}
    for col in reader.fieldnames:
        lower = col.strip().lower()
        if not col_map.get('sku') and lower in ('sku', 'variant sku', 'variant_sku'):
            col_map['sku'] = col
        elif not col_map.get('qty') and lower in (
            'ordered', 'quantity', 'qty', 'order qty', 'replenishment'
        ):
            col_map['qty'] = col
        elif not col_map.get('title') and lower in (
            'name', 'title', 'product title', 'product_title', 'description',
            'product info', 'item', 'item description'
        ):
            col_map['title'] = col
        elif not col_map.get('total_cost') and lower in (
            'ordered units cost', 'total cost', 'line total', 'amount'
        ):
            col_map['total_cost'] = col
        elif not col_map.get('unit_cost') and lower in (
            'unit cost', 'cost', 'unit_cost', 'cost price'
        ):
            col_map['unit_cost'] = col

    if 'sku' not in col_map or 'qty' not in col_map:
        raise ValueError(
            f"File must have SKU and Ordered (or Quantity) columns. Found: {reader.fieldnames}"
        )

    items = []
    for row in reader:
        sku = (row.get(col_map['sku']) or '').strip()
        if not sku:
            continue
        qty = _to_int(row.get(col_map['qty']))
        if qty <= 0:
            continue
        title = (row.get(col_map.get('title', ''), '') or '').strip()
        total_cost = _to_float(row.get(col_map.get('total_cost', ''), 0))
        unit_cost = _to_float(row.get(col_map.get('unit_cost', ''), 0))
        if not unit_cost and total_cost and qty:
            unit_cost = total_cost / qty
        if not total_cost and unit_cost:
            total_cost = unit_cost * qty

        items.append({
            'sku': sku,
            'qty': qty,
            'title': title[:300],
            'total_cost': round(total_cost, 2),
            'unit_cost': round(unit_cost, 2),
        })

    return items


def _load_velocity_data(cursor, skus: List[str]) -> Dict[str, Dict]:
    """Return {sku_upper: {velocity data}} for the given SKUs (case-insensitive match)."""
    if not skus:
        return {}
    placeholders = ','.join(['?'] * len(skus))
    cursor.execute(f"""
        SELECT
            sku, product_title, vendor, current_stock,
            total_sold_365d, total_sold_90d, total_sold_30d,
            avg_daily_velocity, days_of_stock, cost, price,
            CASE WHEN price > 0 THEN (price - cost) / price * 100 ELSE 0 END AS margin_pct,
            tags, inventory_policy
        FROM product_velocity_cache
        WHERE UPPER(sku) IN ({','.join(['UPPER(?)'] * len(skus))})
    """, *skus)
    cols = [d[0] for d in cursor.description]
    result = {}
    for row in cursor.fetchall():
        d = dict(zip(cols, row))
        result[d['sku'].upper()] = {
            'sku': d['sku'],
            'product_title': d['product_title'],
            'vendor': d['vendor'],
            'current_stock': d['current_stock'],
            'total_sold_365d': d['total_sold_365d'],
            'total_sold_90d': d['total_sold_90d'],
            'total_sold_30d': d['total_sold_30d'],
            'avg_daily_velocity': float(d['avg_daily_velocity'] or 0),
            'days_of_stock': float(d['days_of_stock'] or 0),
            'cost': float(d['cost'] or 0),
            'price': float(d['price'] or 0),
            'margin_pct': float(d['margin_pct'] or 0),
            'tags': d['tags'] or '',
            'inventory_policy': d['inventory_policy'] or '',
        }
    return result


def _load_on_order(cursor, skus: List[str]) -> Dict[str, int]:
    """Return {sku_upper: on_order_qty} from open TC stock orders."""
    if not skus:
        return {}
    placeholders = ','.join(['UPPER(?)'] * len(skus))
    cursor.execute(f"""
        SELECT UPPER(soi.sku) AS sku_upper,
               SUM(soi.ordered_qty - soi.received_qty) AS on_order
        FROM stock_order_items soi
        JOIN stock_orders so ON soi.stock_order_id = so.id
        WHERE so.status NOT IN ('closed', 'cancelled')
          AND soi.ordered_qty > soi.received_qty
          AND UPPER(soi.sku) IN ({placeholders})
        GROUP BY UPPER(soi.sku)
    """, *skus)
    return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}


def _classify(ip_item: Optional[Dict], tc_item: Optional[Dict],
               vel: Optional[Dict], on_order: int,
               thresholds: Dict) -> Dict:
    """
    Return a verdict dict for this SKU. ip_item may be None (TC-only suggestion),
    tc_item may be None (IP-only suggestion), vel may be None (not in cache).

    Verdicts:
      order       — passes thresholds, worth adding to the PO
      overstocked — plenty of stock on hand already
      margin_low  — margin below threshold (review manually)
      skip        — dead stock (zero 365d sales) or well below velocity floor
      no_data     — not in velocity cache

    Tag handling:
      - `istock-preorder` is a system-level out-of-stock indicator, not a
        purchasing signal. Ignored entirely.
      - `Special Order` does NOT auto-skip. An item tagged Special Order that
        otherwise qualifies on velocity / margin / DOS is a legitimate order
        candidate — the tag describes the fulfillment model, not "never stock."
      - `On Sale` applies relaxed thresholds: margin floor is halved and max
        DOS is extended by 50%. The supplier discount means thin-margin items
        still pencil out, and extra stock is OK as long as it won't sit for
        months after the sale ends.
    """
    max_dos_base = thresholds['buy_more_max_dos']
    min_vel = thresholds['buy_more_min_velocity']
    min_margin_base = thresholds['buy_more_min_margin_pct']

    ip_qty = ip_item['qty'] if ip_item else 0
    tc_qty = tc_item['qty'] if tc_item else 0

    reasons = []

    if vel is None:
        reasons.append("SKU not found in velocity cache (not in current inventory)")
        verdict = 'no_data'
    else:
        tags_lower = (vel['tags'] or '').lower()
        tagged_special = 'special order' in tags_lower
        on_sale = 'on sale' in tags_lower
        deny_policy = (vel.get('inventory_policy') or '').upper() == 'DENY'
        # istock-preorder is deliberately ignored — it's an OOS flag, not purchasing signal

        # On Sale items get more lenient thresholds:
        # - supplier typically offers us a discount too → thinner margin is OK
        # - we can carry a bit more stock since it'll sell faster at the promo price
        # - velocity floor relaxed because the sale itself will lift demand above
        #   what the pre-sale historical velocity reflects
        if on_sale:
            max_dos = max_dos_base * 1.5  # e.g. 60 → 90
            min_margin = min_margin_base / 2.0  # e.g. 10 → 5
            min_vel_effective = min_vel / 2.0  # e.g. 0.03 → 0.015
        else:
            max_dos = max_dos_base
            min_margin = min_margin_base
            min_vel_effective = min_vel

        # Core metrics
        velocity_ok = vel['avg_daily_velocity'] >= min_vel_effective
        margin_ok = vel['margin_pct'] >= min_margin
        dos_ok = vel['days_of_stock'] < max_dos
        has_stock = vel['current_stock'] > 0
        no_sales_365 = vel['total_sold_365d'] == 0
        recent_sales = vel['total_sold_90d'] > 0

        # Classify in priority order
        # (1) Dead stock — never order
        if no_sales_365:
            verdict = 'skip'
            reasons.append("0 sales in 365d — dead stock risk")
        # (2) Well below velocity floor with no recent activity — skip
        elif not velocity_ok and not recent_sales:
            verdict = 'skip'
            reasons.append(
                f"velocity {vel['avg_daily_velocity']:.4f}/day < {min_vel_effective:.4f} threshold"
                f"{' (relaxed for On Sale)' if on_sale else ''}, no recent sales"
            )
        # (3) Below velocity floor but has recent sales — ambiguous, review manually
        elif not velocity_ok:
            verdict = 'margin_low'  # reuse as the "review manually" bucket
            reasons.append(
                f"velocity {vel['avg_daily_velocity']:.4f}/day < {min_vel_effective:.4f} threshold"
                f"{' (relaxed for On Sale)' if on_sale else ''} — "
                f"but {vel['total_sold_90d']} sale(s) in last 90d suggest possible trend"
            )
        # (4) Margin below (possibly relaxed) floor
        elif not margin_ok:
            verdict = 'margin_low'
            reasons.append(
                f"margin {vel['margin_pct']:.1f}% < {min_margin:.1f}% threshold"
                f"{' (relaxed for On Sale)' if on_sale else ''}"
            )
            if dos_ok and has_stock:
                reasons.append(f"...but DOS is {vel['days_of_stock']:.0f} (low) — review manually")
        # (5) Overstocked relative to the (possibly relaxed) DOS ceiling
        elif not dos_ok and has_stock:
            verdict = 'overstocked'
            reasons.append(
                f"DOS is {vel['days_of_stock']:.0f} days (>= {max_dos:.0f}"
                f"{' relaxed for On Sale' if on_sale else ''}) — plenty of stock already"
            )
        # (6) Passes all filters — order it
        else:
            verdict = 'order'
            base = (f"velocity {vel['avg_daily_velocity']:.4f}/day, "
                    f"DOS {vel['days_of_stock']:.0f}, margin {vel['margin_pct']:.1f}%")
            if on_sale:
                base += " (On Sale: thresholds relaxed)"
            if tagged_special:
                base += " · Special Order tag present but item qualifies on data"
            if not has_stock:
                base += " · 0 stock (TC's SQL would miss this — false negative)"
            reasons.append(base)

        # Modifiers (annotations, not verdict changes)
        if on_order > 0:
            reasons.append(f"already on order (qty {on_order})")
        if deny_policy and vel['current_stock'] <= 0:
            reasons.append("inventory policy = DENY (customers can't order)")

    # Figure out the "recommendation" — what each system is saying
    if ip_item and tc_item:
        agreement = 'both_agree' if abs(ip_qty - tc_qty) == 0 else 'qty_differs'
    elif ip_item:
        agreement = 'ip_only'
    elif tc_item:
        agreement = 'tc_only'
    else:
        agreement = 'neither'

    return {
        'verdict': verdict,  # order | overstocked | skip | margin_low | no_data
        'agreement': agreement,  # both_agree | qty_differs | ip_only | tc_only
        'reasons': reasons,
        'ip_qty': ip_qty,
        'tc_qty': tc_qty,
        'on_order': on_order,
        'velocity': vel,  # full velocity data or None
    }


def compare_po(db, ip_content: str,
                tc_items: Optional[List[Dict]] = None,
                vendor_filter: Optional[str] = None) -> Dict:
    """
    Parse the IP export and produce a per-SKU comparison.

    Args:
        db: Database instance
        ip_content: CSV text of the IP export
        tc_items: Optional list of {sku, qty, title} dicts — if you want to compare
                   against a specific saved TC stock order instead of the live
                   action list. Most useful when diffing a specific PO.
                   If None, we pull the live `increase_buying` list from analytics.
        vendor_filter: Optional vendor name — restrict both sides to this vendor.
    """
    ip_items = _parse_ip_export(ip_content)
    ip_by_sku = {item['sku'].upper(): item for item in ip_items}

    # Get TC recommendations — either from provided items (e.g. a saved PO)
    # or from the live increase_buying list
    if tc_items is None:
        from .analytics import compute_action_lists
        actions = compute_action_lists(db)
        candidates = actions.get('increase_buying', [])
        if vendor_filter:
            candidates = [c for c in candidates if (c.get('vendor') or '').lower() == vendor_filter.lower()]
        # TC's increase_buying doesn't include a quantity — it's a "consider these" list.
        # We signal quantity=None to mean "TC flagged this, go decide the qty".
        tc_items = [{'sku': c['sku'], 'qty': 1, 'title': c.get('product_title', '')} for c in candidates]
    tc_by_sku = {item['sku'].upper(): item for item in tc_items}

    all_skus_upper = set(ip_by_sku) | set(tc_by_sku)

    # Load velocity data + on-order in bulk
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        # The case-insensitive lookup takes the actual SKU strings (not upper)
        sku_list = list({item['sku'] for item in ip_items} | {item['sku'] for item in (tc_items or [])})
        velocity = _load_velocity_data(cursor, sku_list)
        on_order_map = _load_on_order(cursor, sku_list)
    finally:
        conn.close()

    # Read thresholds once
    thresholds = {
        'buy_more_max_dos': db.get_float_setting('buy_more_max_dos', 60.0),
        'buy_more_min_velocity': db.get_float_setting('buy_more_min_velocity', 0.03),
        'buy_more_min_margin_pct': db.get_float_setting('buy_more_min_margin_pct', 10.0),
    }

    # Build the per-SKU comparison
    rows = []
    for sku_upper in all_skus_upper:
        ip_item = ip_by_sku.get(sku_upper)
        tc_item = tc_by_sku.get(sku_upper)
        vel = velocity.get(sku_upper)
        on_order = on_order_map.get(sku_upper, 0)

        # If there's a vendor filter, skip SKUs that belong to other vendors
        if vendor_filter and vel and vel.get('vendor'):
            if (vel['vendor'] or '').lower() != vendor_filter.lower():
                continue

        classification = _classify(ip_item, tc_item, vel, on_order, thresholds)

        rows.append({
            'sku': (ip_item or tc_item)['sku'],
            'title': (vel['product_title'] if vel else None)
                     or (ip_item['title'] if ip_item else '')
                     or (tc_item['title'] if tc_item else ''),
            **classification,
        })

    # Sort: risky over-orders (high $ IP-only skips) first, then qty_differs, then others
    def _sort_key(r):
        agreement_priority = {
            'ip_only': 0, 'qty_differs': 1, 'both_agree': 2, 'tc_only': 3, 'neither': 4,
        }.get(r['agreement'], 5)
        verdict_priority = {
            'skip': 0, 'overstocked': 1, 'margin_low': 2, 'order': 3, 'no_data': 4,
        }.get(r['verdict'], 5)
        dollar_value = -((r.get('ip_qty', 0) * (r['velocity']['cost'] if r.get('velocity') else 0))
                          if r.get('velocity') else 0)
        return (agreement_priority, verdict_priority, dollar_value)

    rows.sort(key=_sort_key)

    # Summary stats
    summary = {
        'ip_total_skus': len(ip_items),
        'ip_total_units': sum(i['qty'] for i in ip_items),
        'ip_total_cost': round(sum(i['total_cost'] for i in ip_items), 2),
        'tc_total_skus': len(tc_items),
        'tc_total_units': sum(i['qty'] for i in tc_items),
        'skus_in_both': sum(1 for r in rows if r['agreement'] in ('both_agree', 'qty_differs')),
        'skus_ip_only': sum(1 for r in rows if r['agreement'] == 'ip_only'),
        'skus_tc_only': sum(1 for r in rows if r['agreement'] == 'tc_only'),
        'verdict_counts': {
            v: sum(1 for r in rows if r['verdict'] == v)
            for v in ('order', 'overstocked', 'margin_low', 'skip', 'no_data')
        },
        # Dollar value of IP's over-orders (IP-only items we'd skip)
        'ip_skip_dollars': round(sum(
            r['ip_qty'] * (r['velocity']['cost'] if r.get('velocity') else 0)
            for r in rows
            if r['agreement'] == 'ip_only' and r['verdict'] in ('skip', 'overstocked')
        ), 2),
    }

    return {
        'rows': rows,
        'summary': summary,
        'thresholds': thresholds,
    }
