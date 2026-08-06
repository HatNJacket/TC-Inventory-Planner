"""
Shared SKU matching logic for vendor pricelists and invoices.

Priority order for matching a vendor's SKU to a TC SKU:
1. Saved mapping (vendor_sku → shopify_sku in sku_mappings table)
2. Exact SKU match against product_velocity_cache
3. Fuzzy title match against product descriptions

Saved mappings persist across invoice and pricelist uploads per vendor.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _f(val):
    if val is None:
        return 0.0
    return float(val)


def get_saved_mappings(cursor, vendor: str) -> Dict[str, str]:
    """Return a dict of {vendor_sku_upper: shopify_sku} for this vendor."""
    cursor.execute(
        "SELECT vendor_sku, shopify_sku FROM sku_mappings WHERE vendor = ?", vendor
    )
    return {row[0].upper(): row[1] for row in cursor.fetchall()}


def get_shopify_products(cursor, vendor: Optional[str] = None) -> Dict[str, Dict]:
    """Return a dict of {sku_upper: product_info} for the given vendor (or all if None)."""
    if vendor:
        cursor.execute("""
            SELECT sku, product_title, price, cost, current_stock
            FROM product_velocity_cache
            WHERE vendor = ? AND sku IS NOT NULL AND sku != ''
        """, vendor)
    else:
        cursor.execute("""
            SELECT sku, product_title, price, cost, current_stock
            FROM product_velocity_cache
            WHERE sku IS NOT NULL AND sku != ''
        """)
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


def fuzzy_title_match(title: str, products: Dict[str, Dict], top_n: int = 3,
                       min_score: float = 0.2) -> List[Dict]:
    """Return top N products whose titles best match the given title (word overlap)."""
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
        if score > min_score:
            scores.append((score, prod))
    scores.sort(key=lambda x: -x[0])
    return [s[1] for s in scores[:top_n]]


def match_vendor_sku(vendor_sku: str, description: str,
                      saved_mappings: Dict[str, str],
                      shopify_products: Dict[str, Dict]) -> Dict:
    """
    Match a single vendor SKU to a TC SKU using the 3-tier priority.

    Returns a dict with:
    - match_type: 'mapped' | 'exact' | 'suggested' | 'unmatched'
    - matched_sku: the TC SKU matched (empty if unmatched)
    - matched_title: product title
    - suggestions: list of fuzzy suggestions (only for 'suggested' and 'unmatched')
    """
    vendor_sku_upper = (vendor_sku or '').strip().upper()
    result = {
        'match_type': 'unmatched',
        'matched_sku': '',
        'matched_title': '',
        'suggestions': [],
    }

    if not vendor_sku_upper:
        return result

    # 1. Saved mapping
    if vendor_sku_upper in saved_mappings:
        mapped_sku = saved_mappings[vendor_sku_upper]
        if mapped_sku.upper() in shopify_products:
            result['match_type'] = 'mapped'
            result['matched_sku'] = shopify_products[mapped_sku.upper()]['sku']
            result['matched_title'] = shopify_products[mapped_sku.upper()]['title']
            return result

    # 2. Exact SKU match
    if vendor_sku_upper in shopify_products:
        result['match_type'] = 'exact'
        result['matched_sku'] = shopify_products[vendor_sku_upper]['sku']
        result['matched_title'] = shopify_products[vendor_sku_upper]['title']
        return result

    # 3. Fuzzy title match (suggestions only)
    if description:
        fuzzy = fuzzy_title_match(description, shopify_products)
        if fuzzy:
            result['suggestions'] = [{'sku': s['sku'], 'title': s['title']} for s in fuzzy]
            result['match_type'] = 'suggested'
            result['matched_sku'] = fuzzy[0]['sku']
            result['matched_title'] = fuzzy[0]['title']

    return result


def save_mapping(cursor, vendor: str, vendor_sku: str, shopify_sku: str):
    """Upsert a SKU mapping. Uses MERGE to handle both insert and update."""
    if not vendor or not vendor_sku or not shopify_sku:
        return
    cursor.execute("""
        MERGE sku_mappings AS target
        USING (SELECT ? AS vendor, ? AS vendor_sku, ? AS shopify_sku) AS source
        ON target.vendor = source.vendor AND target.vendor_sku = source.vendor_sku
        WHEN MATCHED THEN UPDATE SET shopify_sku = source.shopify_sku
        WHEN NOT MATCHED THEN INSERT (vendor, vendor_sku, shopify_sku)
            VALUES (source.vendor, source.vendor_sku, source.shopify_sku);
    """, vendor, vendor_sku.strip(), shopify_sku.strip())


def list_mappings(db, vendor: Optional[str] = None,
                   search: Optional[str] = None) -> List[Dict]:
    """List SKU mappings, optionally filtered by vendor and/or search text."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = []
        params = []
        if vendor:
            where.append("m.vendor = ?")
            params.append(vendor)
        if search:
            where.append("(m.vendor_sku LIKE ? OR m.shopify_sku LIKE ?)")
            params.append('%' + search + '%')
            params.append('%' + search + '%')
        where_sql = "WHERE " + " AND ".join(where) if where else ""

        cursor.execute(f"""
            SELECT m.id, m.vendor, m.vendor_sku, m.shopify_sku, m.created_at,
                   pvc.product_title
            FROM sku_mappings m
            LEFT JOIN product_velocity_cache pvc ON UPPER(pvc.sku) = UPPER(m.shopify_sku)
            {where_sql}
            ORDER BY m.vendor, m.vendor_sku
        """, *params)

        results = []
        for row in cursor.fetchall():
            results.append({
                'id': row[0],
                'vendor': row[1],
                'vendor_sku': row[2],
                'shopify_sku': row[3],
                'created_at': row[4].isoformat() if row[4] else None,
                'product_title': row[5] or '(SKU not in current inventory)',
            })
        return results
    finally:
        conn.close()


