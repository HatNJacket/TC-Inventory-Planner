"""
TC Inventory Planner - Import Stock Orders from Inventory Planner by Sage

Pulls all existing purchase orders (with line items) from the
Inventory Planner API and imports them into the TC Planner Azure SQL database.

Usage:
    python import_from_ip.py

Required env vars (in .env):
    IP_API_KEY          - Inventory Planner API key (from Account > Settings > API)
    IP_ACCOUNT_ID       - Inventory Planner account ID (e.g. a10720)
    AZURE_SQL_SERVER    - Azure SQL server
    AZURE_SQL_DATABASE  - Azure SQL database
    AZURE_SQL_USER      - Azure SQL username
    AZURE_SQL_PASSWORD  - Azure SQL password
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pyodbc
from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).parent / ".env")

# ─── Configuration ───────────────────────────────────────────────

IP_API_KEY = os.getenv("IP_API_KEY", "")
IP_ACCOUNT_ID = os.getenv("IP_ACCOUNT_ID", "a10720")
IP_BASE_URL = "https://app.inventory-planner.com/api/v1"

AZURE_SQL_SERVER = os.getenv("AZURE_SQL_SERVER", "")
AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE", "")
AZURE_SQL_USER = os.getenv("AZURE_SQL_USER", "")
AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD", "")

# ─── Inventory Planner API Client ────────────────────────────────

def ip_headers():
    return {
        "Authorization": IP_API_KEY,
        "Account": IP_ACCOUNT_ID,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_purchase_orders(client, status_filter=None, limit=200):
    """Fetch all purchase orders from Inventory Planner."""
    all_orders = []
    page = 0

    while True:
        params = {
            "limit": limit,
            "page": page,
            "type": "po",  # Only purchase orders, not transfers/assembly
        }
        if status_filter:
            params["status"] = status_filter

        print(f"  Fetching page {page}...")
        resp = client.get(
            f"{IP_BASE_URL}/purchase-orders",
            headers=ip_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        orders = data.get("purchase-orders", [])
        meta = data.get("meta", {})

        all_orders.extend(orders)

        total = meta.get("total", 0)
        count = meta.get("count", 0)
        print(f"    Got {count} orders (total: {total})")

        if len(all_orders) >= total or count == 0:
            break

        page += 1
        time.sleep(0.5)  # Be polite to their API

    return all_orders


def fetch_order_items(client, order_id, limit=500):
    """Fetch all line items for a purchase order."""
    params = {"limit": limit}
    resp = client.get(
        f"{IP_BASE_URL}/purchase-orders/{order_id}/items",
        headers=ip_headers(),
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


# ─── Status Mapping ──────────────────────────────────────────────

def map_ip_status(ip_status):
    """Map Inventory Planner status to TC Planner status."""
    status_map = {
        "OPEN": "open",
        "DRAFT": "open",
        "ACTIVE": "ordered",
        "ORDERED": "ordered",
        "ORDERED_WAITING_SHIP": "ordered",
        "ORDERED_WAITING_INVOICE": "ordered_invoice",
        "PAID": "paid",
        "PAID_WAITING_SHIP": "paid",
        "SHIPPED": "shipped",
        "PARTIALLY_SHIPPED": "partial_shipped",
        "PARTIALLY_RECEIVED": "partial_received",
        "RECEIVED": "closed",
        "CLOSED": "closed",
        "CANCELLED": "closed",
    }
    # Normalize: Inventory Planner uses various formats
    normalized = ip_status.upper().replace(" ", "_").replace(",", "")
    return status_map.get(normalized, "open")


def parse_ip_date(date_str):
    """Parse Inventory Planner date string to SQL-compatible format."""
    if not date_str:
        return None
    try:
        # Try ISO format first
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    try:
        # Try other common formats
        for fmt in ["%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y"]:
            try:
                return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    except Exception:
        pass
    return None


# ─── Database Import ─────────────────────────────────────────────

def get_db_connection():
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={AZURE_SQL_SERVER};"
        f"DATABASE={AZURE_SQL_DATABASE};"
        f"UID={AZURE_SQL_USER};"
        f"PWD={AZURE_SQL_PASSWORD};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str)


def import_order_to_db(conn, order, items):
    """Import a single purchase order with its items into Azure SQL."""
    cursor = conn.cursor()

    # Parse fields
    reference = order.get("reference", "")
    try:
        ref_num = int(reference) if reference.isdigit() else 0
    except (ValueError, AttributeError):
        ref_num = 0

    status = map_ip_status(order.get("status", "OPEN"))
    vendor = order.get("vendor", order.get("source", "Unknown"))
    currency = order.get("currency", "CAD")
    expected_date = parse_ip_date(order.get("expected_date"))
    created_date = parse_ip_date(order.get("created_date"))
    notes = order.get("notes", "")
    total_cost = float(order.get("total", 0) or 0)
    ip_id = order.get("id", "")

    # Check if this order already exists (by reference number)
    if ref_num > 0:
        cursor.execute(
            "SELECT id FROM stock_orders WHERE reference_number = ?", ref_num
        )
        existing = cursor.fetchone()
        if existing:
            print(f"    Skipping #{ref_num} (already exists)")
            return False

    # Insert stock order
    cursor.execute(
        """INSERT INTO stock_orders
           (reference_number, status, vendor, currency, expected_date,
            notes, total_cost, created_at, updated_at)
           OUTPUT INSERTED.id
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, GETUTCDATE())""",
        ref_num,
        status,
        vendor,
        currency,
        expected_date,
        f"Imported from IP (id: {ip_id}). {notes or ''}".strip(),
        total_cost,
        created_date or datetime.utcnow().strftime("%Y-%m-%d"),
    )
    order_id = cursor.fetchone()[0]

    # Insert line items
    item_count = 0
    for item in items:
        sku = item.get("sku", "")
        title = item.get("title", "")
        barcode = item.get("barcode", "")
        vendor_ref = item.get("vendor_reference", "")
        ordered_qty = int(item.get("replenishment", 0) or 0)
        received_qty = int(item.get("received", 0) or 0)
        cost_price = float(item.get("cost price", 0) or item.get("cost_price", 0) or 0)

        if ordered_qty == 0 and received_qty == 0:
            continue

        cursor.execute(
            """INSERT INTO stock_order_items
               (stock_order_id, product_title, variant_title, sku,
                barcode, vendor, ordered_qty, received_qty,
                unit_cost, unit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            order_id,
            title,
            "",  # variant_title not in IP API
            sku,
            barcode,
            vendor,
            ordered_qty,
            received_qty,
            cost_price,
            0,  # unit_price - we'll get this from Shopify later
        )
        item_count += 1

    conn.commit()
    return True


