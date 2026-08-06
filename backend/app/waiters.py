"""
Back-in-stock waiter request tracking.

Ingests the "iStock Report 1" individual customer export from the
Now In Stock Shopify app. Each upload creates a new snapshot with individual
rows; the latest snapshot per SKU drives replenishment demand signals.

Schema (see DB_MIGRATION below):
- waiter_snapshots: one row per upload
- waiter_requests: individual customer-level rows

Conversion dampener: when used as a demand signal for ordering, apply a
configurable dampener (default 0.60 = 60%) and round up. So 25 waiters → 15 demand units.
"""
import csv
import io
import logging
from datetime import datetime
from math import ceil
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


DB_MIGRATION = """
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'waiter_snapshots')
CREATE TABLE waiter_snapshots (
    id INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    source_filename NVARCHAR(300) NULL,
    total_requests INT NOT NULL DEFAULT 0,
    unique_skus INT NOT NULL DEFAULT 0,
    imported_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    notes NVARCHAR(MAX) NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'waiter_requests')
CREATE TABLE waiter_requests (
    id INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_id INT NOT NULL,
    sku NVARCHAR(100) NOT NULL,
    product_title NVARCHAR(500) NULL,
    customer_email NVARCHAR(300) NULL,
    date_added DATETIME2 NULL,
    shopify_sku NVARCHAR(100) NULL,
    CONSTRAINT FK_waiter_requests_snapshot FOREIGN KEY (snapshot_id)
        REFERENCES waiter_snapshots(id)
);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_waiter_requests_snapshot')
    CREATE INDEX IX_waiter_requests_snapshot ON waiter_requests(snapshot_id);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_waiter_requests_sku')
    CREATE INDEX IX_waiter_requests_sku ON waiter_requests(sku);

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_waiter_requests_shopify_sku')
    CREATE INDEX IX_waiter_requests_shopify_sku ON waiter_requests(shopify_sku);
"""


# Default dampener — 60% conversion assumption. Roughly 40% of waiters find the
# product elsewhere or lose interest before we restock. Adjust based on real data.
DEFAULT_CONVERSION_RATE = 0.60


def compute_demand_boost(waiter_count: int,
                          conversion_rate: float = DEFAULT_CONVERSION_RATE) -> int:
    """
    Convert a raw waiter count into a demand boost for ordering.
    Rounds up so 1 waiter → 1 unit of demand.
    """
    if not waiter_count or waiter_count < 1:
        return 0
    return max(1, ceil(waiter_count * conversion_rate))


def _parse_datetime(val) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD HH:MM:SS' or similar from the report."""
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Try several common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_csv_content(content: str) -> List[Dict]:
    """
    Parse the individual customer report CSV.
    Expected columns: "Product Info", "SKU", "Customer Email", "Date Added".

    Returns a list of dicts with keys: sku, product_title, customer_email, date_added.
    """
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("Empty or unreadable CSV")

    # Build column map (case-insensitive and tolerant of variants)
    col_map = {}
    for col in reader.fieldnames:
        lower = col.strip().lower()
        if lower in ("sku", "variant sku"):
            col_map["sku"] = col
        elif lower in ("product info", "product", "product info ", "title", "product title"):
            col_map["product_title"] = col
        elif lower in ("customer email", "email"):
            col_map["customer_email"] = col
        elif lower in ("date added", "date", "created at", "added"):
            col_map["date_added"] = col

    if "sku" not in col_map:
        raise ValueError(
            f"CSV must have a SKU column. Found columns: {reader.fieldnames}"
        )

    rows = []
    for row in reader:
        sku = (row.get(col_map["sku"]) or "").strip()
        if not sku:
            continue
        rows.append({
            "sku": sku,
            "product_title": (row.get(col_map.get("product_title", ""), "") or "").strip(),
            "customer_email": (row.get(col_map.get("customer_email", ""), "") or "").strip().lower(),
            "date_added": _parse_datetime(row.get(col_map.get("date_added", ""))),
        })
    return rows


