"""
Inventory Planner API client.

Reads stock orders (purchase orders) from Inventory Planner for import into TC Planner.

Auth: uses IP_ACCOUNT_ID and IP_API_KEY environment variables.
Docs: https://help.inventory-planner.com/en/articles/6852591-inventory-planner-stock-orders-api

Status values in IP: OPEN, CLOSED, RECEIVED, CANCELLED, draft, active, etc.
We map these to TC's statuses: 'open', 'closed'. Partial receipt state is derived
from per-item received_qty vs replenishment (ordered) qty, not the order-level status.
"""
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

IP_BASE_URL = "https://app.inventory-planner.com"
IP_API_PATH = "/api/v1"
PAGE_SIZE = 100  # IP allows up to 1000 but smaller batches are kinder
MAX_PAGES = 50   # hard cap to prevent runaway loops — up to 5000 POs


def _get_auth_headers() -> Dict[str, str]:
    """Return the auth headers required by the IP API. Raises if env vars are missing."""
    account_id = os.getenv("IP_ACCOUNT_ID", "").strip()
    api_key = os.getenv("IP_API_KEY", "").strip()
    if not account_id or not api_key:
        raise RuntimeError(
            "Missing IP_ACCOUNT_ID and/or IP_API_KEY environment variables. "
            "Set these in Azure App Service configuration."
        )
    return {
        "Authorization": api_key,
        "Account": account_id,
        "Accept": "application/json",
    }


def _normalize_ip_status(ip_status: Optional[str]) -> str:
    """Map IP status → TC status.

    IP uses statuses like: OPEN, open (uploaded), ordered, waiting for invoice,
    paid waiting to ship, shipped, partial_received, partial_shipped, CLOSED, CANCELLED.
    TC uses: open, ordered, ordered_invoice, paid, shipped, partial_received,
    partial_shipped, closed.
    """
    if not ip_status:
        return "open"
    s = str(ip_status).strip().lower()

    # Explicit closed/cancelled
    if s in ("closed", "cancelled", "canceled", "received"):
        return "closed"

    # Match order lifecycle phrases regardless of exact wording
    if "partial" in s and "receiv" in s:
        return "partial_received"
    if "partial" in s and "ship" in s:
        return "partial_shipped"
    if "ship" in s:
        return "shipped"
    if "paid" in s or "waiting to ship" in s:
        return "paid"
    if "invoice" in s or "ordered" in s:
        return "ordered_invoice"
    if "upload" in s:
        return "ordered"

    # Default — OPEN, draft, active, unknown → 'open'
    return "open"


def _extract_int_reference(ip_reference: Optional[str],
                             fallback: int) -> int:
    """
    Extract a numeric reference number from IP's reference string.
    IP references may be purely numeric (845, 846) or prefixed (PO-845, PO0045).
    Returns the first run of digits found, or the fallback if none found.
    """
    if not ip_reference:
        return fallback
    import re
    match = re.search(r'\d+', str(ip_reference))
    if match:
        try:
            return int(match.group())
        except (ValueError, OverflowError):
            pass
    return fallback


def _parse_iso_date(val) -> Optional[str]:
    """Parse various IP date formats to ISO date string (YYYY-MM-DD)."""
    if not val:
        return None
    try:
        if isinstance(val, (int, float)):
            # Unix timestamp
            return datetime.utcfromtimestamp(val).date().isoformat()
        s = str(val).strip()
        if not s:
            return None
        # RFC822: "2018-03-15T10:00:00+01:00"
        if 'T' in s:
            return s.split('T')[0]
        return s[:10]
    except Exception:
        return None