# ─── Main ────────────────────────────────────────────────────────

def main():
    # Validate config
    if not IP_API_KEY:
        print("ERROR: IP_API_KEY not set in .env")
        print("Get your API key from Inventory Planner: Account > Settings > API")
        sys.exit(1)

    if not AZURE_SQL_SERVER:
        print("ERROR: Azure SQL credentials not set in .env")
        sys.exit(1)

    print("=" * 60)
    print("  TC Inventory Planner - Import from Inventory Planner")
    print("=" * 60)
    print(f"  Account ID: {IP_ACCOUNT_ID}")
    print(f"  API Base:   {IP_BASE_URL}")
    print(f"  Database:   {AZURE_SQL_DATABASE} on {AZURE_SQL_SERVER}")
    print()

    # Test API connection
    print("Testing Inventory Planner API connection...")
    client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            f"{IP_BASE_URL}/purchase-orders",
            headers=ip_headers(),
            params={"limit": 1},
        )
        resp.raise_for_status()
        meta = resp.json().get("meta", {})
        total_orders = meta.get("total", 0)
        print(f"  Connected! Found {total_orders} total purchase orders.\n")
    except Exception as e:
        print(f"  ERROR: Could not connect to Inventory Planner API: {e}")
        sys.exit(1)

    # Test DB connection
    print("Testing Azure SQL connection...")
    try:
        conn = get_db_connection()
        print("  Connected!\n")
    except Exception as e:
        print(f"  ERROR: Could not connect to Azure SQL: {e}")
        sys.exit(1)

    # Fetch all purchase orders
    print("Fetching all purchase orders from Inventory Planner...")
    orders = fetch_purchase_orders(client)
    print(f"  Total orders to import: {len(orders)}\n")

    # Import each order with its items
    imported = 0
    skipped = 0
    errors = 0

    for i, order in enumerate(orders):
        ref = order.get("reference", "?")
        vendor = order.get("vendor", order.get("source", "?"))
        status = order.get("status", "?")
        order_id = order.get("id", "")

        print(f"[{i+1}/{len(orders)}] Order #{ref} ({vendor}) - {status}")

        try:
            # Fetch items for this order
            # Items may already be embedded in the order response
            items = order.get("items", [])
            if not items and order_id:
                items = fetch_order_items(client, order_id)
                time.sleep(0.3)

            print(f"    {len(items)} line items")

            success = import_order_to_db(conn, order, items)
            if success:
                imported += 1
                print(f"    ✓ Imported")
            else:
                skipped += 1

        except Exception as e:
            errors += 1
            print(f"    ✗ Error: {e}")

    # Update the reference number counter
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(reference_number) FROM stock_orders"
        )
        max_ref = cursor.fetchone()[0] or 999
        cursor.execute(
            "UPDATE app_settings SET setting_value = ?, updated_at = GETUTCDATE() "
            "WHERE setting_key = 'next_reference_number'",
            str(max_ref + 1),
        )
        conn.commit()
        print(f"\nNext reference number set to: {max_ref + 1}")
    except Exception as e:
        print(f"\nWarning: Could not update reference counter: {e}")

    conn.close()
    client.close()

    print()
    print("=" * 60)
    print(f"  Import complete!")
    print(f"  Imported: {imported}")
    print(f"  Skipped:  {skipped} (already existed)")
    print(f"  Errors:   {errors}")
    print("=" * 60)


if __name__ == "__main__":
    main()