def upsert_mapping(db, vendor: str, vendor_sku: str, shopify_sku: str) -> Dict:
    """Create or update a single mapping."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        save_mapping(cursor, vendor, vendor_sku, shopify_sku)
        conn.commit()
        # Return the upserted record
        cursor.execute("""
            SELECT id, vendor, vendor_sku, shopify_sku, created_at
            FROM sku_mappings WHERE vendor = ? AND vendor_sku = ?
        """, vendor, vendor_sku.strip())
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'vendor': row[1], 'vendor_sku': row[2],
                'shopify_sku': row[3],
                'created_at': row[4].isoformat() if row[4] else None,
            }
        return {'status': 'error', 'message': 'Failed to save mapping'}
    finally:
        conn.close()


def delete_mapping(db, mapping_id: int) -> Dict:
    """Delete a single mapping by ID."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sku_mappings WHERE id = ?", mapping_id)
        conn.commit()
        return {"status": "ok", "deleted": mapping_id}
    finally:
        conn.close()


def bulk_import_mappings(db, vendor: str, csv_content: str,
                          vendor_sku_col: str = 'vendor_sku',
                          tc_sku_col: str = 'tc_sku') -> Dict:
    """Bulk import mappings from a CSV. Returns counts of added/updated/skipped."""
    import csv as csv_mod
    import io

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Get existing shopify SKUs to validate against
        cursor.execute("SELECT sku FROM product_velocity_cache WHERE sku IS NOT NULL")
        valid_skus = {row[0].upper() for row in cursor.fetchall()}

        # Get existing mappings to distinguish add vs update
        cursor.execute("SELECT vendor_sku FROM sku_mappings WHERE vendor = ?", vendor)
        existing = {row[0].upper() for row in cursor.fetchall()}

        reader = csv_mod.DictReader(io.StringIO(csv_content))
        if not reader.fieldnames:
            return {"status": "error", "message": "Empty CSV"}

        # Try both provided and common column name variations
        actual_vendor_col = None
        actual_tc_col = None
        for field in reader.fieldnames:
            fl = field.strip().lower()
            if not actual_vendor_col and fl in (vendor_sku_col.lower(), 'vendor_sku', 'vendor sku', 'supplier_sku', 'supplier sku'):
                actual_vendor_col = field
            if not actual_tc_col and fl in (tc_sku_col.lower(), 'tc_sku', 'tc sku', 'shopify_sku', 'shopify sku', 'our_sku', 'our sku'):
                actual_tc_col = field

        if not actual_vendor_col or not actual_tc_col:
            return {"status": "error",
                    "message": f"Could not find required columns. Expected columns like 'vendor_sku' and 'tc_sku'. Found: {list(reader.fieldnames)}"}

        added = 0
        updated = 0
        skipped = []
        invalid_skus = []

        for row in reader:
            vsku = (row.get(actual_vendor_col) or '').strip()
            tsku = (row.get(actual_tc_col) or '').strip()
            if not vsku or not tsku:
                continue
            if tsku.upper() not in valid_skus:
                invalid_skus.append({'vendor_sku': vsku, 'tc_sku': tsku})
                continue
            save_mapping(cursor, vendor, vsku, tsku)
            if vsku.upper() in existing:
                updated += 1
            else:
                added += 1
                existing.add(vsku.upper())

        conn.commit()

        return {
            "status": "ok",
            "added": added,
            "updated": updated,
            "skipped_count": len(invalid_skus),
            "invalid_skus": invalid_skus[:20],  # limit preview
        }
    finally:
        conn.close()