def _dedupe_rows(rows: List[Dict]) -> (List[Dict], int):
    """
    De-duplicate by (sku, customer_email). Keeps the earliest date_added.
    Returns (deduplicated_rows, count_of_dupes_removed).

    Rows without an email are kept as-is (no way to dedupe without a key).
    """
    seen = {}
    result = []
    dupes = 0
    for r in rows:
        email = r.get("customer_email") or ""
        if not email:
            result.append(r)
            continue
        key = (r["sku"].upper(), email.lower())
        if key in seen:
            dupes += 1
            # Keep the earliest date_added
            existing_idx = seen[key]
            existing_date = result[existing_idx].get("date_added")
            new_date = r.get("date_added")
            if new_date and existing_date and new_date < existing_date:
                result[existing_idx]["date_added"] = new_date
            elif new_date and not existing_date:
                result[existing_idx]["date_added"] = new_date
        else:
            seen[key] = len(result)
            result.append(r)
    return result, dupes


def _match_to_shopify(rows: List[Dict], cursor) -> Dict[str, Optional[str]]:
    """
    For each unique SKU in rows, try to find the corresponding Shopify SKU
    (exact case-insensitive match against product_velocity_cache).
    Returns {report_sku_upper: shopify_sku or None}.
    """
    unique_skus = {r["sku"].upper() for r in rows}
    if not unique_skus:
        return {}

    cursor.execute("""
        SELECT sku FROM product_velocity_cache
        WHERE sku IS NOT NULL AND sku != ''
    """)
    shopify_skus = {}  # upper -> actual
    for row in cursor.fetchall():
        shopify_skus[row[0].upper()] = row[0]

    return {sku_upper: shopify_skus.get(sku_upper) for sku_upper in unique_skus}


def preview_upload(db, csv_content: str, snapshot_date: Optional[str] = None) -> Dict:
    """
    Parse the CSV and return a preview without inserting anything.
    Shows matched/unmatched counts, deduplication info, waiter counts per SKU.
    """
    rows = _parse_csv_content(csv_content)
    if not rows:
        return {"status": "error", "message": "No valid rows found in CSV"}

    dedupe_rows, dupes = _dedupe_rows(rows)

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        sku_match_map = _match_to_shopify(dedupe_rows, cursor)
    finally:
        conn.close()

    # Build per-SKU aggregation for preview
    aggregated = {}
    for r in dedupe_rows:
        sku_upper = r["sku"].upper()
        shopify_sku = sku_match_map.get(sku_upper)
        if sku_upper not in aggregated:
            aggregated[sku_upper] = {
                "report_sku": r["sku"],
                "shopify_sku": shopify_sku,
                "product_title": r.get("product_title", ""),
                "matched": shopify_sku is not None,
                "count": 0,
                "oldest_request": None,
                "newest_request": None,
            }
        agg = aggregated[sku_upper]
        agg["count"] += 1
        date_added = r.get("date_added")
        if date_added:
            if agg["oldest_request"] is None or date_added < agg["oldest_request"]:
                agg["oldest_request"] = date_added
            if agg["newest_request"] is None or date_added > agg["newest_request"]:
                agg["newest_request"] = date_added

    # Serialize datetimes
    per_sku = []
    for sku_upper, agg in sorted(aggregated.items(), key=lambda x: -x[1]["count"]):
        per_sku.append({
            **agg,
            "oldest_request": agg["oldest_request"].isoformat() if agg["oldest_request"] else None,
            "newest_request": agg["newest_request"].isoformat() if agg["newest_request"] else None,
            "demand_boost": compute_demand_boost(agg["count"]),
        })

    matched_count = sum(1 for s in per_sku if s["matched"])
    unmatched_count = sum(1 for s in per_sku if not s["matched"])

    return {
        "status": "ok",
        "total_rows_parsed": len(rows),
        "duplicates_removed": dupes,
        "total_requests_after_dedupe": len(dedupe_rows),
        "unique_skus": len(aggregated),
        "matched_skus": matched_count,
        "unmatched_skus": unmatched_count,
        "snapshot_date": snapshot_date or datetime.utcnow().date().isoformat(),
        "per_sku": per_sku,
    }