def _extract_money(val) -> float:
    """IP sometimes returns costs as strings or nested objects. Coerce to float."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip().replace('$', '').replace(',', ''))
    except (ValueError, TypeError):
        return 0.0


async def fetch_purchase_orders(include_closed: bool = False,
                                 progress_cb=None) -> List[Dict]:
    """
    Fetch all purchase orders from IP with pagination.
    Each PO in the returned list includes a fully-populated 'items' array.

    Args:
        include_closed: if False, only fetch orders where status != CLOSED
        progress_cb: optional callable(stage: str, current: int, total: int)

    Returns a list of IP purchase order dicts (as returned by the API).
    """
    headers = _get_auth_headers()
    all_orders = []
    page = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while page < MAX_PAGES:
            params = {
                "limit": PAGE_SIZE,
                "page": page,
                # Fields we need — restricting keeps the payload small
                "fields": "id,reference,extra_reference,status,created_date,expected_date,"
                          "received_date,last_modified,vendor,warehouse,notes,currency,"
                          "items,total,total_ordered,total_received,type",
            }
            if not include_closed:
                # Note: IP filter uses _ne operator. We want status != CLOSED.
                params["status_ne"] = "CLOSED"

            url = f"{IP_BASE_URL}{IP_API_PATH}/purchase-orders"
            try:
                resp = await client.get(url, headers=headers, params=params)
            except httpx.RequestError as e:
                raise RuntimeError(f"IP API request failed: {e}")

            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"IP API authentication failed ({resp.status_code}). "
                    "Check IP_ACCOUNT_ID and IP_API_KEY."
                )
            if resp.status_code != 200:
                raise RuntimeError(f"IP API error {resp.status_code}: {resp.text[:500]}")

            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError(f"IP API returned non-JSON response: {resp.text[:200]}")

            orders = data.get("purchase-orders", [])
            if not orders:
                break  # no more pages

            all_orders.extend(orders)

            meta = data.get("meta", {})
            total = meta.get("total", 0)
            if progress_cb:
                progress_cb("fetching", len(all_orders), total)

            # Stop when we've fetched everything
            if total and len(all_orders) >= total:
                break
            # If the page returned fewer than PAGE_SIZE, we're done
            if len(orders) < PAGE_SIZE:
                break

            page += 1

    logger.info(f"Fetched {len(all_orders)} purchase orders from Inventory Planner "
                f"(include_closed={include_closed})")
    return all_orders


def map_ip_order_to_tc(ip_order: Dict) -> Dict:
    """Convert an IP purchase order to the shape TC expects for insertion.

    Returns a dict with header fields + an 'items' array ready for DB insert.
    """
    ip_id = ip_order.get("id", "")
    ip_ref = ip_order.get("reference", "") or ip_order.get("extra_reference", "")
    ip_status = ip_order.get("status", "")
    tc_status = _normalize_ip_status(ip_status)
    vendor = ip_order.get("vendor", "") or "Unknown"
    currency = (ip_order.get("currency", "") or "CAD").upper()

    # Build a notes string that preserves traceability back to IP
    trace_parts = []
    if ip_ref:
        trace_parts.append(f"IP Ref: {ip_ref}")
    if ip_id:
        trace_parts.append(f"IP ID: {ip_id}")
    if ip_status:
        trace_parts.append(f"IP Status: {ip_status}")
    trace_line = " | ".join(trace_parts)

    existing_notes = (ip_order.get("notes") or "").strip()
    if existing_notes and trace_line:
        notes = f"{trace_line}\n\n{existing_notes}"
    else:
        notes = trace_line or existing_notes or None

    # Map line items
    items = []
    for ip_item in (ip_order.get("items") or []):
        sku = (ip_item.get("sku") or "").strip()
        if not sku:
            continue  # skip items without a SKU — can't reconcile in TC
        items.append({
            "sku": sku,
            "product_title": (ip_item.get("title") or "").strip()[:500],
            "variant_title": "",
            "barcode": (ip_item.get("barcode") or "").strip()[:100] or None,
            "ordered_qty": int(_extract_money(ip_item.get("replenishment")) or 0),
            "received_qty": int(_extract_money(ip_item.get("received")) or 0),
            "unit_cost": _extract_money(ip_item.get("cost_price")),
            "unit_price": 0.0,
        })

    total_cost = sum(it["ordered_qty"] * it["unit_cost"] for it in items)

    return {
        "status": tc_status,
        "vendor": vendor,
        "currency": currency,
        "expected_date": _parse_iso_date(ip_order.get("expected_date")),
        "created_date": _parse_iso_date(ip_order.get("created_date")),
        "notes": notes,
        "total_cost": round(total_cost, 2),
        "ip_id": ip_id,
        "ip_reference": ip_ref,
        "items": items,
    }


def clear_all_stock_orders(db) -> Dict:
    """Delete every stock order and its line items. Returns counts deleted."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock_order_items")
        item_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM stock_orders")
        order_count = cursor.fetchone()[0]

        # Delete items first due to FK
        cursor.execute("DELETE FROM stock_order_items")
        cursor.execute("DELETE FROM stock_orders")
        conn.commit()

        logger.info(f"Cleared {order_count} stock orders and {item_count} line items")
        return {
            "status": "ok",
            "orders_deleted": order_count,
            "items_deleted": item_count,
        }
    finally:
        conn.close()


