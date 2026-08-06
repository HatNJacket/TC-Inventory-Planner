"""
TC Inventory Planner - Sales Manager
Manages temporary vendor sale pricing: upload sale pricelist, preview changes,
activate/schedule sales, and revert pricing when sale ends.

Database tables:
  vendor_sales — sale metadata (vendor, name, status, schedule, fx_rate)
  vendor_sale_items — individual SKU pricing records with original/sale prices
"""
import logging
import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ─── SALE LIFECYCLE ──────────────────────────────────────────────

SALE_STATUSES = ['pending', 'active', 'completed', 'cancelled']


def _f(val):
    """Convert Decimal/int/None to float."""
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


def _row_to_dict(cursor, row):
    """Convert a pyodbc row to dict using cursor.description."""
    return {desc[0]: val for desc, val in zip(cursor.description, row)}


def _rows_to_dicts(cursor, utc_to_eastern_cols=None):
    """Convert all fetched rows to list of dicts.
    If utc_to_eastern_cols is provided, those datetime columns are converted
    from stored UTC to Eastern Time for display.
    """
    cols = [desc[0] for desc in cursor.description]
    eastern_set = set(utc_to_eastern_cols or [])
    results = []
    for row in cursor.fetchall():
        d = {}
        for col, val in zip(cols, row):
            if isinstance(val, Decimal):
                d[col] = float(val)
            elif isinstance(val, datetime):
                if col in eastern_set:
                    d[col] = _utc_to_eastern_str(val)
                else:
                    d[col] = val.isoformat()
            else:
                d[col] = val
        results.append(d)
    return results


# ─── CREATE / LIST / GET SALES ───────────────────────────────────

def _parse_dt(val):
    """
    Parse a datetime string from the frontend and convert from Eastern Time
    to UTC for storage. The frontend sends times labeled as EST, so we need
    to interpret them as America/Toronto (handles EST/EDT automatically)
    and convert to UTC before saving to the database.
    
    The Azure Function timer compares against UTC, so storing in UTC ensures
    sales activate/revert at the correct Eastern Time.
    """
    if not val or val.strip() == '':
        return None
    try:
        from zoneinfo import ZoneInfo
        # Handle ISO format from datetime-local input: "2026-04-15T10:00"
        val = val.strip().replace('T', ' ')
        if len(val) == 16:  # "2026-04-15 10:00"
            naive = datetime.strptime(val, '%Y-%m-%d %H:%M')
        elif len(val) == 19:  # "2026-04-15 10:00:00"
            naive = datetime.strptime(val, '%Y-%m-%d %H:%M:%S')
        else:
            # Already has timezone info
            return datetime.fromisoformat(val.replace('Z', '+00:00'))

        # Treat the naive datetime as Eastern Time and convert to UTC
        eastern = ZoneInfo('America/Toronto')
        local_dt = naive.replace(tzinfo=eastern)
        utc_dt = local_dt.astimezone(timezone.utc)
        # Return as naive UTC (Azure SQL stores DATETIME2 without timezone)
        return utc_dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _utc_to_eastern_str(utc_dt):
    """Convert a naive UTC datetime to an Eastern Time ISO string for frontend display."""
    try:
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo('America/Toronto')
        aware_utc = utc_dt.replace(tzinfo=timezone.utc)
        eastern_dt = aware_utc.astimezone(eastern)
        return eastern_dt.strftime('%Y-%m-%dT%H:%M')
    except Exception:
        return utc_dt.isoformat() if utc_dt else None


def create_sale(db, vendor: str, name: str, currency: str,
                fx_rate: float, start_at: str = None, end_at: str = None,
                notes: str = None, collection_name: str = None) -> Dict:
    """Create a new sale record in pending status."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO vendor_sales
                (vendor, name, status, currency, fx_rate_at_creation,
                 start_at, end_at, notes, collection_name, created_at)
            OUTPUT INSERTED.id, INSERTED.created_at
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, GETUTCDATE())
        """, vendor, name, currency, fx_rate,
             _parse_dt(start_at), _parse_dt(end_at), notes or None,
             collection_name or None)
        row = cursor.fetchone()
        conn.commit()
        return {"id": row[0], "created_at": row[1].isoformat()}
    finally:
        conn.close()