def import_snapshot(db, csv_content: str, source_filename: Optional[str],
                     snapshot_date: Optional[str] = None,
                     notes: Optional[str] = None) -> Dict:
    """
    Import a new waiter snapshot. Wipes ALL prior snapshots + requests
    in the same transaction first, so the incoming list is the only
    one in the database. This matches the operational model where each
    upload is a complete export of every open request — anyone not on
    the new list is no longer waiting on anything.

    Wipe + insert is atomic: if anything fails mid-import, the
    connection closes without committing and SQL Server rolls the
    transaction back, leaving the old data intact.

    Returns the inserted snapshot info + counts, including how many
    prior rows were cleared so the UI can surface that.
    """
    rows = _parse_csv_content(csv_content)
    if not rows:
        raise ValueError("No valid rows found in CSV")

    dedupe_rows, dupes = _dedupe_rows(rows)

    # Parse snapshot_date
    if snapshot_date:
        try:
            snap_date = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
        except ValueError:
            snap_date = datetime.utcnow().date()
    else:
        snap_date = datetime.utcnow().date()

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Wipe prior data first. waiter_requests references waiter_snapshots
        # via snapshot_id so we delete children before parents.
        cursor.execute("SELECT COUNT(*) FROM waiter_requests")
        cleared_requests = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM waiter_snapshots")
        cleared_snapshots = cursor.fetchone()[0] or 0
        cursor.execute("DELETE FROM waiter_requests")
        cursor.execute("DELETE FROM waiter_snapshots")

        # Match SKUs
        sku_match_map = _match_to_shopify(dedupe_rows, cursor)

        # Compute aggregates for the snapshot row
        unique_skus = len({r["sku"].upper() for r in dedupe_rows})
        total_requests = len(dedupe_rows)

        # Insert snapshot
        cursor.execute("""
            INSERT INTO waiter_snapshots
            (snapshot_date, source_filename, total_requests, unique_skus, notes)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?)
        """, snap_date, (source_filename or "")[:300], total_requests, unique_skus,
             notes or None)
        snapshot_id = cursor.fetchone()[0]

        # Insert individual rows
        for r in dedupe_rows:
            sku_upper = r["sku"].upper()
            shopify_sku = sku_match_map.get(sku_upper)
            cursor.execute("""
                INSERT INTO waiter_requests
                (snapshot_id, sku, product_title, customer_email, date_added, shopify_sku)
                VALUES (?, ?, ?, ?, ?, ?)
            """, snapshot_id,
                 r["sku"][:100],
                 (r.get("product_title", "") or "")[:500],
                 (r.get("customer_email", "") or "")[:300] or None,
                 r.get("date_added"),
                 shopify_sku)

        conn.commit()
        logger.info(
            "Imported waiter snapshot %d: %d requests across %d SKUs "
            "(%d dupes removed, %d prior request(s) / %d prior snapshot(s) cleared)",
            snapshot_id, total_requests, unique_skus, dupes,
            cleared_requests, cleared_snapshots,
        )

        return {
            "status": "ok",
            "snapshot_id": snapshot_id,
            "snapshot_date": snap_date.isoformat(),
            "total_requests": total_requests,
            "unique_skus": unique_skus,
            "duplicates_removed": dupes,
            "cleared_prior_requests": cleared_requests,
            "cleared_prior_snapshots": cleared_snapshots,
        }
    finally:
        conn.close()


def list_snapshots(db) -> List[Dict]:
    """List all waiter snapshots, newest first."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, snapshot_date, source_filename, total_requests,
                   unique_skus, imported_at, notes
            FROM waiter_snapshots
            ORDER BY snapshot_date DESC, id DESC
        """)
        cols = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d["snapshot_date"] = d["snapshot_date"].isoformat() if d["snapshot_date"] else None
            d["imported_at"] = d["imported_at"].isoformat() if d["imported_at"] else None
            results.append(d)
        return results
    finally:
        conn.close()


