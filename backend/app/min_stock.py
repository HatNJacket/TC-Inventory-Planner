"""
Per-SKU minimum stock levels.

Persisted in ``sku_min_stock`` (separate from ``product_velocity_cache``
because the cache is DELETE+rebuilt from Shopify on every refresh — keeping
the user-set values in their own table means they survive). Cache rebuilds
mirror the value in ``product_velocity_cache.min_stock_level`` so the
replenishment row reads atomically.

Behaviour: when ``current_stock + on_order < min_stock``, the replenishment
math adds a ``top_to_min`` floor so the next PO covers the gap. The floor is
applied alongside the waiter-floor and is overridden by the same status rules
(Discontinued / Replacement Part / DENY+OOS still win).
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def list_min_stock(db, only_set: bool = True) -> List[Dict]:
    """List all SKUs with a min stock setting. ``only_set=True`` (default)
    skips rows where ``min_stock <= 0`` so the alias panel stays tight."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        sql = """SELECT sku, min_stock, notes, updated_at FROM sku_min_stock"""
        if only_set:
            sql += " WHERE min_stock > 0"
        sql += " ORDER BY sku"
        cursor.execute(sql)
        results = []
        for row in cursor.fetchall():
            results.append({
                'sku': row[0],
                'min_stock': int(row[1] or 0),
                'notes': row[2],
                'updated_at': row[3].isoformat() if row[3] else None,
            })
        return results
    finally:
        conn.close()


def get_min_stock_map(db) -> Dict[str, int]:
    """Return ``{sku: min_stock}`` for all SKUs with min_stock > 0. Used by
    forecasting + cache rebuild to avoid N+1 lookups."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT sku, min_stock FROM sku_min_stock WHERE min_stock > 0")
        return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}
    finally:
        conn.close()


def set_min_stock(db, sku: str, min_stock: int, notes: Optional[str] = None) -> Dict:
    """Set or clear the min stock for one SKU. ``min_stock=0`` removes the
    row so the SKU goes back to pure velocity-driven replenishment."""
    if not sku:
        raise ValueError("sku is required")
    min_stock = max(0, int(min_stock))

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        if min_stock == 0:
            cursor.execute("DELETE FROM sku_min_stock WHERE sku = ?", sku)
            cursor.execute(
                "UPDATE product_velocity_cache SET min_stock_level = 0 WHERE sku = ?", sku
            )
        else:
            cursor.execute("""
                MERGE sku_min_stock AS target
                USING (SELECT ? AS sku, ? AS min_stock, ? AS notes) AS source
                ON target.sku = source.sku
                WHEN MATCHED THEN UPDATE SET min_stock = source.min_stock,
                                              notes = source.notes,
                                              updated_at = GETUTCDATE()
                WHEN NOT MATCHED THEN INSERT (sku, min_stock, notes)
                                      VALUES (source.sku, source.min_stock, source.notes);
            """, sku, min_stock, notes)
            cursor.execute(
                "UPDATE product_velocity_cache SET min_stock_level = ? WHERE sku = ?",
                min_stock, sku,
            )
        conn.commit()
        return {'status': 'ok', 'sku': sku, 'min_stock': min_stock}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bulk_set_min_stock(db, items: List[Dict]) -> Dict:
    """Set min stock for many SKUs at once. ``items`` is a list of
    ``{sku, min_stock, notes?}``. Used by the bulk-set UI on the
    Replenishment page."""
    if not items:
        return {'status': 'ok', 'updated': 0, 'cleared': 0}

    conn = db._get_connection()
    updated = 0
    cleared = 0
    try:
        cursor = conn.cursor()
        for item in items:
            sku = item.get('sku')
            if not sku:
                continue
            min_stock = max(0, int(item.get('min_stock') or 0))
            notes = item.get('notes')
            if min_stock == 0:
                cursor.execute("DELETE FROM sku_min_stock WHERE sku = ?", sku)
                cursor.execute(
                    "UPDATE product_velocity_cache SET min_stock_level = 0 WHERE sku = ?", sku
                )
                cleared += 1
            else:
                cursor.execute("""
                    MERGE sku_min_stock AS target
                    USING (SELECT ? AS sku, ? AS min_stock, ? AS notes) AS source
                    ON target.sku = source.sku
                    WHEN MATCHED THEN UPDATE SET min_stock = source.min_stock,
                                                  notes = source.notes,
                                                  updated_at = GETUTCDATE()
                    WHEN NOT MATCHED THEN INSERT (sku, min_stock, notes)
                                          VALUES (source.sku, source.min_stock, source.notes);
                """, sku, min_stock, notes)
                cursor.execute(
                    "UPDATE product_velocity_cache SET min_stock_level = ? WHERE sku = ?",
                    min_stock, sku,
                )
                updated += 1
        conn.commit()
        return {'status': 'ok', 'updated': updated, 'cleared': cleared}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_min_stock_floor(replenish_qty: int, min_stock: int,
                          current_stock: int, on_order: int) -> int:
    """Hard floor for the replenishment quantity. Mirrors the waiter-floor
    pattern in forecasting.calculate_replenishment so the math matches
    whether the value comes from a fresh forecast or a post-mutation recalc."""
    if min_stock <= 0:
        return replenish_qty
    top_to_min = max(0, int(min_stock) - int(current_stock or 0) - int(on_order or 0))
    return max(int(replenish_qty), top_to_min)