def list_sales(db, vendor: str = None, status: str = None) -> List[Dict]:
    """List sales with optional vendor/status filter."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = []
        params = []
        if vendor:
            where.append("vs.vendor = ?")
            params.append(vendor)
        if status:
            where.append("vs.status = ?")
            params.append(status)

        where_sql = " AND ".join(where) if where else "1=1"

        cursor.execute(f"""
            SELECT vs.*,
                (SELECT COUNT(*) FROM vendor_sale_items vsi WHERE vsi.sale_id = vs.id) AS item_count,
                (SELECT COUNT(*) FROM vendor_sale_items vsi WHERE vsi.sale_id = vs.id AND vsi.status = 'active') AS active_items,
                (SELECT AVG(vsi.discount_pct) FROM vendor_sale_items vsi WHERE vsi.sale_id = vs.id) AS avg_discount_pct,
                (SELECT SUM(vsi.original_price - vsi.sale_price_cad) FROM vendor_sale_items vsi WHERE vsi.sale_id = vs.id) AS total_discount_value
            FROM vendor_sales vs
            WHERE {where_sql}
            ORDER BY vs.created_at DESC
        """, *params)
        return _rows_to_dicts(cursor, utc_to_eastern_cols=['start_at', 'end_at'])
    finally:
        conn.close()


def get_sale(db, sale_id: int) -> Optional[Dict]:
    """Get a single sale with its items."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vendor_sales WHERE id = ?", sale_id)
        row = cursor.fetchone()
        if not row:
            return None
        sale = _row_to_dict(cursor, row)
        # Convert datetimes — schedule times (start_at, end_at) are stored
        # as UTC; convert back to Eastern for display
        schedule_fields = {'start_at', 'end_at'}
        for k, v in sale.items():
            if isinstance(v, datetime):
                if k in schedule_fields:
                    sale[k] = _utc_to_eastern_str(v)
                else:
                    sale[k] = v.isoformat()
            elif isinstance(v, Decimal):
                sale[k] = float(v)

        # Get items
        cursor.execute("""
            SELECT * FROM vendor_sale_items
            WHERE sale_id = ?
            ORDER BY sku
        """, sale_id)
        sale['items'] = _rows_to_dicts(cursor)
        return sale
    finally:
        conn.close()


def update_sale(db, sale_id: int, updates: Dict) -> Dict:
    """Update sale metadata (name, schedule, notes, status)."""
    allowed_fields = ['name', 'start_at', 'end_at', 'notes', 'status', 'collection_name']
    date_fields = {'start_at', 'end_at'}
    sets = []
    params = []
    for field in allowed_fields:
        if field in updates:
            sets.append(f"{field} = ?")
            val = updates[field]
            if field in date_fields:
                params.append(_parse_dt(val))
            else:
                params.append(val if val != '' else None)

    if not sets:
        return {"status": "ok", "message": "Nothing to update"}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        params.append(sale_id)
        cursor.execute(f"UPDATE vendor_sales SET {', '.join(sets)} WHERE id = ?", *params)
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