def delete_snapshot(db, snapshot_id: int) -> Dict:
    """Delete a snapshot and all its waiter records."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM waiter_requests WHERE snapshot_id = ?", snapshot_id)
        req_deleted = cursor.rowcount
        cursor.execute("DELETE FROM waiter_snapshots WHERE id = ?", snapshot_id)
        conn.commit()
        return {
            "status": "ok",
            "deleted_snapshot": snapshot_id,
            "requests_deleted": req_deleted,
        }
    finally:
        conn.close()


def _latest_snapshot_id(cursor) -> Optional[int]:
    """Return the id of the most recent snapshot, or None."""
    cursor.execute("""
        SELECT TOP 1 id FROM waiter_snapshots
        ORDER BY snapshot_date DESC, id DESC
    """)
    row = cursor.fetchone()
    return row[0] if row else None


def current_waiters_per_sku(db, vendor: Optional[str] = None) -> List[Dict]:
    """
    Return per-SKU waiter counts from the latest snapshot.
    Optionally filter to a specific vendor.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        latest = _latest_snapshot_id(cursor)
        if not latest:
            return []

        vendor_filter = ""
        params = [latest]
        if vendor:
            vendor_filter = " AND pvc.vendor = ?"
            params.append(vendor)

        cursor.execute(f"""
            SELECT
                ISNULL(wr.shopify_sku, wr.sku) AS sku,
                pvc.product_title,
                pvc.vendor,
                pvc.current_stock,
                COUNT(*) AS waiter_count,
                MIN(wr.date_added) AS oldest_request,
                MAX(wr.date_added) AS newest_request,
                SUM(CASE WHEN wr.shopify_sku IS NOT NULL THEN 1 ELSE 0 END) AS matched_count
            FROM waiter_requests wr
            LEFT JOIN product_velocity_cache pvc
                ON UPPER(pvc.sku) = UPPER(ISNULL(wr.shopify_sku, wr.sku))
            WHERE wr.snapshot_id = ?{vendor_filter}
            GROUP BY ISNULL(wr.shopify_sku, wr.sku), pvc.product_title, pvc.vendor, pvc.current_stock
            ORDER BY COUNT(*) DESC
        """, *params)

        cols = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d["oldest_request"] = d["oldest_request"].isoformat() if d["oldest_request"] else None
            d["newest_request"] = d["newest_request"].isoformat() if d["newest_request"] else None
            d["demand_boost"] = compute_demand_boost(d["waiter_count"] or 0)
            d["matched"] = (d.pop("matched_count", 0) or 0) > 0
            results.append(d)
        return results
    finally:
        conn.close()


def waiter_counts_map(db) -> Dict[str, int]:
    """
    Return a dict {shopify_sku_upper: waiter_count} from the latest snapshot.
    Used to enrich action lists and stock order items cheaply.
    Only includes rows that matched to a Shopify SKU.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        latest = _latest_snapshot_id(cursor)
        if not latest:
            return {}

        cursor.execute("""
            SELECT shopify_sku, COUNT(*) AS cnt
            FROM waiter_requests
            WHERE snapshot_id = ? AND shopify_sku IS NOT NULL AND shopify_sku != ''
            GROUP BY shopify_sku
        """, latest)

        return {row[0].upper(): row[1] for row in cursor.fetchall()}
    finally:
        conn.close()


def get_waiters_for_sku(db, sku: str,
                         include_history: bool = False) -> Dict:
    """
    Get detailed waiter info for a single SKU.
    Returns current waiter list (from latest snapshot) and optionally historical count per snapshot.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        latest = _latest_snapshot_id(cursor)

        current = {"sku": sku, "waiter_count": 0, "waiters": []}
        if latest:
            cursor.execute("""
                SELECT id, customer_email, date_added, product_title
                FROM waiter_requests
                WHERE snapshot_id = ?
                  AND (UPPER(shopify_sku) = UPPER(?) OR UPPER(sku) = UPPER(?))
                ORDER BY date_added DESC
            """, latest, sku, sku)
            for row in cursor.fetchall():
                current["waiters"].append({
                    "id": row[0],
                    "customer_email": row[1],
                    "date_added": row[2].isoformat() if row[2] else None,
                    "product_title": row[3],
                })
            current["waiter_count"] = len(current["waiters"])
            current["demand_boost"] = compute_demand_boost(current["waiter_count"])

        history = []
        if include_history:
            cursor.execute("""
                SELECT ws.snapshot_date, ws.id,
                    (SELECT COUNT(*) FROM waiter_requests wr
                     WHERE wr.snapshot_id = ws.id
                       AND (UPPER(wr.shopify_sku) = UPPER(?) OR UPPER(wr.sku) = UPPER(?))) AS cnt
                FROM waiter_snapshots ws
                ORDER BY ws.snapshot_date DESC, ws.id DESC
            """, sku, sku)
            for row in cursor.fetchall():
                history.append({
                    "snapshot_date": row[0].isoformat() if row[0] else None,
                    "snapshot_id": row[1],
                    "waiter_count": row[2] or 0,
                })

        return {**current, "history": history if include_history else None}
    finally:
        conn.close()