async def import_from_ip(db, include_closed: bool = False) -> Dict:
    """
    Fetch purchase orders from IP and insert them into TC's stock_orders tables.
    Uses IP's reference number as TC's reference_number so the two systems stay aligned.
    When IP's reference isn't numeric (or collides), falls back to a fresh sequential number.

    Does NOT clear existing orders — call clear_all_stock_orders first if you want
    a fresh import. This function is safe to call against an empty table.
    """
    ip_orders = await fetch_purchase_orders(include_closed=include_closed)

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Fallback sequential counter — starts above any existing TC ref
        cursor.execute("SELECT ISNULL(MAX(reference_number), 0) FROM stock_orders")
        fallback_next = (cursor.fetchone()[0] or 0) + 1

        # Track refs used in this batch to prevent collisions if two IP orders
        # parse to the same number (e.g. one is "845" and another is "PO845" with prefix stripped)
        used_refs = set()

        # Also load existing TC refs so we don't collide with those either
        cursor.execute("SELECT reference_number FROM stock_orders")
        existing_refs = {row[0] for row in cursor.fetchall()}

        imported = 0
        skipped = 0
        total_items = 0
        total_cost_cad = 0.0
        errors = []
        used_fallback = 0

        for ip_order in ip_orders:
            try:
                mapped = map_ip_order_to_tc(ip_order)

                if not mapped["items"]:
                    skipped += 1
                    continue

                # Prefer IP's numeric reference; fall back to sequential on collision or parse failure
                ip_ref_extracted = _extract_int_reference(mapped["ip_reference"], 0)
                ref_num = ip_ref_extracted
                if not ref_num or ref_num in used_refs or ref_num in existing_refs:
                    # Use fallback, bump past any collisions
                    while fallback_next in used_refs or fallback_next in existing_refs:
                        fallback_next += 1
                    ref_num = fallback_next
                    fallback_next += 1
                    used_fallback += 1
                used_refs.add(ref_num)

                # Insert header
                cursor.execute(
                    """INSERT INTO stock_orders
                       (reference_number, status, vendor, currency, expected_date,
                        notes, total_cost, created_at, updated_at, closed_at)
                       OUTPUT INSERTED.id
                       VALUES (?, ?, ?, ?, ?, ?, ?,
                               ISNULL(?, GETUTCDATE()), GETUTCDATE(),
                               CASE WHEN ? = 'closed' THEN GETUTCDATE() ELSE NULL END)""",
                    ref_num,
                    mapped["status"],
                    mapped["vendor"],
                    mapped["currency"],
                    mapped["expected_date"],
                    mapped["notes"],
                    mapped["total_cost"],
                    mapped["created_date"],
                    mapped["status"],
                )
                order_id = cursor.fetchone()[0]

                # Insert line items
                for it in mapped["items"]:
                    cursor.execute(
                        """INSERT INTO stock_order_items
                           (stock_order_id, product_title, variant_title, sku,
                            barcode, vendor, ordered_qty, received_qty,
                            unit_cost, unit_price)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        order_id,
                        it["product_title"],
                        it["variant_title"],
                        it["sku"],
                        it["barcode"],
                        mapped["vendor"],
                        it["ordered_qty"],
                        it["received_qty"],
                        it["unit_cost"],
                        it["unit_price"],
                    )

                imported += 1
                total_items += len(mapped["items"])
                total_cost_cad += mapped["total_cost"] if mapped["currency"] == "CAD" else 0

            except Exception as e:
                logger.error(f"Failed to import IP order {ip_order.get('id')}: {e}", exc_info=True)
                errors.append({
                    "ip_id": ip_order.get("id"),
                    "ip_ref": ip_order.get("reference"),
                    "error": str(e)[:200],
                })
                continue

        conn.commit()

        # Sync the next_reference_number counter so future manual orders
        # don't collide with or fall behind the imported refs
        cursor.execute("SELECT ISNULL(MAX(reference_number), 0) FROM stock_orders")
        new_max = cursor.fetchone()[0] or 0
        if new_max > 0:
            cursor.execute(
                "UPDATE app_settings SET setting_value = ?, updated_at = GETUTCDATE() "
                "WHERE setting_key = 'next_reference_number'",
                str(new_max + 1),
            )
            conn.commit()
            logger.info(f"Ref counter advanced to {new_max + 1}")

        logger.info(f"Imported {imported} stock orders ({total_items} items) from IP. "
                    f"Skipped {skipped}. Used fallback ref for {used_fallback}. Errors: {len(errors)}.")

        return {
            "status": "ok",
            "imported": imported,
            "skipped_empty": skipped,
            "total_items": total_items,
            "total_cost_cad": round(total_cost_cad, 2),
            "ip_fetched": len(ip_orders),
            "used_fallback_ref": used_fallback,
            "errors": errors,
            "next_reference_number": new_max + 1 if new_max else None,
        }
    finally:
        conn.close()