def delete_sale(db, sale_id: int) -> Dict:
    """Delete a sale and its items. Active sales must be reverted first."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM vendor_sales WHERE id = ?", sale_id)
        row = cursor.fetchone()
        if not row:
            return {"status": "error", "message": "Sale not found"}
        if row[0] == 'active':
            return {"status": "error", "message": "Cannot delete an active sale. Revert it first."}

        cursor.execute("DELETE FROM vendor_sale_items WHERE sale_id = ?", sale_id)
        cursor.execute("DELETE FROM vendor_sales WHERE id = ?", sale_id)
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# ─── UPLOAD & PREVIEW ────────────────────────────────────────────

def preview_sale_pricelist(db, sale_id: int, csv_text: str,
                           sku_col: str, price_col: str,
                           price_currency: str, fx_rate: float,
                           dealer_cost_col: str = None,
                           dealer_cost_currency: str = None,
                           dealer_cost_fx_rate: float = None) -> Dict:
    """
    Parse a sale pricelist CSV/TSV, match SKUs to velocity cache,
    compute sale prices in CAD, and return a preview without applying anything.
    Also detects conflicts with other active/pending sales.
    Optional dealer_cost_col: column name for the vendor's dealer/wholesale cost during the sale.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Get the sale record
        cursor.execute("SELECT vendor, currency FROM vendor_sales WHERE id = ?", sale_id)
        sale_row = cursor.fetchone()
        if not sale_row:
            return {"status": "error", "message": "Sale not found"}
        vendor = sale_row[0]

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            return {"status": "error", "message": "Empty CSV"}

        items = []
        for row in reader:
            sku = (row.get(sku_col) or '').strip()
            price_str = (row.get(price_col) or '').strip().replace('$', '').replace(',', '')
            if not sku or not price_str:
                continue
            try:
                price = float(price_str)
            except ValueError:
                continue
            if price <= 0:
                continue

            item = {"sku": sku, "sale_price_foreign": price}

            # Parse dealer cost if column is specified
            if dealer_cost_col and dealer_cost_col in (row.keys() if hasattr(row, 'keys') else []):
                dc_str = (row.get(dealer_cost_col) or '').strip().replace('$', '').replace(',', '')
                if dc_str:
                    try:
                        item["dealer_cost_foreign"] = float(dc_str)
                    except ValueError:
                        pass

            items.append(item)

        if not items:
            return {"status": "error", "message": "No valid SKU/price rows found"}

        # Look up each SKU in the velocity cache
        preview_items = []
        skus = [item["sku"] for item in items]

        # Build a lookup dict from velocity cache
        placeholders = ','.join(['?'] * min(len(skus), 500))
        for batch_start in range(0, len(skus), 500):
            batch = skus[batch_start:batch_start + 500]
            ph = ','.join(['?'] * len(batch))
            cursor.execute(f"""
                SELECT sku, product_title, price, cost, variant_id, product_id, tags
                FROM product_velocity_cache
                WHERE vendor = ? AND UPPER(sku) IN ({','.join(['UPPER(?)'] * len(batch))})
            """, vendor, *batch)
            for r in cursor.fetchall():
                cache_item = _row_to_dict(cursor, r)
                # Convert types
                for k, v in cache_item.items():
                    if isinstance(v, Decimal):
                        cache_item[k] = float(v)
                cache_item['_matched'] = True
                # Store by upper SKU for matching
                preview_items.append(cache_item)

        cache_lookup = {item['sku'].upper(): item for item in preview_items}

        # Vendor-promo overlay: when an item we're putting on a customer sale
        # is ALSO on an active vendor cost-promo, use the vendor's discounted
        # cost for the margin calc below. Without this, the margin shown on
        # the preview understates profit during overlapping windows.
        # build_sale_cost_lookup returns {SKU_UPPER: { sale_cost_cad, ... }}
        # filtered to currently-active rows.
        try:
            vendor_promo_lookup = db.build_sale_cost_lookup() or {}
        except Exception as _e:
            # Defensive: if the lookup fails for any reason, fall back to no
            # overlay — preview is still accurate against regular costs.
            vendor_promo_lookup = {}

        # Check for conflicts with other active/pending sales
        cursor.execute("""
            SELECT vsi.sku, vs.name, vs.id
            FROM vendor_sale_items vsi
            JOIN vendor_sales vs ON vs.id = vsi.sale_id
            WHERE vs.vendor = ? AND vs.status IN ('pending', 'active')
                AND vs.id != ?
        """, vendor, sale_id)
        conflicts = {}
        for r in cursor.fetchall():
            conflicts[r[0].upper()] = {"sale_name": r[1], "sale_id": r[2]}

        # Build preview
        result_items = []
        matched = 0
        unmatched = []
        warnings = []

        for item in items:
            sku_upper = item["sku"].upper()
            cached = cache_lookup.get(sku_upper)

            if not cached:
                unmatched.append(item["sku"])
                continue

            matched += 1
            sale_price_cad = round(item["sale_price_foreign"] * fx_rate) if price_currency != 'CAD' else round(item["sale_price_foreign"])
            original_price = cached.get('price', 0)
            cost = cached.get('cost', 0)
            discount_pct = round((1 - sale_price_cad / original_price) * 100, 1) if original_price > 0 else 0

            # Dealer cost: use from pricelist if available, otherwise fall back to velocity cache cost
            dealer_cost_foreign = item.get("dealer_cost_foreign")
            dealer_cost_cad = None
            effective_cost = cost  # for margin calculation
            if dealer_cost_foreign is not None:
                dc_currency = dealer_cost_currency or price_currency
                dc_fx = dealer_cost_fx_rate or fx_rate
                dealer_cost_cad = round(dealer_cost_foreign * dc_fx, 2) if dc_currency != 'CAD' else round(dealer_cost_foreign, 2)
                effective_cost = dealer_cost_cad  # use dealer cost for margin calc

            # Vendor-promo override: a vendor's active cost discount beats both
            # the velocity-cache cost and any per-row dealer cost, since it's
            # the actual price we'd pay if we replenished today. Stash the
            # pre-override cost on the entry so the UI can show "margin uses
            # vendor sale price" if we ever surface it.
            vendor_promo_cost_cad = None
            vendor_promo_ends_at = None
            promo = vendor_promo_lookup.get(sku_upper)
            if promo and promo.get('sale_cost_cad'):
                vendor_promo_cost_cad = float(promo['sale_cost_cad'])
                vendor_promo_ends_at = promo.get('ends_at')
                effective_cost = vendor_promo_cost_cad

            sale_margin_pct = round((sale_price_cad - effective_cost) / sale_price_cad * 100, 1) if sale_price_cad > 0 and effective_cost else None

            entry = {
                "sku": cached['sku'],
                "title": cached.get('product_title', ''),
                "original_price": original_price,
                "sale_price_foreign": item["sale_price_foreign"],
                "sale_price_cad": sale_price_cad,
                "discount_pct": discount_pct,
                "cost": cost,
                "dealer_cost_foreign": dealer_cost_foreign,
                "dealer_cost_cad": dealer_cost_cad,
                "dealer_cost_currency": dealer_cost_currency or price_currency if dealer_cost_foreign else None,
                "vendor_promo_cost_cad": vendor_promo_cost_cad,
                "vendor_promo_active": vendor_promo_cost_cad is not None,
                "vendor_promo_ends_at": vendor_promo_ends_at,
                "sale_margin_pct": sale_margin_pct,
                "variant_id": cached.get('variant_id'),
                "product_id": cached.get('product_id'),
                "tags": cached.get('tags', ''),
            }

            # Warnings
            if sale_price_cad >= original_price:
                entry["warning"] = "Sale price not lower than current price"
                warnings.append(f"{cached['sku']}: sale ${sale_price_cad} >= current ${original_price}")
            if sale_margin_pct is not None and sale_margin_pct < 10:
                entry["warning"] = entry.get("warning", "") + " Low margin!"
                warnings.append(f"{cached['sku']}: margin {sale_margin_pct}% below 10%")
            if effective_cost and sale_price_cad < effective_cost:
                entry["warning"] = "BELOW COST"
                warnings.append(f"{cached['sku']}: sale ${sale_price_cad} below cost ${effective_cost}")
            if sku_upper in conflicts:
                entry["conflict"] = conflicts[sku_upper]
                warnings.append(f"{cached['sku']}: already in sale '{conflicts[sku_upper]['sale_name']}'")

            result_items.append(entry)

        return {
            "status": "ok",
            "matched": matched,
            "unmatched": unmatched,
            "unmatched_count": len(unmatched),
            "warnings": warnings,
            "items": result_items,
            "currency": price_currency,
            "fx_rate": fx_rate,
            "avg_discount_pct": round(sum(i['discount_pct'] for i in result_items) / max(len(result_items), 1), 1),
        }
    finally:
        conn.close()


async def confirm_sale_items(db, shopify_client, sale_id: int, items: List[Dict]) -> Dict:
    """
    Save the previewed sale items to the database.
    Does NOT activate the sale — just stores the items for later activation.
    If a collection_name is set on the sale, creates the Shopify collection
    now so the admin can set up the storefront before the sale goes live.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Verify sale exists and is pending
        cursor.execute("""
            SELECT status, collection_name, collection_id
            FROM vendor_sales WHERE id = ?
        """, sale_id)
        row = cursor.fetchone()
        if not row:
            return {"status": "error", "message": "Sale not found"}
        if row[0] not in ('pending', 'cancelled'):
            return {"status": "error", "message": f"Cannot add items to {row[0]} sale"}

        collection_name = row[1]
        existing_collection_id = row[2]

        # Clear existing items
        cursor.execute("DELETE FROM vendor_sale_items WHERE sale_id = ?", sale_id)

        # Insert new items
        inserted = 0
        product_ids = set()
        for item in items:
            cursor.execute("""
                INSERT INTO vendor_sale_items
                    (sale_id, sku, shopify_variant_id, shopify_product_id,
                     original_price, original_compare_at, sale_price_foreign,
                     sale_price_cad, discount_pct, cost_cad, sale_margin_pct,
                     dealer_cost_foreign, dealer_cost_cad, dealer_cost_currency,
                     status)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
                sale_id, item['sku'], item.get('variant_id'),
                item.get('product_id'), item.get('original_price', 0),
                item.get('sale_price_foreign', 0), item.get('sale_price_cad', 0),
                item.get('discount_pct', 0), item.get('cost', 0),
                item.get('sale_margin_pct'),
                item.get('dealer_cost_foreign'),
                item.get('dealer_cost_cad'),
                item.get('dealer_cost_currency'))
            inserted += 1
            if item.get('product_id'):
                product_ids.add(item['product_id'])

        # Update sale status back to pending if it was cancelled
        cursor.execute("""
            UPDATE vendor_sales SET status = 'pending' WHERE id = ? AND status = 'cancelled'
        """, sale_id)

        # Create Shopify collection early so admin can set up storefront
        collection_id = existing_collection_id
        if collection_name and product_ids and not existing_collection_id:
            try:
                collection_id = await shopify_client.create_manual_collection(
                    collection_name
                )
                logger.info(f"Created collection '{collection_name}': {collection_id}")

                await shopify_client.add_products_to_collection(
                    collection_id, list(product_ids)
                )
                logger.info(f"Added {len(product_ids)} products to collection {collection_id}")

                cursor.execute("""
                    UPDATE vendor_sales SET collection_id = ? WHERE id = ?
                """, collection_id, sale_id)
            except Exception as e:
                logger.error(f"Collection creation failed during confirm: {e}")
                # Don't fail the confirm for a collection error

        conn.commit()
        return {"status": "ok", "items_saved": inserted, "collection_id": collection_id}
    finally:
        conn.close()


# ─── ACTIVATE / REVERT SALE ──────────────────────────────────────

async def activate_sale(db, shopify_client, sale_id: int) -> Dict:
    """
    Activate a sale: for each item, move current price → compareAt,
    set sale price, add 'On Sale' tag. Optionally create a Shopify
    collection and add all sale products to it.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Get sale
        cursor.execute("""
            SELECT status, vendor, collection_name, collection_id
            FROM vendor_sales WHERE id = ?
        """, sale_id)
        sale_row = cursor.fetchone()
        if not sale_row:
            return {"status": "error", "message": "Sale not found"}
        if sale_row[0] == 'active':
            return {"status": "error", "message": "Sale is already active"}
        if sale_row[0] not in ('pending',):
            return {"status": "error", "message": f"Cannot activate a {sale_row[0]} sale. Resubmit it to pending first."}

        collection_name = sale_row[2]
        existing_collection_id = sale_row[3]

        # Get pending items
        cursor.execute("""
            SELECT id, sku, shopify_variant_id, shopify_product_id,
                   original_price, sale_price_cad
            FROM vendor_sale_items
            WHERE sale_id = ? AND status = 'pending'
        """, sale_id)
        items = [_row_to_dict(cursor, r) for r in cursor.fetchall()]

        if not items:
            return {"status": "error", "message": "No pending items to activate"}

        results = []
        activated = 0
        errors = 0
        activated_product_ids = set()

        for item in items:
            try:
                variant_id = item['shopify_variant_id']
                product_id = item['shopify_product_id']
                original_price = float(item['original_price']) if item['original_price'] else 0
                sale_price = float(item['sale_price_cad']) if item['sale_price_cad'] else 0

                # 1. Fetch current compareAtPrice (in case it's already set)
                current_compare_at = await _get_current_compare_at(
                    shopify_client, variant_id
                )

                # 2. Update variant: set price=sale_price, compareAtPrice=original_price
                await shopify_client.update_variant_price_with_compare(
                    product_id, variant_id, sale_price, original_price
                )

                # 3. Add "On Sale" tag
                await shopify_client.add_tags_to_product(product_id, ["On Sale"])

                # 4. Update the sale item record
                cursor.execute("""
                    UPDATE vendor_sale_items
                    SET status = 'active', original_compare_at = ?
                    WHERE id = ?
                """, current_compare_at, item['id'])

                # 5. Update velocity cache
                cursor.execute("""
                    UPDATE product_velocity_cache SET price = ? WHERE sku = ?
                """, sale_price, item['sku'])

                results.append({"sku": item['sku'], "status": "ok"})
                activated += 1
                if product_id:
                    activated_product_ids.add(product_id)

            except Exception as e:
                logger.error(f"Failed to activate sale item {item['sku']}: {e}")
                results.append({"sku": item['sku'], "status": "error", "message": str(e)})
                errors += 1

        # Update collection if it exists — add any products not already in it
        collection_id = existing_collection_id
        if collection_name and activated_product_ids:
            try:
                if not collection_id:
                    collection_id = await shopify_client.create_manual_collection(
                        collection_name
                    )
                    logger.info(f"Created collection '{collection_name}': {collection_id}")
                    cursor.execute("""
                        UPDATE vendor_sales SET collection_id = ? WHERE id = ?
                    """, collection_id, sale_id)

                # Add products to the collection (idempotent — Shopify ignores duplicates)
                product_list = list(activated_product_ids)
                await shopify_client.add_products_to_collection(
                    collection_id, product_list
                )
                logger.info(f"Added {len(product_list)} products to collection {collection_id}")
            except Exception as e:
                logger.error(f"Collection update failed: {e}")
                # Don't fail the whole activation for a collection error

        # Update sale status
        cursor.execute("""
            UPDATE vendor_sales
            SET status = 'active', activated_at = GETUTCDATE()
            WHERE id = ?
        """, sale_id)
        conn.commit()

        return {
            "status": "ok",
            "activated": activated,
            "errors": errors,
            "collection_id": collection_id,
            "results": results,
        }
    finally:
        conn.close()


async def revert_sale(db, shopify_client, sale_id: int) -> Dict:
    """
    Revert a sale: restore original prices, clear compareAt, remove 'On Sale' tag.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Get sale
        cursor.execute("SELECT status FROM vendor_sales WHERE id = ?", sale_id)
        sale_row = cursor.fetchone()
        if not sale_row:
            return {"status": "error", "message": "Sale not found"}
        if sale_row[0] not in ('active', 'pending'):
            return {"status": "error", "message": f"Cannot revert {sale_row[0]} sale"}

        # Get active items
        cursor.execute("""
            SELECT id, sku, shopify_variant_id, shopify_product_id,
                   original_price, original_compare_at
            FROM vendor_sale_items
            WHERE sale_id = ? AND status = 'active'
        """, sale_id)
        items = [_row_to_dict(cursor, r) for r in cursor.fetchall()]

        results = []
        reverted = 0
        errors = 0

        for item in items:
            try:
                variant_id = item['shopify_variant_id']
                product_id = item['shopify_product_id']
                original_price = float(item['original_price']) if item['original_price'] else 0
                original_compare_at = float(item['original_compare_at']) if item['original_compare_at'] else None

                # 1. Restore price and compareAt
                await shopify_client.update_variant_price_with_compare(
                    product_id, variant_id, original_price, original_compare_at
                )

                # 2. Remove "On Sale" tag
                await shopify_client.remove_tags_from_product(product_id, ["On Sale"])

                # 3. Update sale item status
                cursor.execute("""
                    UPDATE vendor_sale_items SET status = 'reverted' WHERE id = ?
                """, item['id'])

                # 4. Restore velocity cache price
                cursor.execute("""
                    UPDATE product_velocity_cache SET price = ? WHERE sku = ?
                """, original_price, item['sku'])

                results.append({"sku": item['sku'], "status": "ok"})
                reverted += 1

            except Exception as e:
                logger.error(f"Failed to revert sale item {item['sku']}: {e}")
                results.append({"sku": item['sku'], "status": "error", "message": str(e)})
                errors += 1

        # Update sale status
        new_status = 'completed' if sale_row[0] == 'active' else 'cancelled'
        cursor.execute("""
            UPDATE vendor_sales
            SET status = ?, reverted_at = GETUTCDATE()
            WHERE id = ?
        """, new_status, sale_id)
        conn.commit()

        return {
            "status": "ok",
            "reverted": reverted,
            "errors": errors,
            "new_status": new_status,
            "results": results,
        }
    finally:
        conn.close()


def resubmit_sale(db, sale_id: int) -> Dict:
    """
    Move a completed or cancelled sale back to pending status,
    resetting all item statuses to pending so it can be re-activated.
    Re-reads current Shopify prices to update original_price in case
    they've changed since the sale was last active.
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT status, vendor FROM vendor_sales WHERE id = ?", sale_id)
        row = cursor.fetchone()
        if not row:
            return {"status": "error", "message": "Sale not found"}
        if row[0] not in ('completed', 'cancelled'):
            return {"status": "error", "message": f"Only completed or cancelled sales can be resubmitted (current: {row[0]})"}

        vendor = row[1]

        # Update all item statuses back to pending and refresh original_price
        # from the current velocity cache (prices may have changed)
        cursor.execute("""
            UPDATE vsi
            SET vsi.status = 'pending',
                vsi.original_price = COALESCE(pvc.price, vsi.original_price),
                vsi.original_compare_at = NULL
            FROM vendor_sale_items vsi
            LEFT JOIN product_velocity_cache pvc
                ON pvc.sku = vsi.sku AND pvc.vendor = ?
            WHERE vsi.sale_id = ?
        """, vendor, sale_id)

        # Recalculate discount_pct based on refreshed original prices
        cursor.execute("""
            UPDATE vendor_sale_items
            SET discount_pct = CASE
                WHEN original_price > 0
                THEN ROUND((1.0 - sale_price_cad / original_price) * 100, 1)
                ELSE 0 END
            WHERE sale_id = ?
        """, sale_id)

        # Set sale back to pending
        cursor.execute("""
            UPDATE vendor_sales
            SET status = 'pending', activated_at = NULL, reverted_at = NULL
            WHERE id = ?
        """, sale_id)

        conn.commit()

        # Get updated item count
        cursor.execute("SELECT COUNT(*) FROM vendor_sale_items WHERE sale_id = ?", sale_id)
        item_count = cursor.fetchone()[0]

        return {"status": "ok", "sale_id": sale_id, "items_reset": item_count}
    finally:
        conn.close()


async def exclude_item_from_sale(db, shopify_client, sale_id: int, sku: str) -> Dict:
    """Remove a single SKU from an active sale (partial revert)."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, shopify_variant_id, shopify_product_id,
                   original_price, original_compare_at, status
            FROM vendor_sale_items
            WHERE sale_id = ? AND sku = ?
        """, sale_id, sku)
        row = cursor.fetchone()
        if not row:
            return {"status": "error", "message": f"SKU {sku} not found in sale"}

        item = _row_to_dict(cursor, row)
        if item['status'] != 'active':
            return {"status": "error", "message": f"Item is {item['status']}, not active"}

        # Revert this single item
        original_price = float(item['original_price']) if item['original_price'] else 0
        original_compare_at = float(item['original_compare_at']) if item['original_compare_at'] else None

        await shopify_client.update_variant_price_with_compare(
            item['shopify_product_id'], item['shopify_variant_id'],
            original_price, original_compare_at
        )

        # Check if this product has OTHER active sale items before removing tag
        cursor.execute("""
            SELECT COUNT(*) FROM vendor_sale_items
            WHERE sale_id = ? AND shopify_product_id = ? AND sku != ? AND status = 'active'
        """, sale_id, item['shopify_product_id'], sku)
        other_active = cursor.fetchone()[0]
        if other_active == 0:
            await shopify_client.remove_tags_from_product(
                item['shopify_product_id'], ["On Sale"]
            )

        cursor.execute("""
            UPDATE vendor_sale_items SET status = 'excluded' WHERE id = ?
        """, item['id'])

        cursor.execute("""
            UPDATE product_velocity_cache SET price = ? WHERE sku = ?
        """, original_price, sku)

        conn.commit()
        return {"status": "ok", "sku": sku}
    finally:
        conn.close()


# ─── SCHEDULING ──────────────────────────────────────────────────

def check_scheduled_sales(db) -> List[Dict]:
    """
    Check for sales that should be activated or reverted based on schedule.
    Returns a list of actions to take. Called periodically by a timer.
    """
    conn = db._get_connection()
    now = datetime.now(timezone.utc)
    try:
        cursor = conn.cursor()
        actions = []

        # Sales to activate: pending, start_at <= now
        cursor.execute("""
            SELECT id, vendor, name FROM vendor_sales
            WHERE status = 'pending' AND start_at IS NOT NULL AND start_at <= ?
        """, now)
        for row in cursor.fetchall():
            actions.append({
                "action": "activate",
                "sale_id": row[0],
                "vendor": row[1],
                "name": row[2],
            })

        # Sales to revert: active, end_at <= now
        cursor.execute("""
            SELECT id, vendor, name FROM vendor_sales
            WHERE status = 'active' AND end_at IS NOT NULL AND end_at <= ?
        """, now)
        for row in cursor.fetchall():
            actions.append({
                "action": "revert",
                "sale_id": row[0],
                "vendor": row[1],
                "name": row[2],
            })

        return actions
    finally:
        conn.close()


# ─── HELPER ──────────────────────────────────────────────────────

async def _get_current_compare_at(shopify_client, variant_id: str) -> float:
    """Fetch the current compareAtPrice for a variant."""
    query = """
    query($id: ID!) {
        productVariant(id: $id) {
            compareAtPrice
        }
    }
    """
    try:
        data = await shopify_client._query(query, {"id": variant_id})
        cap = data.get("productVariant", {}).get("compareAtPrice")
        return float(cap) if cap else None
    except Exception:
        return None
