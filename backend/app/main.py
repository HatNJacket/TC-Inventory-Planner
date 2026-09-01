"""
TC Inventory Planner - FastAPI Application
API endpoints for replenishment, stock orders, and data refresh.
"""
import asyncio
import csv
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import config
from .database import db
from .forecasting import forecast_engine
from .shopify_client import shopify_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── AUTH ────────────────────────────────────────────────────────

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Accept either the shared AUTH_TOKEN or any per-user token."""
    tok = credentials.credentials
    if tok != config.AUTH_TOKEN and tok not in config.user_tokens:
        raise HTTPException(status_code=401, detail="Invalid token")
    return tok


def current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """Resolve the caller's display name from their token.

    Per-user tokens (TC_PLANNER_USER_TOKENS) map to a person's name; the
    legacy shared token resolves to "Unknown" so nothing breaks for clients
    that haven't switched over. Used to attribute stock receipts.
    """
    tok = credentials.credentials
    if tok in config.user_tokens:
        return config.user_tokens[tok]
    if tok == config.AUTH_TOKEN:
        return "Unknown"
    raise HTTPException(status_code=401, detail="Invalid token")


# ─── APP LIFECYCLE ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("TC Inventory Planner starting up...")
    try:
        db.initialize_schema()
        logger.info("Database schema ready")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        logger.info("App will start without database - set connection env vars")

    yield

    # Shutdown
    await shopify_client.close()
    logger.info("TC Inventory Planner shutting down")


app = FastAPI(
    title="TC Inventory Planner",
    description="Telescopes Canada Inventory Planning & Replenishment",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── PYDANTIC MODELS ────────────────────────────────────────────

class StockOrderItemCreate(BaseModel):
    product_title: str
    variant_title: str = ""
    sku: str
    barcode: str = ""
    ordered_qty: int
    unit_cost: float = 0
    unit_price: float = 0


class StockOrderCreate(BaseModel):
    vendor: str
    expected_date: Optional[str] = None
    notes: Optional[str] = None
    comments: Optional[str] = None  # User-facing notes/comments shown on PO detail
    items: List[StockOrderItemCreate]


class ReceiveItem(BaseModel):
    item_id: int
    received_qty: int


class ReceiveRequest(BaseModel):
    items: List[ReceiveItem]


class StatusUpdate(BaseModel):
    status: str


# ─── HEALTH & INFO ───────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "tc-inventory-planner"}


@app.get("/api/auth/whoami")
async def whoami(user: str = Depends(current_user)):
    """Return the display name for the caller's token.

    "Unknown" means the shared token is in use (no per-user attribution).
    The UI shows this so staff can confirm they're signed in as themselves
    before receiving stock.
    """
    return {"user": user, "identified": user != "Unknown"}


@app.get("/api/config/vendors")
async def get_vendors(token: str = Depends(verify_token)):
    """Get list of vendors with their lead times."""
    try:
        cached_vendors = db.get_distinct_vendors()
        vendor_info = []
        for v in cached_vendors:
            vendor_info.append({
                "name": v,
                "lead_time_days": config.get_lead_time(v),
            })
        return {"vendors": vendor_info}
    except Exception:
        # Fallback to config if DB not available
        return {
            "vendors": [
                {"name": v, "lead_time_days": lt}
                for v, lt in config.VENDOR_LEAD_TIMES.items()
            ]
        }


@app.get("/api/config/seasonal-multipliers")
async def get_seasonal_multipliers(token: str = Depends(verify_token)):
    """Get seasonal multipliers by month."""
    return {
        "multipliers": config.SEASONAL_MULTIPLIERS,
        "month_names": {
            1: "January", 2: "February", 3: "March", 4: "April",
            5: "May", 6: "June", 7: "July", 8: "August",
            9: "September", 10: "October", 11: "November", 12: "December",
        },
    }


# ─── DATA REFRESH ────────────────────────────────────────────────

# ─── REFRESH STATUS TRACKING ─────────────────────────────────────
refresh_status = {"step": "idle", "detail": ""}


@app.get("/api/refresh/status")
async def get_refresh_status(token: str = Depends(verify_token)):
    return refresh_status


@app.post("/api/refresh")
async def refresh_data(token: str = Depends(verify_token)):
    """
    Full data refresh: fetch products and orders from Shopify,
    calculate velocities, and cache results.
    This is the heavy operation — typically run on a schedule (e.g., nightly)
    or triggered manually.
    """
    try:
        logger.info("Starting full data refresh...")

        # 1. Fetch all products with inventory from Shopify
        refresh_status["step"] = "products"
        refresh_status["detail"] = "Fetching products from Shopify..."
        products = await shopify_client.fetch_all_products()
        logger.info(f"Fetched {len(products)} product variants")
        refresh_status["detail"] = f"Fetched {len(products)} products"

        # 2. Fetch order history for trailing 12 months
        refresh_status["step"] = "orders"
        refresh_status["detail"] = "Fetching order history..."
        line_items = await shopify_client.fetch_all_order_line_items(
            months=config.SALES_HISTORY_MONTHS
        )
        logger.info(f"Fetched {len(line_items)} order line items")
        refresh_status["detail"] = f"Fetched {len(line_items)} orders"

        # 3. Get on-order quantities from our stock orders
        refresh_status["step"] = "on_order"
        refresh_status["detail"] = "Loading stock order data..."
        on_order = db.get_on_order_by_sku()
        logger.info(f"Found {len(on_order)} SKUs with on-order quantities")

        # 4. Generate replenishment recommendations
        refresh_status["step"] = "forecasting"
        refresh_status["detail"] = "Computing forecasts..."
        projection_multiplier = db.get_float_setting('projection_multiplier', 1.0)
        if projection_multiplier != 1.0:
            logger.info(f"Using projection multiplier {projection_multiplier}× "
                        f"(extends demand window beyond lead+safety)")

        # Pull waiter counts from the latest snapshot. Returns {} when no
        # snapshots have been imported, in which case the floor is a no-op.
        from .waiters import waiter_counts_map
        waiter_map = waiter_counts_map(db)
        waiter_rate = db.get_float_setting('waiter_conversion_rate', 0.6)
        if waiter_map and waiter_rate > 0:
            logger.info(f"Applying waiter floor: {len(waiter_map)} SKU(s) with "
                        f"waiters, conversion rate {waiter_rate}")

        # Order cycle (days each PO should last after arriving). User-tunable
        # on Settings page; defaults to 30. Combined with projection_multiplier:
        # demand_days = (cycle_days × projection_multiplier) + lead_time.
        cycle_days = int(round(db.get_float_setting('replenishment_cycle_days', 30)))
        if cycle_days < 1:
            cycle_days = 30
        logger.info(f"Demand window: ({cycle_days}d cycle × {projection_multiplier}) + lead_time")

        # Active vendor sales: lets the forecast use the discounted reorder
        # cost when computing margin / forecast_profit for SKUs currently on
        # promo. Built once per refresh.
        sale_cost_lookup = db.build_sale_cost_lookup()
        if sale_cost_lookup:
            logger.info(f"Vendor sales active for {len(sale_cost_lookup)} SKU(s)")

        recommendations = forecast_engine.generate_replenishment_report(
            products=products,
            order_line_items=line_items,
            on_order_by_sku=on_order,
            projection_multiplier=projection_multiplier,
            cycle_days=cycle_days,
            waiter_count_by_sku=waiter_map,
            waiter_conversion_rate=waiter_rate,
            sale_cost_lookup=sale_cost_lookup,
        )
        logger.info(f"Generated {len(recommendations)} recommendations")

        # 5. Cache results
        refresh_status["step"] = "caching"
        refresh_status["detail"] = "Saving to database..."
        db.cache_velocity_data(recommendations)
        logger.info("Cached velocity data")

        # 6. Get overview stats
        refresh_status["step"] = "complete"
        refresh_status["detail"] = f"Done — {len(recommendations)} products"
        stats = forecast_engine.get_overview_stats(recommendations)

        return {
            "status": "ok",
            "products_fetched": len(products),
            "orders_fetched": len(line_items),
            "recommendations": len(recommendations),
            "needs_replenishment": sum(
                1 for r in recommendations if r.replenish_qty > 0
            ),
            "stats": stats,
        }

    except Exception as e:
        logger.error(f"Data refresh failed: {e}", exc_info=True)
        refresh_status["step"] = "error"
        refresh_status["detail"] = str(e)[:100]
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Reset to idle after a brief delay so the frontend can read "complete"
        async def _reset():
            import asyncio
            await asyncio.sleep(5)
            refresh_status["step"] = "idle"
            refresh_status["detail"] = ""
        asyncio.create_task(_reset())


# ─── REPLENISHMENT ───────────────────────────────────────────────

def _enrich_with_waiters(items: List[dict]) -> None:
    """Add waiter_count to each item in-place. Uses the latest waiter snapshot.
    No-op if no snapshots have been imported. Cheap to call repeatedly — the
    DB query runs once and returns a small dict."""
    from .waiters import waiter_counts_map
    waiter_map = waiter_counts_map(db)
    if not waiter_map:
        for item in items:
            item["waiter_count"] = 0
        return
    for item in items:
        sku = (item.get("sku") or "").upper()
        item["waiter_count"] = waiter_map.get(sku, 0)


def _enrich_with_sales(items: List[dict]) -> None:
    """Overlay active vendor-sale info onto cached replenishment items.

    For each item with a matching active sale row:
      * replaces ``cost`` with the CAD-converted sale cost,
      * stashes the original on ``regular_cost`` for display,
      * recomputes ``forecast_profit`` from the new margin (so the user
        sees the discounted-reorder profit, not the cached one),
      * adds: ``on_sale``, ``sale_cost_foreign``, ``sale_currency``,
        ``sale_ends_at``.
    The sale fields are always set (False/0/"") so the frontend can rely
    on them existing whether or not a sale applies.
    """
    sale_lookup = db.build_sale_cost_lookup()
    for item in items:
        sku = (item.get("sku") or "").upper()
        sale = sale_lookup.get(sku)
        if sale and sale.get("sale_cost_cad"):
            regular_cost = float(item.get("cost") or 0)
            new_cost = float(sale["sale_cost_cad"])
            item["regular_cost"] = regular_cost
            item["cost"] = round(new_cost, 4)
            item["on_sale"] = True
            item["sale_cost_foreign"] = float(sale.get("sale_cost") or 0)
            item["sale_currency"] = sale.get("sale_currency") or ""
            item["sale_ends_at"] = sale.get("ends_at")
            # Re-derive forecast_profit using the discounted cost. The
            # cached forecast_profit was computed off regular cost, so it
            # under-states profit during a sale window.
            qty = item.get("replenish_qty", 0) or 0
            price = float(item.get("price") or 0)
            margin = price - new_cost if new_cost > 0 else price * 0.2
            # Sell-through assumption: same convention used at cache time
            # (cap at 1.0; the cached forecast_profit already incorporated
            # this, so a flat scale is the closest reproduction without
            # re-running the velocity engine).
            if regular_cost > 0:
                regular_margin = price - regular_cost
                if regular_margin > 0 and item.get("forecast_profit"):
                    # Scale the cached profit by the new-margin ratio;
                    # preserves the embedded sell-through factor.
                    item["forecast_profit"] = round(
                        float(item["forecast_profit"]) * (margin / regular_margin), 2,
                    )
                else:
                    item["forecast_profit"] = round(qty * margin, 2)
            else:
                item["forecast_profit"] = round(qty * margin, 2)
            # Total forecast profit is the simple full-order profit, so it
            # recomputes directly from the discounted margin (no sell-through
            # factor to preserve).
            item["total_forecast_profit"] = round(qty * margin, 2)
        else:
            item["on_sale"] = False
            item["regular_cost"] = float(item.get("cost") or 0)
            item["sale_cost_foreign"] = 0.0
            item["sale_currency"] = ""
            item["sale_ends_at"] = None


@app.get("/api/replenishment")
async def get_replenishment(
    vendor: Optional[str] = Query(None, description="Filter by vendor"),
    search: Optional[str] = Query(None, description="Search product name or SKU"),
    sort: str = Query("replenish_qty", description="Sort field"),
    dir: str = Query("desc", description="Sort direction (asc/desc)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    needs_replenish: bool = Query(True, description="Only show items needing replenishment"),
    replenishable_only: bool = Query(True, description="Exclude non-replenishable items"),
    token: str = Depends(verify_token),
):
    """
    Get replenishment recommendations from cache.
    Use POST /api/refresh to update the cache first.
    """
    try:
        result = db.get_cached_replenishment(
            vendor=vendor,
            search=search,
            sort_field=sort,
            sort_dir=dir,
            page=page,
            per_page=per_page,
            needs_replenish=needs_replenish,
            replenishable_only=replenishable_only,
        )

        items = result["items"]

        # Enrich with current waiter counts (in-place)
        _enrich_with_waiters(items)
        # Overlay active vendor-sale pricing (replaces cost, recomputes
        # forecast_profit; adds on_sale + sale_* fields used by the UI).
        _enrich_with_sales(items)

        # Calculate page totals
        totals = {
            "total_replenish_qty": sum(i.get("replenish_qty", 0) for i in items),
            "total_stock": sum(max(i.get("current_stock", 0), 0) for i in items),
            "total_on_order": sum(i.get("on_order", 0) for i in items),
            "total_365d_sales": sum(i.get("total_sold_365d", 0) for i in items),
            "total_waiters": sum(i.get("waiter_count", 0) for i in items),
            "total_replenish_cost": round(
                sum((i.get("replenish_qty", 0) or 0) * float(i.get("cost") or 0) for i in items),
                2,
            ),
            "total_forecast_profit": round(
                sum(float(i.get("forecast_profit") or 0) for i in items),
                2,
            ),
            # Sum of the full (non-pro-rated) order profit across the page —
            # backs the "Total forecast profit" column footer.
            "total_full_forecast_profit": round(
                sum(float(i.get("total_forecast_profit") or 0) for i in items),
                2,
            ),
            # Sum of sale prices isn't meaningful as a column total, but the
            # total replenish revenue (qty × price) is — surfaced for the
            # Sale price column footer.
            "total_replenish_revenue": round(
                sum((i.get("replenish_qty", 0) or 0) * float(i.get("price") or 0) for i in items),
                2,
            ),
        }

        return {**result, "totals": totals}

    except Exception as e:
        logger.error(f"Error fetching replenishment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/replenishment/all-items")
async def get_replenishment_all_items(
    vendor: Optional[str] = Query(None, description="Filter by vendor"),
    search: Optional[str] = Query(None, description="Search product name or SKU"),
    needs_replenish: bool = Query(True),
    replenishable_only: bool = Query(True),
    skus: Optional[str] = Query(None, description="Comma-separated SKUs to fetch (overrides filters)"),
    token: str = Depends(verify_token),
):
    """
    Get ALL items matching current filters (no pagination). Used for:
    - Select-all-across-pages on the Replenishment screen
    - Fetching full item data for a list of selected SKUs when creating a PO
      that spans multiple pages

    If `skus` is provided, returns just those SKUs (max 5000) — useful for
    bulk PO creation where the frontend has selected SKUs across pages and
    needs the full item details.
    """
    try:
        if skus:
            sku_list = [s.strip() for s in skus.split(",") if s.strip()]
            if len(sku_list) > 5000:
                raise HTTPException(400, "Too many SKUs (max 5000)")
            # Fetch the ENTIRE cache (no filters) and intersect with the
            # requested SKUs. This previously fetched only len(sku_list)+100
            # rows of the globally-sorted cache (default sort: replenish_qty
            # DESC across ALL vendors) before intersecting — so any selected
            # SKU that didn't rank inside that window was silently dropped.
            # Observed: a 66-SKU Celestron selection produced a 28-line PO.
            # The cache is ~3k rows and /replenishment/summary already does a
            # full per_page=99999 fetch every load, so this is safe.
            result = db.get_cached_replenishment(
                page=1, per_page=99999,
                vendor=None, search=None,
                needs_replenish=False, replenishable_only=False,
            )
            sku_set = set(sku_list)
            filtered = [i for i in result["items"] if i.get("sku") in sku_set]
            # Surface SKUs that genuinely aren't in the cache so the client
            # can warn instead of silently creating a smaller PO.
            found = {i.get("sku") for i in filtered}
            missing = sorted(sku_set - found)
            if missing:
                logger.warning(
                    f"all-items: {len(missing)} of {len(sku_list)} requested "
                    f"SKU(s) not found in velocity cache: {missing[:20]}"
                )
            _enrich_with_waiters(filtered)
            _enrich_with_sales(filtered)
            return {"items": filtered, "total": len(filtered), "missing_skus": missing}

        # Otherwise fetch everything matching the current filters
        # Use a high per_page; the current cache is ~3000 SKUs total so this is safe
        result = db.get_cached_replenishment(
            vendor=vendor, search=search,
            page=1, per_page=5000,
            needs_replenish=needs_replenish,
            replenishable_only=replenishable_only,
        )
        items = result["items"]
        _enrich_with_waiters(items)
        _enrich_with_sales(items)
        return {"items": items, "total": result.get("total", len(items))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching all-items: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/replenishment/summary")
async def get_replenishment_summary(token: str = Depends(verify_token)):
    """Get overall replenishment summary stats."""
    try:
        # Get all data (unpaginated) for summary
        result = db.get_cached_replenishment(per_page=99999)
        items = result["items"]

        total_variants = len(items)
        in_stock = sum(1 for i in items if i.get("current_stock", 0) > 0)
        needs_replenish = [i for i in items if i.get("replenish_qty", 0) > 0]

        # Vendor breakdown
        from collections import defaultdict
        vendor_stock = defaultdict(float)
        for i in items:
            stock = max(i.get("current_stock", 0), 0)
            if stock > 0:
                vendor_stock[i["vendor"]] += i.get("price", 0) * stock

        top_vendors = sorted(
            vendor_stock.items(), key=lambda x: x[1], reverse=True
        )[:8]

        return {
            "total_variants": total_variants,
            "variants_in_stock": in_stock,
            "total_stock_units": sum(max(i.get("current_stock", 0), 0) for i in items),
            "total_stock_cost": round(sum(i.get("cost", 0) * max(i.get("current_stock", 0), 0) for i in items), 2),
            "total_stock_retail": round(sum(i.get("price", 0) * max(i.get("current_stock", 0), 0) for i in items), 2),
            "replenishment_units": sum(i.get("replenish_qty", 0) for i in needs_replenish),
            "replenishment_cost": round(sum(i.get("cost", 0) * i.get("replenish_qty", 0) for i in needs_replenish), 2),
            "replenishment_retail": round(sum(i.get("price", 0) * i.get("replenish_qty", 0) for i in needs_replenish), 2),
            "top_vendors_by_retail": [{"vendor": v, "retail": round(r, 2)} for v, r in top_vendors],
            "cached_at": max((i.get("cached_at", "") for i in items), default=""),
        }

    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class PoReminderDismiss(BaseModel):
    active: bool = False   # False = stop showing; True = re-arm the reminder


@app.post("/api/vendors/{vendor}/po-reminder/dismiss")
async def dismiss_po_reminder(
    vendor: str, data: PoReminderDismiss, token: str = Depends(verify_token)
):
    """Turn a vendor's PO reminder popup off (or back on).

    Only flips the flag — the reminder text is kept so it can be re-enabled
    later without retyping. Called by the "Don't show again" button on the
    popup shown at PO creation.
    """
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE vendor_settings SET po_reminder_active = ?, updated_at = GETUTCDATE() "
                "WHERE vendor = ?",
                1 if data.active else 0, vendor,
            )
            updated = cursor.rowcount
            conn.commit()
        finally:
            conn.close()
        if not updated:
            raise HTTPException(status_code=404, detail=f"Vendor '{vendor}' not found")
        return {"status": "ok", "vendor": vendor, "po_reminder_active": data.active}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PO reminder for {vendor}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class ReplenishableToggle(BaseModel):
    skus: List[str]
    replenishable: bool  # True = make replenishable, False = mark non-replenishable


@app.post("/api/replenishment/toggle-replenishable")
async def toggle_replenishable(
    request: ReplenishableToggle, token: str = Depends(verify_token)
):
    """Mark SKUs as replenishable or non-replenishable."""
    try:
        if request.replenishable:
            count = db.set_replenishable(request.skus)
            return {"status": "ok", "action": "set_replenishable", "count": count}
        else:
            count = db.set_non_replenishable(request.skus)
            return {"status": "ok", "action": "set_non_replenishable", "count": count}
    except Exception as e:
        logger.error(f"Error toggling replenishable: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── STOCK ORDERS ────────────────────────────────────────────────

@app.get("/api/stock-orders")
async def list_stock_orders(
    status: str = Query("open", description="Filter: open, closed, all"),
    vendor: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search reference, vendor, comments, or line item SKU/title"),
    token: str = Depends(verify_token),
):
    """List stock orders with summary information."""
    try:
        orders = db.list_stock_orders(
            status_filter=status,
            vendor_filter=vendor,
            search=search,
        )
        return {"orders": orders, "total": len(orders)}
    except Exception as e:
        logger.error(f"Error listing stock orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock-orders/on-order-skus")
async def get_on_order_skus(token: str = Depends(verify_token)):
    """
    Return the set of SKUs that should carry the 'More on the Way' tag:
    those with units still on order (ordered > received on a non-closed PO)
    AND where the incoming units exceed any customer backorder hole
    (current_stock + on_order > 0).

    The backorder carve-out means a product whose entire in-progress PO is
    already claimed by existing backorders (backorders >= on_order) is
    EXCLUDED — it should read 'Back Ordered', not 'More on the Way', since a
    new customer wouldn't get stock from that PO.

    Used by the Azure Function "More on the Way" tagger to keep the Shopify
    'More on the Way' tag in sync with TC Planner POs. The tag indicates to
    storefront customers that a product is incoming. The tagger pre-clears
    the tag from every product each run and re-applies it to exactly this
    set, so a SKU dropping out of this list (received, closed, or fully
    backordered) gets the tag removed on the next run. Shopify Flow also
    removes the tag reactively when inventory rises above 0.

    Response shape: {"skus": ["12100", "22451", ...], "count": 25}

    Note: returns just the SKU list (not quantities) because the consumer
    only cares about presence/absence.
    """
    try:
        skus = sorted(db.get_motw_eligible_skus())
        return {"skus": skus, "count": len(skus)}
    except Exception as e:
        logger.error(f"Error fetching on-order SKUs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock-orders/{order_id}")
async def get_stock_order(order_id: int, token: str = Depends(verify_token)):
    """Get a stock order with all its line items, enriched with tags/inventory_policy/waiter_count."""
    order = db.get_stock_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Stock order not found")

    # Enrich items with tags and inventory_policy from velocity cache
    if order.get('items'):
        skus = [item['sku'] for item in order['items'] if item.get('sku')]
        if skus:
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                placeholders = ','.join(['?'] * len(skus))
                cursor.execute(f"""
                    SELECT sku, tags, inventory_policy
                    FROM product_velocity_cache
                    WHERE sku IN ({placeholders})
                """, *skus)
                cache_data = {}
                for row in cursor.fetchall():
                    cache_data[row[0]] = {'tags': row[1] or '', 'inventory_policy': row[2] or ''}
                # Which of this PO's SKUs are excluded from replenishment
                # suggestions — powers the toggle icon on the PO detail table.
                cursor.execute(f"""
                    SELECT sku FROM non_replenishable_skus
                    WHERE sku IN ({placeholders})
                """, *skus)
                non_repl_skus = {row[0] for row in cursor.fetchall()}
            finally:
                conn.close()

            # Enrich with current waiter counts (from latest snapshot)
            from .waiters import waiter_counts_map, compute_demand_boost
            waiter_map = waiter_counts_map(db)

            # How many of each SKU are already on order on OTHER open POs
            # (excludes this PO). Lets the operator see incoming units they
            # may not want to double-order.
            on_order_other = db.get_on_order_excluding_order(order_id, skus)

            for item in order['items']:
                cached = cache_data.get(item.get('sku'), {})
                item['tags'] = cached.get('tags', '')
                item['inventory_policy'] = cached.get('inventory_policy', '')
                item['on_order_other'] = on_order_other.get(item.get('sku'), 0)
                item['is_non_replenishable'] = item.get('sku') in non_repl_skus
                wc = waiter_map.get(item.get('sku', '').upper(), 0)
                item['waiter_count'] = wc
                # demand_boost on the PO detail represents the REMAINING gap to
                # cover the waitlist, taking this PO and current stock into
                # account. Previously this was the raw "ideal coverage"
                # (compute_demand_boost(wc)) which was misleading: an item with
                # 4 waiters showed "+3" even when this PO already ordered 2 of
                # them. Now "+N" means "N additional units still needed beyond
                # what's on this PO + on-hand stock." Stored separately is
                # demand_boost_ideal so the UI/tooltip can show both if useful.
                ideal = compute_demand_boost(wc) if wc else 0
                in_motion = max(0, item.get('current_stock') or 0) + (item.get('ordered_qty') or 0)
                item['demand_boost'] = max(0, ideal - in_motion)
                item['demand_boost_ideal'] = ideal

            # Enrich with the stock.bin variant metafield, read live from
            # Shopify. The bin location isn't cached locally (it's edited
            # directly on the PO screen and pushed straight to Shopify), so
            # we fetch it on demand. A failure here is non-fatal — the PO
            # still loads, the Bin column just shows blank.
            try:
                bin_map = await shopify_client.get_variant_bins_by_skus(skus)
            except Exception as e:
                logger.warning(f"Could not fetch stock.bin metafields: {e}")
                bin_map = {}
            for item in order['items']:
                item['bin'] = bin_map.get(item.get('sku'), '')

    # Full-shipment receive flag (2026-09-01): when the RFID app already
    # printed AND paired this order's labels via "Receive entire
    # shipment", the Print labels button grays out here - the labels are
    # on the boxes. Best effort with a short timeout; the PO must load
    # even with the RFID app down.
    order['rfid_labels_printed'] = False
    try:
        if config.RFID_STATION_KEY:
            import httpx
            async with httpx.AsyncClient(timeout=4) as client:
                resp = await client.get(
                    f"{config.RFID_APP_URL.rstrip('/')}"
                    f"/api/receiving/order-status/{order_id}",
                    headers={"X-Station-Key": config.RFID_STATION_KEY},
                )
            if resp.status_code < 400:
                order['rfid_labels_printed'] = bool(
                    resp.json().get('printed')
                )
    except Exception as e:
        logger.warning(f"RFID order-status check failed: {e}")

    return order


@app.post("/api/stock-orders/{order_id}/top-up-waiter-coverage")
async def top_up_waiter_coverage(order_id: int, token: str = Depends(verify_token)):
    """Bump each PO line's ``ordered_qty`` up to the live waiter coverage gap.

    For every line item where today's waitlist (× conversion rate) exceeds
    what's already on this PO + current stock, increase ordered_qty by
    the gap. Lines already meeting/exceeding coverage are untouched. Items
    with no waiters are untouched.

    This solves the timing problem where a PO was created when fewer
    customers were waiting; once a newer waitlist snapshot is uploaded
    the existing PO falls short. We call this automatically on every PO
    detail load so the operator never has to manually edit qtys.

    Status overrides (Discontinued, Replacement Part with 0+ stock,
    closed POs) are skipped — we don't want to silently escalate POs
    on retired SKUs or modify already-fulfilled orders.
    """
    from .waiters import waiter_counts_map, compute_demand_boost
    try:
        order = db.get_stock_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Stock order not found")
        if (order.get("status") or "").lower() == "closed":
            # Don't silently mutate a closed PO.
            return {"status": "ok", "bumped_count": 0, "total_units_added": 0, "bumped": []}

        items = order.get("items") or []

        # Pull tags + inventory_policy for this PO's SKUs from the velocity
        # cache. db.get_stock_order does NOT join these, so without this
        # lookup the rule below ``policy == 'DENY' and cur_stock <= 0`` would
        # see the default `policy = 'DENY'` for every line and skip them all.
        # That's the bug that produced "Topped up 0 lines (+0 units)".
        cache_meta = {}
        skus = [it.get("sku") for it in items if it.get("sku")]
        if skus:
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(skus))
                cursor.execute(
                    f"""SELECT sku, tags, inventory_policy
                          FROM product_velocity_cache
                         WHERE sku IN ({placeholders})""",
                    *skus,
                )
                for row in cursor.fetchall():
                    cache_meta[row[0]] = {
                        "tags": (row[1] or "").lower(),
                        "policy": (row[2] or "").upper(),
                    }
            finally:
                conn.close()

        waiter_map = waiter_counts_map(db)
        bumped = []
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            for item in items:
                sku = item.get("sku") or ""
                if not sku:
                    continue
                wc = waiter_map.get(sku.upper(), 0)
                if wc <= 0:
                    continue
                ideal = compute_demand_boost(wc)
                if ideal <= 0:
                    continue
                meta = cache_meta.get(sku) or {}
                tags = meta.get("tags", "")
                policy = meta.get("policy", "")
                cur_stock = int(item.get("current_stock") or 0)
                if "discontinued" in tags:
                    continue
                # DENY+OOS only applies when we actually have policy info.
                # Without it we shouldn't accidentally treat every line as DENY.
                if policy == "DENY" and cur_stock <= 0:
                    continue
                # Replacement Part: only top-up if backordered (negative stock).
                is_replacement = "replacement part" in tags
                if is_replacement and cur_stock >= 0:
                    continue

                in_motion = max(0, cur_stock) + int(item.get("ordered_qty") or 0)
                gap = ideal - in_motion
                if gap <= 0:
                    continue
                new_qty = int(item.get("ordered_qty") or 0) + gap
                cursor.execute(
                    "UPDATE stock_order_items SET ordered_qty = ?, updated_at = GETUTCDATE() WHERE id = ?",
                    new_qty, item["id"],
                )
                bumped.append({
                    "sku": sku,
                    "from_qty": int(item.get("ordered_qty") or 0),
                    "to_qty": new_qty,
                    "added": gap,
                    "waiter_count": wc,
                })
            conn.commit()
        finally:
            conn.close()
        return {
            "status": "ok",
            "bumped_count": len(bumped),
            "total_units_added": sum(b["added"] for b in bumped),
            "bumped": bumped,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Top-up waiter coverage error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class UpdateBinRequest(BaseModel):
    sku: str
    value: str = ""  # empty string clears the bin metafield


@app.post("/api/variants/bin")
async def update_variant_bin(data: UpdateBinRequest, token: str = Depends(verify_token)):
    """Set (or clear) the `stock.bin` variant metafield for a SKU.

    Called from the PO receiving screen's editable Bin column. Resolves
    the SKU → variant GID via the velocity cache, then writes the
    metafield straight to Shopify. An empty value clears the metafield.
    """
    sku = (data.sku or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="sku is required")
    try:
        # Resolve variant GID from the velocity cache.
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT variant_id FROM product_velocity_cache WHERE sku = ?",
                sku,
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            raise HTTPException(
                status_code=404,
                detail=f"SKU '{sku}' not found in product cache — can't resolve a Shopify variant to update.",
            )
        variant_id = row[0]
        await shopify_client.set_variant_stock_bin(variant_id, data.value)
        return {"status": "ok", "sku": sku, "bin": (data.value or "").strip()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating stock.bin for {sku}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class UpdateBarcodeRequest(BaseModel):
    sku: str
    value: str = ""  # empty string clears the barcode field


@app.post("/api/variants/barcode")
async def update_variant_barcode(data: UpdateBarcodeRequest, token: str = Depends(verify_token)):
    """Set (or clear) the native `barcode` field for a SKU's Shopify variant.

    Called from the PO detail screen's editable Barcode column. Resolves the
    SKU → variant GID + product GID via the velocity cache (productVariantsBulkUpdate
    needs the owning product id), writes the barcode straight to Shopify, then
    mirrors the new value into the cache so an immediate PO reload reflects it
    (the PO detail prefers the live cache barcode over the snapshot). An empty
    value clears the barcode.
    """
    sku = (data.sku or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="sku is required")
    value = (data.value or "").strip()
    try:
        # Resolve variant + product GIDs from the velocity cache.
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT variant_id, product_id FROM product_velocity_cache WHERE sku = ?",
                sku,
            )
            row = cursor.fetchone()
            if not row or not row[0] or not row[1]:
                raise HTTPException(
                    status_code=404,
                    detail=f"SKU '{sku}' not found in product cache — can't resolve a Shopify variant to update.",
                )
            variant_id, product_id = row[0], row[1]
            await shopify_client.set_variant_barcode(variant_id, product_id, value)
            # Mirror into the cache so a reload shows the new barcode without
            # waiting for the next Shopify sync.
            cursor.execute(
                "UPDATE product_velocity_cache SET barcode = ? WHERE sku = ?",
                (value or None), sku,
            )
            conn.commit()
        finally:
            conn.close()
        return {"status": "ok", "sku": sku, "barcode": value}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating barcode for {sku}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stock-orders")
async def create_stock_order(
    order: StockOrderCreate, token: str = Depends(verify_token)
):
    """Create a new stock order from selected replenishment items."""
    try:
        # Duplicate-submit guard: creating two POs for the same vendor within
        # a minute is essentially never intentional — it's a double-click on a
        # slow create (observed: duplicate ZWO POs 3.8s apart, the second full
        # of qty-0 lines because the first PO's recalc had already zeroed the
        # replenish recommendations). The UI also disables the button now;
        # this is the server-side backstop (covers laggy clients, two tabs).
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT TOP 1 reference_number, created_at FROM stock_orders
                   WHERE vendor = ? AND status != 'closed'
                     AND created_at > DATEADD(second, -60, GETUTCDATE())
                   ORDER BY created_at DESC""",
                order.vendor,
            )
            recent = cursor.fetchone()
        finally:
            conn.close()
        if recent:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A purchase order for {order.vendor} (#{recent[0]}) was created "
                    f"seconds ago — this looks like a duplicate submit. If you really "
                    f"want a second PO, wait a minute and try again."
                ),
            )

        items = [item.dict() for item in order.items]
        result = db.create_stock_order(
            vendor=order.vendor,
            items=items,
            expected_date=order.expected_date,
            notes=order.notes,
            comments=order.comments,
        )
        # Refresh cache for affected SKUs — they now have higher on_order
        # and (typically) zero replenish_qty
        affected = [it.sku for it in order.items if it.sku]
        if affected:
            db.recalc_on_order_for_skus(affected)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating stock order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class StockOrderHeaderUpdate(BaseModel):
    expected_date: Optional[str] = None
    comments: Optional[str] = None


@app.patch("/api/stock-orders/{order_id}")
async def update_stock_order_header(
    order_id: int, payload: StockOrderHeaderUpdate,
    token: str = Depends(verify_token),
):
    """Update PO header fields (currently expected_date and comments)."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            sets = []
            params = []
            # Use exclude_unset so callers can update one field without
            # accidentally clearing others
            data = payload.dict(exclude_unset=True)
            if 'expected_date' in data:
                sets.append('expected_date = ?')
                params.append(data['expected_date'])
            if 'comments' in data:
                sets.append('comments = ?')
                params.append(data['comments'])
            if not sets:
                raise HTTPException(400, "No fields to update")
            sets.append('updated_at = GETUTCDATE()')
            cursor.execute(
                f"UPDATE stock_orders SET {', '.join(sets)} WHERE id = ?",
                *params, order_id,
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Stock order not found")
            conn.commit()
        finally:
            conn.close()
        return db.get_stock_order(order_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PO header: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── INVENTORY PLANNER IMPORT ────────────────────────────────────

from .inventory_planner import (
    clear_all_stock_orders as ip_clear_all_stock_orders,
    import_from_ip as ip_import_orders,
)


class IpImportRequest(BaseModel):
    clear_existing: bool = False
    include_closed: bool = False
    confirm: bool = False  # must be true to actually clear


@app.delete("/api/stock-orders/all")
async def api_clear_all_stock_orders(
    confirm: bool = Query(False, description="Must be true to confirm deletion"),
    token: str = Depends(verify_token),
):
    """DANGER: delete every stock order and line item. Requires confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="This will delete every stock order. Pass confirm=true to proceed.",
        )
    try:
        return ip_clear_all_stock_orders(db)
    except Exception as e:
        logger.error(f"Clear all stock orders error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stock-orders/import-from-ip")
async def api_import_from_ip(
    req: IpImportRequest,
    token: str = Depends(verify_token),
):
    """
    Import stock orders from Inventory Planner via the IP API.
    Optionally clears all existing TC stock orders first when clear_existing=true
    AND confirm=true (both must be set to prevent accidents).
    """
    try:
        cleared_info = None
        if req.clear_existing:
            if not req.confirm:
                raise HTTPException(
                    status_code=400,
                    detail="clear_existing=true requires confirm=true to proceed.",
                )
            cleared_info = ip_clear_all_stock_orders(db)

        imported_info = await ip_import_orders(db, include_closed=req.include_closed)

        return {
            "status": "ok",
            "cleared": cleared_info,
            "imported": imported_info,
            "message": (
                (f"Cleared {cleared_info['orders_deleted']} old orders. " if cleared_info else "")
                + f"Imported {imported_info['imported']} orders "
                + f"({imported_info['total_items']} line items) from Inventory Planner."
                + (f" {imported_info['skipped_empty']} skipped (no line items)." if imported_info['skipped_empty'] else "")
                + (f" {imported_info.get('used_fallback_ref', 0)} used fallback reference numbers." if imported_info.get('used_fallback_ref', 0) else "")
                + (f" {len(imported_info['errors'])} errors." if imported_info['errors'] else "")
            ),
        }
    except HTTPException:
        raise
    except RuntimeError as e:
        # Config or API errors from the IP module
        logger.error(f"IP import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"IP import error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/stock-orders/{order_id}/status")
async def update_order_status(
    order_id: int, update: StatusUpdate, token: str = Depends(verify_token)
):
    """Update stock order status."""
    valid_statuses = [
        "open", "ordered", "ordered_invoice", "paid",
        "shipped", "partial_received", "partial_shipped", "closed",
    ]
    if update.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {valid_statuses}",
        )
    result = db.update_stock_order_status(order_id, update.status)
    if not result:
        raise HTTPException(status_code=404, detail="Stock order not found")
    # Closing or reopening an order changes whether its line items count toward
    # on_order. Refresh the cache rows for the SKUs on this order.
    affected = list({it.get("sku") for it in (result.get("items") or []) if it.get("sku")})
    if affected:
        db.recalc_on_order_for_skus(affected)
    return result


@app.delete("/api/stock-orders/{order_id}")
async def delete_order(
    order_id: int,
    confirm: bool = Query(False, description="Must be true to proceed"),
    token: str = Depends(verify_token),
):
    """Permanently delete a single stock order and its line items.
    Requires confirm=true to prevent accidental deletion."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass confirm=true to delete this order. This cannot be undone.",
        )
    try:
        result = db.delete_stock_order(order_id)
        if not result.get("deleted"):
            raise HTTPException(status_code=404, detail=result.get("reason", "Not found"))
        # Update cache for affected SKUs so the Replenishment screen reflects
        # the new on_order state without requiring a full refresh
        affected = result.get("skus", [])
        if affected:
            db.recalc_on_order_for_skus(affected)
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete stock order error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stock-orders/{order_id}/receive")
async def receive_items(
    order_id: int, request: ReceiveRequest,
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """Record received items in our database (does NOT update Shopify).

    The receiving user is resolved from their API token and logged with a
    UTC timestamp against each line (see stock_order_receipts).
    """
    try:
        items = [item.dict() for item in request.items]
        result = db.receive_stock_order_items(order_id, items, received_by=user)
        if not result:
            raise HTTPException(status_code=404, detail="Stock order not found")
        # Receiving reduces (ordered - received), which lowers on_order.
        # Refresh affected SKUs in the cache.
        affected = list({it.get("sku") for it in (result.get("items") or []) if it.get("sku")})
        if affected:
            db.recalc_on_order_for_skus(affected)
        return result
    except Exception as e:
        logger.error(f"Error receiving items: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



class UndoReceiveRequest(BaseModel):
    """qty omitted means undo everything received on that line."""
    qty: Optional[int] = None


@app.post("/api/stock-orders/{order_id}/items/{item_id}/undo-receive")
async def undo_receive_item(
    order_id: int, item_id: int,
    request: UndoReceiveRequest = UndoReceiveRequest(),
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """Reverse a receive on a single PO line.

    Units already pushed to Shopify are taken back out of Shopify too —
    undoing only the database would leave stock overstated by exactly the
    amount undone, which is the same class of silent drift that left PO 936
    short in the first place.
    """
    try:
        order = db.get_stock_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Stock order not found")
        line = next((i for i in order.get("items", []) if i.get("id") == item_id), None)
        if not line:
            raise HTTPException(status_code=404, detail="Line item not found on this order")

        received = int(line.get("received_qty") or 0)
        if received <= 0:
            raise HTTPException(status_code=400, detail="Nothing received on this line")

        qty = request.qty if request.qty is not None else received
        if qty <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be positive")
        if qty > received:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot undo {qty}; only {received} received on this line")

        result = db.undo_receive(order_id, item_id, qty, undone_by=user)
        undone = result.get("undone", 0)
        pushed_undone = result.get("pushed_undone", 0)
        sku = result.get("sku") or line.get("sku")

        # Take the already-pushed units back out of Shopify.
        shopify_note = None
        if pushed_undone > 0:
            try:
                canonical = db.resolve_skus_via_cache([sku]).get(sku, sku)
                inv = await shopify_client.lookup_inventory_by_skus([canonical])
                info = inv.get(canonical) or inv.get(sku)
                inv_item_id = (info or {}).get("inventory_item_id")
                if not inv_item_id:
                    shopify_note = (
                        f"Could not find {sku} in Shopify — its stock is now "
                        f"{pushed_undone} too high and needs a manual correction.")
                    logger.error("Undo receive: %s", shopify_note)
                else:
                    location_id = await shopify_client.get_receiving_location_id()
                    res = await shopify_client.adjust_inventory(
                        [{"inventory_item_id": inv_item_id,
                          "delta": -pushed_undone, "sku": sku}],
                        location_id, reason="correction",
                    )
                    ok = res and res[0].get("success")
                    if not ok:
                        shopify_note = (
                            f"Shopify adjustment failed ({(res[0] or {}).get('error') if res else 'no response'})"
                            f" — stock for {sku} is {pushed_undone} too high.")
                        logger.error("Undo receive: %s", shopify_note)
                    else:
                        qty_after = res[0].get("quantity_after")
                        if qty_after is None:
                            fresh = await shopify_client.lookup_inventory_by_skus([canonical])
                            qty_after = (fresh.get(canonical) or {}).get("available")
                        if qty_after is not None:
                            conn = db._get_connection()
                            try:
                                cur = conn.cursor()
                                cur.execute(
                                    "UPDATE product_velocity_cache SET current_stock = ?, "
                                    "cached_at = GETUTCDATE() WHERE UPPER(sku) = UPPER(?)",
                                    int(qty_after), canonical,
                                )
                                conn.commit()
                            finally:
                                conn.close()
            except Exception as e:
                shopify_note = (
                    f"Shopify adjustment failed ({e}) — stock for {sku} is "
                    f"{pushed_undone} too high.")
                logger.error("Undo receive Shopify error: %s", e, exc_info=True)

        # Receiving lowers on_order; undoing it puts that back.
        try:
            if sku:
                db.recalc_on_order_for_skus([sku])
        except Exception as e:
            logger.warning(f"Could not recalc on_order after undo: {e}")

        return {
            "status": "ok",
            "sku": sku,
            "undone": undone,
            "shopify_adjusted": pushed_undone if not shopify_note else 0,
            "warning": shopify_note,
            "order": db.get_stock_order(order_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Undo receive error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stock-orders/{order_id}/rfid-labels")
async def send_rfid_labels(
    order_id: int, request: ReceiveRequest,
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """Send just-received items to the RFID Stickers app's print queue.

    Server-to-server (the station key never reaches the browser). The
    RFID app creates/reuses a receiving batch named after this stock
    order, queues one RFID label per received unit (each printed with
    the product's home bin), and the warehouse pairs tags over there.
    Nothing here writes stock - "Increase stock in Shopify" stays the
    separate, explicit step it already is.
    """
    if not config.RFID_STATION_KEY:
        raise HTTPException(
            status_code=503,
            detail="RFID label bridge is not configured (set "
                   "RFID_STATION_KEY / RFID_APP_URL).",
        )
    order = db.get_stock_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Stock order not found")
    by_id = {it.get("id"): it for it in (order.get("items") or [])}
    rfid_items = []
    missing_sku = []
    for entry in request.items:
        it = by_id.get(entry.item_id)
        if not it or (entry.received_qty or 0) <= 0:
            continue
        if not it.get("sku"):
            missing_sku.append(str(entry.item_id))
            continue
        rfid_items.append({
            "sku": it["sku"],
            "quantity": int(entry.received_qty),
            "barcode": it.get("barcode") or None,
        })
    if not rfid_items:
        raise HTTPException(
            status_code=422,
            detail="Nothing printable in that receive (missing SKUs or "
                   "zero quantities).",
        )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.RFID_APP_URL.rstrip('/')}/api/receiving/prints",
                json={
                    "items": rfid_items,
                    "requested_by": user,
                    # The RFID side caps reference at 60 chars.
                    "reference": (
                        f"SO {order_id}"
                        + (f" · {order.get('vendor')}"
                           if order.get("vendor") else "")
                    )[:60],
                },
                headers={"X-Station-Key": config.RFID_STATION_KEY},
            )
        if resp.status_code >= 400:
            detail = resp.text[:300]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(
                status_code=502,
                detail=f"RFID app refused the print request: {detail}",
            )
        result = resp.json()
        if missing_sku:
            result["skipped_no_sku"] = missing_sku
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RFID label bridge error: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the RFID app: {e}",
        )


class PrepareStockItems(BaseModel):
    """Specific items to include in the stock update preview."""
    items: List[ReceiveItem] = []  # item_id + received_qty (the amounts just saved)


@app.post("/api/stock-orders/{order_id}/prepare-stock-update")
async def prepare_stock_update(
    order_id: int,
    request: Optional[PrepareStockItems] = None,
    token: str = Depends(verify_token),
):
    """
    Prepare a stock update summary for review.
    If specific items are provided, only includes those items with the
    specified adjustment quantities (from the just-saved receive batch).
    Otherwise falls back to all unreceived items.
    """
    try:
        order = db.get_stock_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Stock order not found")

        items = order.get("items", [])

        # If specific items were provided (from the just-saved batch), use those
        if request and request.items:
            specific_ids = {ri.item_id: ri.received_qty for ri in request.items}
            target_items = []
            for i in items:
                if i["id"] in specific_ids:
                    target_items.append({**i, "_adjustment": specific_ids[i["id"]]})
        else:
            # No batch supplied — offer everything received but never pushed to
            # Shopify. This is the recovery path when the browser lost the
            # in-memory batch (refresh, navigation, or starting the next one).
            outstanding = {r["item_id"]: r["qty"] for r in db.get_unpushed_receipts(order_id)}
            target_items = [
                {**i, "_adjustment": outstanding[i["id"]]}
                for i in items if i["id"] in outstanding and outstanding[i["id"]] > 0
            ]

        if not target_items:
            return {"items": [], "message": "No items to update"}

        # Look up current Shopify inventory for these SKUs. We first translate
        # each PO SKU through the local velocity cache using normalize_sku-aware
        # matching — that way SKUs that drifted in form between order time and
        # now (Ⅱ↔II, ″↔", etc.) still find their Shopify variant.
        po_skus = [i["sku"] for i in target_items if i.get("sku")]
        canonical_by_po = db.resolve_skus_via_cache(po_skus)
        skus_to_query = [canonical_by_po.get(s, s) for s in po_skus]
        inventory_data_canonical = await shopify_client.lookup_inventory_by_skus(skus_to_query)

        # Re-key by the original PO SKU so downstream rendering/UI still
        # references the SKU stored on the PO line.
        inventory_data = {}
        for po_sku in po_skus:
            canonical = canonical_by_po.get(po_sku, po_sku)
            info = inventory_data_canonical.get(canonical) or inventory_data_canonical.get(po_sku)
            if info:
                inventory_data[po_sku] = info

        # Location that receipts are booked into (the warehouse — never an
        # event location like Starfest).
        location_id = await shopify_client.get_receiving_location_id()

        # Build preview
        preview_items = []
        for item in target_items:
            sku = item.get("sku", "")
            adjustment = item["_adjustment"]
            if adjustment <= 0:
                continue
            shopify_info = inventory_data.get(sku, {})
            current = shopify_info.get("available", None)

            preview_items.append({
                "item_id": item["id"],
                "sku": sku,
                "product_title": item.get("product_title", ""),
                "adjustment": adjustment,
                "current_shopify_stock": current if current is not None else "N/A",
                "resulting_stock": (current + adjustment) if isinstance(current, int) else "N/A",
                "inventory_item_id": shopify_info.get("inventory_item_id", ""),
                "found_in_shopify": bool(shopify_info),
            })

        return {
            "items": preview_items,
            "location_id": location_id,
            "order_id": order_id,
            "vendor": order.get("vendor", ""),
            "reference_number": order.get("reference_number", 0),
        }

    except Exception as e:
        logger.error(f"Error preparing stock update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class StockUpdateItem(BaseModel):
    item_id: int
    sku: str
    adjustment: int
    inventory_item_id: Optional[str] = None


class ApplyStockUpdateRequest(BaseModel):
    location_id: str
    items: List[StockUpdateItem]
    # Line-item ids in this update that never had RFID labels printed
    # (the stock-update window tracks its own Print-labels presses,
    # 2026-08-26). Pushed-without-labels lines are relayed to the RFID
    # app, which books them into the receiving batch WITHOUT labels and
    # files one safety-net Review task; resolving it queues the labels.
    unprinted_item_ids: List[int] = []


@app.post("/api/stock-orders/{order_id}/apply-stock-update")
async def apply_stock_update(
    order_id: int, request: ApplyStockUpdateRequest,
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """
    Apply stock updates to Shopify and record receives in our database.
    This is the final step after the user reviews the preview.
    """
    try:
        # 1. Push adjustments to Shopify
        adjustments = []
        unresolved = []
        for item in request.items:
            if item.adjustment <= 0:
                continue
            if not item.inventory_item_id:
                # Previously skipped in silence, so the receive looked applied
                # when nothing had been sent. Report it instead.
                unresolved.append(item.sku)
                continue
            adjustments.append({
                "inventory_item_id": item.inventory_item_id,
                "delta": item.adjustment,
                "sku": item.sku,
            })

        shopify_results = []
        if adjustments:
            shopify_results = await shopify_client.adjust_inventory(
                adjustments, request.location_id, reason="received"
            )
        for sku in unresolved:
            shopify_results.append({
                "sku": sku, "success": False,
                "error": "No Shopify inventory item found for this SKU",
            })

        # Receives were recorded by the Save step; mark the ones that have now
        # genuinely reached Shopify so anything left over stays visible as
        # outstanding instead of being silently forgotten.
        pushed_by_sku = {
            r["sku"]: r for r in shopify_results if r.get("success") and r.get("sku")
        }
        for item in request.items:
            if item.sku in pushed_by_sku and item.adjustment > 0:
                try:
                    db.mark_receipts_pushed(order_id, item.item_id, item.adjustment)
                except Exception as e:
                    logger.warning(
                        "Could not mark receipts pushed for %s: %s", item.sku, e)

        # Write the post-adjustment Shopify quantity back into the velocity
        # cache so the 'More on the Way' backorder rule (current_stock +
        # on_order) reflects this receive immediately, instead of waiting for
        # the next full Shopify refresh. adjust_inventory returns the
        # authoritative quantity_after for each successful change.
        try:
            stock_updates = [
                (r["sku"], int(r["quantity_after"]))
                for r in shopify_results
                if r.get("success") and r.get("sku") and r.get("quantity_after") is not None
            ]
            # Shopify returns an empty `changes` array for "available"
            # adjustments, so quantity_after is usually absent and the cache
            # was never actually refreshed here. Re-read the adjusted SKUs so
            # the backorder rule (current_stock + on_order) sees the receive
            # straight away instead of acting on pre-receive negatives.
            covered = {sku for sku, _ in stock_updates}
            missing = [
                r["sku"] for r in shopify_results
                if r.get("success") and r.get("sku") and r["sku"] not in covered
            ]
            if missing:
                try:
                    fresh = await shopify_client.lookup_inventory_by_skus(missing)
                    for sku, info in fresh.items():
                        qty = info.get("available")
                        if qty is not None:
                            stock_updates.append((sku, int(qty)))
                except Exception as e:
                    logger.warning(f"Could not re-read stock after receive: {e}")

            if stock_updates:
                conn = db._get_connection()
                try:
                    cur = conn.cursor()
                    for sku, qty_after in stock_updates:
                        cur.execute(
                            "UPDATE product_velocity_cache SET current_stock = ?, cached_at = GETUTCDATE() WHERE UPPER(sku) = UPPER(?)",
                            qty_after, sku,
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"Could not sync cached stock after receive: {e}")

        # RFID safety net (2026-08-26): lines pushed to Shopify WITHOUT
        # RFID labels book into the RFID app's receiving batch label-less
        # and file one Review task over there; resolving it queues the
        # labels (mechanically identical to Print labels). Best effort -
        # a bridge outage must never fail the stock push itself.
        rfid_safety_net = None
        try:
            if request.unprinted_item_ids and config.RFID_STATION_KEY:
                unprinted_ids = set(request.unprinted_item_ids)
                order = db.get_stock_order(order_id) or {}
                by_id = {
                    it.get("id"): it for it in (order.get("items") or [])
                }
                rfid_items = []
                for item in request.items:
                    if (item.item_id in unprinted_ids
                            and item.adjustment > 0
                            and item.sku in pushed_by_sku):
                        line = by_id.get(item.item_id) or {}
                        rfid_items.append({
                            "sku": item.sku,
                            "quantity": int(item.adjustment),
                            "barcode": line.get("barcode") or None,
                        })
                if rfid_items:
                    import httpx
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            f"{config.RFID_APP_URL.rstrip('/')}"
                            f"/api/receiving/unprinted",
                            json={
                                "items": rfid_items,
                                "requested_by": user,
                                # Same reference as rfid-labels so both
                                # paths land on ONE receiving batch.
                                "reference": (
                                    f"SO {order_id}"
                                    + (f" · {order.get('vendor')}"
                                       if order.get("vendor") else "")
                                )[:60],
                            },
                            headers={
                                "X-Station-Key": config.RFID_STATION_KEY,
                            },
                        )
                    if resp.status_code < 400:
                        rfid_safety_net = resp.json()
                    else:
                        logger.warning(
                            "RFID safety net refused: %s", resp.text[:200]
                        )
        except Exception as e:
            logger.warning(f"RFID safety net relay failed: {e}")

        # Full-shipment watchdog ping (2026-09-01): tell the RFID app
        # this order's Shopify stock was updated - it stamps the order
        # receipt and auto-closes the "stock not updated" Review task
        # its 1-hour clock may have filed. Best effort, never fails the
        # push.
        try:
            if config.RFID_STATION_KEY:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{config.RFID_APP_URL.rstrip('/')}"
                        f"/api/receiving/stock-updated",
                        json={"stock_order_id": order_id,
                              "updated_by": user},
                        headers={
                            "X-Station-Key": config.RFID_STATION_KEY,
                        },
                    )
        except Exception as e:
            logger.warning(f"RFID stock-updated ping failed: {e}")

        # Build response
        success_count = sum(1 for r in shopify_results if r.get("success"))
        error_count = sum(1 for r in shopify_results if not r.get("success"))

        return {
            "status": "ok",
            "shopify_results": shopify_results,
            "success_count": success_count,
            "error_count": error_count,
            "total_items": len(request.items),
            "rfid_safety_net": rfid_safety_net,
            "message": f"Updated {success_count} items in Shopify"
            + (f", {error_count} errors" if error_count else ""),
        }

    except Exception as e:
        logger.error(f"Error applying stock update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class EditItemRequest(BaseModel):
    ordered_qty: Optional[int] = None
    received_qty: Optional[int] = None
    unit_cost: Optional[float] = None


class AddItemRequest(BaseModel):
    sku: str
    ordered_qty: int
    unit_cost: Optional[float] = None
    product_title: Optional[str] = None
    variant_title: Optional[str] = None
    vendor: Optional[str] = None
    barcode: Optional[str] = None
    unit_price: Optional[float] = None


@app.post("/api/stock-orders/{order_id}/items")
async def add_stock_order_item(
    order_id: int, request: AddItemRequest,
    token: str = Depends(verify_token),
):
    """Add a new line item to an existing stock order.

    If the SKU exists in the velocity cache, missing fields (product_title,
    variant_title, vendor, unit_cost, unit_price) are auto-filled from it.
    If the SKU is not in the cache, the caller must provide product_title
    (and optionally other fields) — this supports adding brand-new products
    that haven't been created in Shopify yet.
    """
    sku = (request.sku or '').strip()
    if not sku:
        raise HTTPException(status_code=400, detail="SKU is required")
    if request.ordered_qty is None or request.ordered_qty <= 0:
        raise HTTPException(status_code=400, detail="ordered_qty must be > 0")

    try:
        # Make sure the order exists
        order = db.get_stock_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Stock order not found")
        if order.get('status') == 'closed':
            raise HTTPException(
                status_code=400,
                detail="Cannot add items to a closed order. Reopen it first.",
            )

        # Try to look up the SKU for auto-fill
        cache = None
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT product_title, variant_title, vendor, price, cost
                FROM product_velocity_cache WHERE sku = ?
            """, sku)
            row = cursor.fetchone()
            if row:
                cache = {
                    'product_title': row[0],
                    'variant_title': row[1],
                    'vendor': row[2],
                    'price': float(row[3] or 0),
                    'cost': float(row[4] or 0),
                }
        finally:
            conn.close()

        # Resolve each field: request value first, then cache, then fallback
        product_title = request.product_title or (cache['product_title'] if cache else None)
        if not product_title:
            raise HTTPException(
                status_code=400,
                detail=(f"SKU '{sku}' was not found in inventory. To add a new "
                         "product line, include product_title in the request."),
            )

        variant_title = request.variant_title if request.variant_title is not None else (cache['variant_title'] if cache else None)
        vendor = request.vendor or (cache['vendor'] if cache else None) or order.get('vendor')
        unit_cost = request.unit_cost if request.unit_cost is not None else (cache['cost'] if cache else 0)
        unit_price = request.unit_price if request.unit_price is not None else (cache['price'] if cache else 0)
        barcode = request.barcode

        # Vendor-sale override: if the caller didn't pin an explicit unit_cost,
        # check whether this SKU has an active vendor sale and use the
        # discounted CAD-converted cost. The original (non-sale) cost is
        # preserved on regular_unit_cost so the PO can later display
        # "was $X, on sale at $Y" and report savings.
        is_vendor_sale = False
        regular_unit_cost = None
        if request.unit_cost is None:
            sale_lookup = db.build_sale_cost_lookup()
            sale = sale_lookup.get(sku.upper())
            if sale and sale.get('sale_cost_cad'):
                regular_unit_cost = float(unit_cost or 0)  # cache cost (non-sale)
                unit_cost = float(sale['sale_cost_cad'])
                is_vendor_sale = True
                logger.info(
                    f"PO line {sku}: using sale price ${unit_cost:.2f} CAD "
                    f"(regular ${regular_unit_cost:.2f}); ends {sale.get('ends_at')}"
                )

        # Insert the line item and recalc total
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stock_order_items
                    (stock_order_id, product_title, variant_title, sku, barcode,
                     vendor, ordered_qty, received_qty, unit_cost, unit_price,
                     is_vendor_sale, regular_unit_cost)
                OUTPUT INSERTED.id
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
                order_id,
                product_title[:500],
                (variant_title or '')[:200] or None,
                sku[:100],
                (barcode or '')[:100] or None,
                (vendor or '')[:200] or None,
                int(request.ordered_qty),
                float(unit_cost or 0),
                float(unit_price or 0),
                1 if is_vendor_sale else 0,
                regular_unit_cost,
            )
            new_id = cursor.fetchone()[0]

            # Recalculate order total (same pattern as edit endpoint)
            cursor.execute("""
                UPDATE stock_orders SET
                    total_cost = (SELECT ISNULL(SUM(unit_cost * ordered_qty), 0)
                                  FROM stock_order_items WHERE stock_order_id = ?),
                    updated_at = GETUTCDATE()
                WHERE id = ?
            """, order_id, order_id)
            conn.commit()
        finally:
            conn.close()

        logger.info(f"Added item {new_id} to stock order {order_id}: "
                    f"{sku} qty={request.ordered_qty} (source={'cache' if cache else 'manual'})")

        # Refresh velocity cache for this SKU (on_order increased)
        db.recalc_on_order_for_skus([sku])

        return {
            "status": "ok",
            "item_id": new_id,
            "source": "cache" if cache else "manual",
            "order": db.get_stock_order(order_id),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding item to stock order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/stock-orders/{order_id}/items/{item_id}")
async def edit_stock_order_item(
    order_id: int, item_id: int, request: EditItemRequest,
    token: str = Depends(verify_token),
):
    """Edit a stock order item's quantities or cost."""
    try:
        # Determine whether we need to refresh the velocity cache. Changes to
        # ordered_qty or received_qty affect on_order; unit_cost alone does not.
        affects_on_order = (
            request.ordered_qty is not None or request.received_qty is not None
        )
        affected_sku = None
        if affects_on_order:
            # Capture SKU before mutation so we can recalc its cache row after
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT sku FROM stock_order_items WHERE id = ? AND stock_order_id = ?",
                    item_id, order_id,
                )
                row = cursor.fetchone()
                affected_sku = row[0] if row else None
            finally:
                conn.close()

        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            updates = []
            params = []

            if request.ordered_qty is not None:
                updates.append("ordered_qty = ?")
                params.append(request.ordered_qty)
            if request.received_qty is not None:
                updates.append("received_qty = ?")
                params.append(request.received_qty)
            if request.unit_cost is not None:
                updates.append("unit_cost = ?")
                params.append(request.unit_cost)

            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")

            updates.append("updated_at = GETUTCDATE()")
            sql = f"UPDATE stock_order_items SET {', '.join(updates)} WHERE id = ? AND stock_order_id = ?"
            params.extend([item_id, order_id])

            cursor.execute(sql, *params)

            # Recalculate order total
            cursor.execute(
                """UPDATE stock_orders SET
                    total_cost = (SELECT ISNULL(SUM(unit_cost * ordered_qty), 0)
                                  FROM stock_order_items WHERE stock_order_id = ?),
                    updated_at = GETUTCDATE()
                   WHERE id = ?""",
                order_id, order_id,
            )
            conn.commit()
        finally:
            conn.close()

        if affected_sku:
            db.recalc_on_order_for_skus([affected_sku])

        return db.get_stock_order(order_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error editing item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/stock-orders/{order_id}/items/{item_id}")
async def delete_stock_order_item(
    order_id: int, item_id: int,
    token: str = Depends(verify_token),
):
    """Delete a single line item from a stock order. Recalculates order total."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            # Verify item exists on this order before deleting (avoids quietly
            # succeeding on a stale item_id and confusing the user)
            cursor.execute(
                "SELECT sku FROM stock_order_items WHERE id = ? AND stock_order_id = ?",
                item_id, order_id,
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found on this order")
            sku = row[0]

            cursor.execute(
                "DELETE FROM stock_order_items WHERE id = ? AND stock_order_id = ?",
                item_id, order_id,
            )

            # Recalculate order total
            cursor.execute(
                """UPDATE stock_orders SET
                    total_cost = (SELECT ISNULL(SUM(unit_cost * ordered_qty), 0)
                                  FROM stock_order_items WHERE stock_order_id = ?),
                    updated_at = GETUTCDATE()
                   WHERE id = ?""",
                order_id, order_id,
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(f"Deleted item {item_id} (SKU {sku}) from order {order_id}")
        # Refresh cache row for this SKU (on_order decreased)
        if sku:
            db.recalc_on_order_for_skus([sku])
        return db.get_stock_order(order_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── OVERVIEW DASHBOARD ──────────────────────────────────────────

@app.get("/api/overview")
async def get_overview(token: str = Depends(verify_token)):
    """Get all data for the overview dashboard."""
    try:
        return db.get_overview_data()
    except Exception as e:
        logger.error(f"Error fetching overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── BACKORDERS ──────────────────────────────────────────────────

# ─── BACKORDER SOURCE ORDERS ─────────────────────────────────────────────
# The backorder list comes from negative stock in the velocity cache, which
# says nothing about *which* customer orders caused it. Negative available
# stock means committed exceeds on-hand, and the committed side is the
# unfulfilled line items — so the orders behind a backorder are the open
# unfulfilled ones containing that SKU.
#
# One paginated sweep covers every backordered SKU at once (about 100 orders
# / 2 pages / ~1.5s for this store), which is far cheaper than a per-SKU
# query and keeps the list a single request.

SHOPIFY_ADMIN_ORDER_URL = "https://admin.shopify.com/store/{store}/orders/{order_id}"


async def _unfulfilled_orders_by_sku() -> dict:
    """{normalized_sku: [ {name, order_number, created_at, qty, admin_url} ]}"""
    from .shopify_client import normalize_sku

    query = """
    query($after: String) {
      orders(first: 100, after: $after,
             query: "fulfillment_status:unfulfilled AND status:open") {
        pageInfo { hasNextPage endCursor }
        edges { node {
          id name createdAt
          lineItems(first: 50) {
            edges { node { sku quantity unfulfilledQuantity } }
          }
        } }
      }
    }
    """
    store_handle = (config.SHOPIFY_STORE or "").replace(".myshopify.com", "")
    by_sku: dict = {}
    after = None
    while True:
        data = await shopify_client._query(query, {"after": after})
        block = data.get("orders") or {}
        for edge in block.get("edges", []):
            node = edge["node"]
            order_id = str(node["id"]).split("/")[-1]
            entry_base = {
                "order_number": node.get("name", ""),
                "created_at": node.get("createdAt"),
                "admin_url": SHOPIFY_ADMIN_ORDER_URL.format(
                    store=store_handle, order_id=order_id),
            }
            for li_edge in (node.get("lineItems") or {}).get("edges", []):
                li = li_edge["node"]
                sku = li.get("sku")
                if not sku:
                    continue
                qty = li.get("unfulfilledQuantity")
                if qty is None:
                    qty = li.get("quantity") or 0
                if qty <= 0:
                    continue
                key = normalize_sku(sku).upper()
                by_sku.setdefault(key, []).append({**entry_base, "qty": qty})
        page = block.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")

    # Oldest first — the order that has been waiting longest is the one that
    # matters when deciding what to chase.
    for entries in by_sku.values():
        entries.sort(key=lambda e: e.get("created_at") or "")
    return by_sku


@app.get("/api/backorders")
async def get_backorders(token: str = Depends(verify_token)):
    """
    Get products with customer backorders (stock < 0) where on-order
    doesn't fully cover the deficit.
    """
    try:
        items = db.get_backorder_items()

        # Attach the customer orders behind each backorder. Best effort: if
        # Shopify is unreachable the list still renders, just without dates.
        try:
            from .shopify_client import normalize_sku
            by_sku = await _unfulfilled_orders_by_sku()
            for item in items:
                key = normalize_sku(item.get("sku") or "").upper()
                item["backorder_orders"] = by_sku.get(key, [])
        except Exception as e:
            logger.warning(f"Could not attach backorder source orders: {e}")
            for item in items:
                item["backorder_orders"] = []

        summary = {
            "total_items": len(items),
            "total_backordered_units": sum(abs(i.get("current_stock", 0)) for i in items),
            "total_uncovered_units": sum(abs(i.get("net_position", 0)) for i in items),
            "total_retail_value": round(sum(abs(i.get("net_position", 0)) * i.get("price", 0) for i in items), 2),
        }
        return {"items": items, "summary": summary}
    except Exception as e:
        logger.error(f"Error fetching backorders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refresh-inventory")
async def refresh_inventory_only(token: str = Depends(verify_token)):
    """
    Lightweight inventory refresh — only updates stock levels in the cache.
    Much faster than full /api/refresh. Intended for hourly use.
    """
    try:
        logger.info("Starting lightweight inventory refresh...")
        inventory = await shopify_client.fetch_all_inventory_levels()
        count = db.update_inventory_levels(inventory)
        logger.info(f"Lightweight refresh complete: {count} rows updated")
        return {
            "status": "ok",
            "variants_checked": len(inventory),
            "rows_updated": count,
        }
    except Exception as e:
        logger.error(f"Lightweight refresh failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── BARCODE LABEL PRINT QUEUE ────────────────────────────────────
# Cloud-side queue + agent dispatch. The frontend POSTs label jobs
# here when the user receives stock; a small Python agent on the
# receiving PC polls /api/labels/pending, prints via Browser Print on
# its own localhost (which is NOT subject to Chrome's Local Network
# Access enforcement because the agent isn't a webpage), then POSTs
# /api/labels/{id}/printed to confirm.

class LabelJob(BaseModel):
    sku: str
    barcode: str
    qty: int = 1
    stock_order_id: Optional[int] = None


class LabelEnqueueRequest(BaseModel):
    jobs: List[LabelJob]


@app.post("/api/labels")
async def labels_enqueue(data: LabelEnqueueRequest, token: str = Depends(verify_token)):
    """Frontend → backend: queue label jobs for the local agent to claim."""
    try:
        jobs = [j.dict() for j in data.jobs]
        created = db.enqueue_label_jobs(jobs)
        return {"status": "ok", "created": created}
    except Exception as e:
        logger.error(f"Label enqueue error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/labels/pending")
async def labels_get_pending(
    agent_id: str = "default-agent",
    limit: int = 25,
    token: str = Depends(verify_token),
):
    """Agent → backend: claim up to ``limit`` pending jobs and return them.
    The claim is atomic so multiple agents (rare, but supported) won't
    print the same job twice."""
    try:
        jobs = db.claim_pending_label_jobs(agent_id=agent_id, limit=limit)
        return {"jobs": jobs}
    except Exception as e:
        logger.error(f"Label claim error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class LabelFailRequest(BaseModel):
    error: str = ""


@app.post("/api/labels/{job_id}/printed")
async def labels_mark_printed(job_id: int, token: str = Depends(verify_token)):
    """Agent → backend: confirm successful print."""
    try:
        ok = db.mark_label_printed(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Job not found or not in claimed state")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Label printed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/labels/{job_id}/failed")
async def labels_mark_failed(job_id: int, data: LabelFailRequest, token: str = Depends(verify_token)):
    """Agent → backend: report a permanent failure (logged for diagnostics)."""
    try:
        ok = db.mark_label_failed(job_id, data.error)
        if not ok:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Label failed-mark error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── EMAIL PURCHASE ORDER ────────────────────────────────────────

class EmailPORequest(BaseModel):
    to_email: Optional[str] = None  # Override default recipient


@app.post("/api/stock-orders/{order_id}/email")
async def email_stock_order(
    order_id: int,
    request: Optional[EmailPORequest] = None,
    token: str = Depends(verify_token),
):
    """Email an XLSX of the purchase order."""
    import csv  # kept for any future fallback paths; XLSX is primary
    import io
    import base64
    import re

    try:
        order = db.get_stock_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Stock order not found")

        vendor = order.get("vendor", "Unknown")
        ref_num = order.get("reference_number", 0)
        # Sort line items A→Z by SKU so the emailed CSV is easy to scan
        # against the supplier's reply. Case-insensitive; blank SKUs sink.
        items = sorted(
            order.get("items", []),
            key=lambda i: ((i.get("sku") or "").strip().upper() or "￿"),
        )

        to_email = (request.to_email if request and request.to_email else config.PO_EMAIL_TO)
        if not to_email:
            raise HTTPException(status_code=400, detail="No email address configured. Set PO_EMAIL_TO in .env")

        if not config.ACS_CONNECTION_STRING:
            raise HTTPException(status_code=400, detail="ACS_CONNECTION_STRING not configured in .env")

        if not config.PO_EMAIL_FROM:
            raise HTTPException(status_code=400, detail="PO_EMAIL_FROM not configured in .env")

        # Determine if this vendor needs extra columns
        extra_fields = config.VENDORS_WITH_EXTRA_FIELDS.get(vendor, [])

        # Look up metafields from cache if extra fields needed
        item_metadata = {}
        if extra_fields:
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                skus = [i["sku"] for i in items if i.get("sku")]
                if skus:
                    placeholders = ",".join(["?"] * len(skus))
                    cursor.execute(
                        f"SELECT sku, cost_usd, system_code FROM product_velocity_cache WHERE sku IN ({placeholders})",
                        *skus,
                    )
                    for row in cursor.fetchall():
                        item_metadata[row[0]] = {"cost_usd": row[1] or "", "system_code": row[2] or ""}
            finally:
                conn.close()

        # Build XLSX
        import openpyxl
        from openpyxl.styles import Font, Alignment

        base_headers = ["SKU", "Quantity", "Name"]
        extra_headers = []
        if "cost_usd" in extra_fields:
            extra_headers.append("Cost USD")
        if "system_code" in extra_fields:
            extra_headers.append("System Code")
        all_headers = base_headers + extra_headers
        # Index of the Quantity column (1-based). Used to center both the
        # header and every data cell in that column.
        qty_col_idx = all_headers.index("Quantity") + 1

        wb = openpyxl.Workbook()
        ws = wb.active
        # Sheet tab name: 31-char Excel limit, strip illegal chars (: \ / ? * [ ])
        safe_vendor = re.sub(r'[:\\/?*\[\]]', '', str(vendor))[:25] or "PO"
        ws.title = f"{safe_vendor} PO"

        # Header row, bold
        ws.append(all_headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)

        for item in items:
            row = [
                item.get("sku", ""),
                item.get("ordered_qty", 0),
                item.get("product_title", ""),
            ]
            if "cost_usd" in extra_fields:
                meta = item_metadata.get(item.get("sku", ""), {})
                row.append(meta.get("cost_usd", ""))
            if "system_code" in extra_fields:
                meta = item_metadata.get(item.get("sku", ""), {})
                row.append(meta.get("system_code", ""))
            ws.append(row)

        # Center-align every cell in the Quantity column (header + data).
        center = Alignment(horizontal="center", vertical="center")
        for row_cells in ws.iter_rows(min_col=qty_col_idx, max_col=qty_col_idx):
            for cell in row_cells:
                cell.alignment = center

        # Roughly auto-size columns based on the longest cell content per column.
        # openpyxl has no true autosize, so we measure and clamp [10, 60].
        for col_idx, header in enumerate(all_headers, start=1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            max_len = len(str(header))
            for cell in ws[col_letter][1:]:
                v = cell.value
                if v is not None:
                    max_len = max(max_len, len(str(v)))
            ws.column_dimensions[col_letter].width = max(10, min(max_len + 2, 60))

        xlsx_buffer = io.BytesIO()
        wb.save(xlsx_buffer)
        xlsx_bytes = xlsx_buffer.getvalue()
        xlsx_b64 = base64.b64encode(xlsx_bytes).decode("utf-8")

        # Send via Azure Communication Services Email
        import httpx
        import hashlib
        import hmac
        from datetime import timezone as tz
        from urllib.parse import urlparse

        # Filename uses today's local date in the form "May 7th 2026" so the
        # attached file is human-friendly to file/search later. We pull the
        # date in America/Toronto so it reflects the operator's wall clock
        # rather than UTC (which can roll the day before EDT/EST midnight).
        try:
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("America/Toronto"))
        except Exception:
            today = datetime.now()

        def _day_ordinal(n: int) -> str:
            if 10 <= n % 100 <= 20:
                return "th"
            return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

        date_str = f"{today.strftime('%B')} {today.day}{_day_ordinal(today.day)} {today.year}"

        subject = f"{vendor} - Purchase Order #{ref_num}"
        # Format: "<Vendor> Order - May 7th 2026 - Telescopes Canada.xlsx"
        # Strip filename-illegal chars from the vendor name only — Excel and
        # most filesystems are happy with the rest of the string as-is.
        safe_vendor_fname = re.sub(r'[\\/:*?"<>|]', '', str(vendor)).strip() or "Vendor"
        filename = f"{safe_vendor_fname} Order - {date_str} - Telescopes Canada.xlsx"

        # Parse connection string
        cs_parts = {}
        for part in config.ACS_CONNECTION_STRING.split(";"):
            if "=" in part:
                key, val = part.split("=", 1)
                cs_parts[key.lower()] = val
        acs_endpoint = cs_parts.get("endpoint", "").rstrip("/")
        acs_key = cs_parts.get("accesskey", "")

        if not acs_endpoint or not acs_key:
            raise HTTPException(status_code=400, detail="Invalid ACS connection string")

        # Build email payload
        email_payload = {
            "senderAddress": config.PO_EMAIL_FROM,
            "content": {
                "subject": subject,
                "plainText": f"Please find attached Purchase Order #{ref_num} for {vendor}.\n\n{len(items)} items, total: ${order.get('total_cost', 0):,.2f} CAD",
            },
            "recipients": {
                "to": [{"address": to_email, "displayName": ""}],
            },
            "attachments": [{
                "name": filename,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentInBase64": xlsx_b64,
            }],
        }

        # ACS HMAC-SHA256 authentication
        api_version = "2023-03-31"
        url_path = f"/emails:send?api-version={api_version}"
        full_url = f"{acs_endpoint}{url_path}"
        parsed = urlparse(acs_endpoint)
        host = parsed.hostname

        body_bytes = json.dumps(email_payload).encode("utf-8")
        content_hash = base64.b64encode(
            hashlib.sha256(body_bytes).digest()
        ).decode("utf-8")

        now_utc = datetime.now(tz.utc)
        date_str = now_utc.strftime("%a, %d %b %Y %H:%M:%S GMT")

        string_to_sign = f"POST\n{url_path}\n{date_str};{host};{content_hash}"
        key_bytes = base64.b64decode(acs_key)
        signature = base64.b64encode(
            hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

        auth_header = (
            f"HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256"
            f"&Signature={signature}"
        )

        headers = {
            "Content-Type": "application/json",
            "x-ms-date": date_str,
            "x-ms-content-sha256": content_hash,
            "Authorization": auth_header,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(full_url, content=body_bytes, headers=headers)

        if resp.status_code >= 400:
            raise Exception(f"ACS Email error {resp.status_code}: {resp.text}")

        return {
            "status": "ok",
            "message": f"PO #{ref_num} emailed to {to_email}",
            "subject": subject,
            "items": len(items),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error emailing PO: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── METAFIELD UPLOAD ────────────────────────────────────────────

from fastapi import File, UploadFile


@app.post("/api/metafields/upload")
async def upload_metafields(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Preview changes without applying"),
    title_col: Optional[str] = Query(None, description="Name of column to use as product title for display"),
    token: str = Depends(verify_token),
):
    """
    Upload a CSV or XLSX to bulk-apply metafields (cost_usd, system_code) to Shopify variants.
    File must have columns: SKU and at least one of Cost USD, System Code.
    Set dry_run=true to preview what would change without writing.
    SKUs not present in the file are left untouched (prior metafield values preserved).
    Blank cells for a particular metafield are skipped (prior value preserved for that field).
    """
    import csv
    import io

    try:
        content = await file.read()
        filename = (file.filename or "").lower()

        # Convert XLSX to CSV text via openpyxl; CSV/TXT handled directly
        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else str(c) for c in row])
            wb.close()
            text = output.getvalue()
        else:
            text = content.decode("utf-8-sig")  # Handle BOM from Excel

        reader = csv.DictReader(io.StringIO(text))

        # Normalize column names
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="Empty file")

        col_map = {}
        for col in reader.fieldnames:
            lower = col.strip().lower().replace(" ", "_")
            if lower in ("sku", "variant_sku"):
                col_map["sku"] = col
            elif lower in ("cost_usd", "cost_us", "usd_cost", "us_cost"):
                col_map["cost_usd"] = col
            elif lower in ("system_code", "systemcode", "sys_code"):
                col_map["system_code"] = col

        if "sku" not in col_map:
            raise HTTPException(
                status_code=400,
                detail=f"File must have a SKU column. Found columns: {reader.fieldnames}"
            )

        if "cost_usd" not in col_map and "system_code" not in col_map:
            raise HTTPException(
                status_code=400,
                detail="File must have at least one of: Cost USD, System Code"
            )

        # Validate optional title column
        title_col_actual = None
        if title_col and title_col.strip():
            # Find case-insensitive match in actual fieldnames
            for col in reader.fieldnames:
                if col.strip().lower() == title_col.strip().lower():
                    title_col_actual = col
                    break

        sku_data = []
        for row in reader:
            sku = row.get(col_map["sku"], "").strip()
            if not sku:
                continue
            item = {"sku": sku}
            if "cost_usd" in col_map:
                item["cost_usd"] = row.get(col_map["cost_usd"], "").strip()
            if "system_code" in col_map:
                item["system_code"] = row.get(col_map["system_code"], "").strip()
            if title_col_actual:
                item["csv_title"] = row.get(title_col_actual, "").strip()
            sku_data.append(item)

        if not sku_data:
            raise HTTPException(status_code=400, detail="No valid rows found in file")

        logger.info(f"Processing metafield upload for {len(sku_data)} SKUs (dry_run={dry_run})")

        if dry_run:
            # Look up SKUs in Shopify AND fetch current metafield values
            skus = [d["sku"] for d in sku_data]
            inventory_data = await shopify_client.lookup_inventory_and_metafields_by_skus(skus)

            results = []
            for item in sku_data:
                sku = item["sku"]
                found = sku in inventory_data
                current_mf = inventory_data.get(sku, {}).get("current_metafields", {}) if found else {}

                # Build a per-field diff showing prior and new values.
                # Blank cells are explicitly skipped (keep_prior=True) so the user sees
                # that the existing value will be preserved.
                metafield_changes = {}
                for key in ("cost_usd", "system_code"):
                    new_val = item.get(key, "")
                    prior_val = current_mf.get(key, "")
                    if new_val:
                        metafield_changes[key] = {
                            "prior": prior_val,
                            "new": new_val,
                            "changed": str(prior_val) != str(new_val),
                            "keep_prior": False,
                        }
                    elif prior_val:
                        # Cell blank in CSV but a prior value exists — it will be kept
                        metafield_changes[key] = {
                            "prior": prior_val,
                            "new": prior_val,
                            "changed": False,
                            "keep_prior": True,
                        }

                # For backward compatibility with the existing UI
                metafields_new_only = {k: v["new"] for k, v in metafield_changes.items() if not v["keep_prior"]}

                has_changes = any(v["changed"] for v in metafield_changes.values())
                action = "no_match" if not found else ("would_update" if has_changes else "no_change")

                results.append({
                    "sku": sku,
                    "found_in_shopify": found,
                    "success": found and has_changes,
                    "action": action,
                    "metafields": metafields_new_only,
                    "metafield_changes": metafield_changes,  # new — with prior values
                    "current_metafields": current_mf,         # new — raw current values
                    "error": "SKU not found in Shopify" if not found else "",
                    "shopify_title": inventory_data.get(sku, {}).get("title", ""),
                    "csv_title": item.get("csv_title", ""),
                })

            found_count = sum(1 for r in results if r["found_in_shopify"])
            not_found_count = sum(1 for r in results if not r["found_in_shopify"])
            would_update = sum(1 for r in results if r["action"] == "would_update")
            no_change = sum(1 for r in results if r["action"] == "no_change")

            return {
                "status": "ok",
                "dry_run": True,
                "total": len(sku_data),
                "found": found_count,
                "not_found": not_found_count,
                "would_update": would_update,
                "no_change": no_change,
                "results": results,
                "message": f"Dry run: {would_update} SKUs would be updated, {no_change} unchanged, {not_found_count} not found",
            }
        else:
            results = await shopify_client.bulk_set_metafields_by_sku(sku_data)

            success = sum(1 for r in results if r.get("success"))
            failed = sum(1 for r in results if not r.get("success"))
            not_found = sum(1 for r in results if r.get("error") == "SKU not found in Shopify")

            # Enrich results with status category
            # Build a lookup of sku → csv_title to enrich results
            csv_title_by_sku = {d["sku"]: d.get("csv_title", "") for d in sku_data}

            for r in results:
                if r.get("success"):
                    r["action"] = "updated"
                    r["found_in_shopify"] = True
                elif r.get("error") == "SKU not found in Shopify":
                    r["action"] = "no_match"
                    r["found_in_shopify"] = False
                else:
                    r["action"] = "error"
                    r["found_in_shopify"] = True
                r["csv_title"] = csv_title_by_sku.get(r.get("sku", ""), "")

            return {
                "status": "ok",
                "dry_run": False,
                "total": len(sku_data),
                "success": success,
                "failed": failed,
                "not_found": not_found,
                "results": results,
                "message": f"Updated {success} of {len(sku_data)} SKUs"
                    + (f", {not_found} not found" if not_found else "")
                    + (f", {failed - not_found} errors" if failed - not_found > 0 else ""),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading metafields: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── SKU CORRECTION ──────────────────────────────────────────────

@app.post("/api/skus/correct")
async def correct_skus(
    file: UploadFile = File(...),
    dry_run: bool = Query(True, description="Preview corrections without applying"),
    title_col: Optional[str] = Query(None, description="Name of column to use as product title for display"),
    token: str = Depends(verify_token),
):
    """
    Upload a CSV or XLSX with a SKU column. Finds SKUs that don't exactly match Shopify
    but have a fuzzy match (e.g., extra dash). Corrects the Shopify SKU to match.
    Optionally pulls a product title column from the file for display in the preview.
    """
    import csv
    import io

    try:
        content = await file.read()
        filename = (file.filename or "").lower()

        # Convert XLSX to CSV text
        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else str(c) for c in row])
            wb.close()
            text = output.getvalue()
        else:
            text = content.decode("utf-8-sig")

        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="Empty file")

        # Find SKU column
        sku_col = None
        for col in reader.fieldnames:
            if col.strip().lower().replace(" ", "_") in ("sku", "variant_sku"):
                sku_col = col
                break

        if not sku_col:
            raise HTTPException(status_code=400, detail=f"File must have a SKU column. Found: {reader.fieldnames}")

        # Validate optional title column
        title_col_actual = None
        if title_col and title_col.strip():
            for col in reader.fieldnames:
                if col.strip().lower() == title_col.strip().lower():
                    title_col_actual = col
                    break

        skus = []
        csv_title_by_sku = {}  # sku → title from user's spreadsheet
        for row in reader:
            sku = row.get(sku_col, "").strip()
            if sku:
                skus.append(sku)
                if title_col_actual:
                    csv_title_by_sku[sku] = row.get(title_col_actual, "").strip()

        if not skus:
            raise HTTPException(status_code=400, detail="No SKUs found in file")

        logger.info(f"SKU correction: checking {len(skus)} SKUs (dry_run={dry_run})")

        # Find mismatches
        match_results = await shopify_client.find_sku_mismatches(skus)

        # Apply corrections if not dry run
        if not dry_run:
            for r in match_results:
                if r["needs_correction"] and r["inventory_item_id"]:
                    try:
                        await shopify_client.update_variant_sku(r["inventory_item_id"], r["csv_sku"])
                        r["corrected"] = True
                        r["action"] = "corrected"
                    except Exception as e:
                        r["corrected"] = False
                        r["action"] = "error"
                        r["error"] = str(e)
                    await asyncio.sleep(0.2)
                else:
                    r["corrected"] = False
                    r["action"] = r["match_type"]
        else:
            for r in match_results:
                r["corrected"] = False
                if r["needs_correction"]:
                    r["action"] = "would_correct"
                elif r["match_type"] == "exact":
                    r["action"] = "exact"
                else:
                    r["action"] = "not_found"

        # Enrich with csv_title
        for r in match_results:
            r["csv_title"] = csv_title_by_sku.get(r.get("csv_sku", ""), "")

        exact = sum(1 for r in match_results if r["match_type"] == "exact")
        fuzzy = sum(1 for r in match_results if r["needs_correction"])
        not_found = sum(1 for r in match_results if r["match_type"] == "not_found")
        corrected = sum(1 for r in match_results if r.get("corrected"))

        if dry_run:
            msg = f"Dry run: {exact} exact matches, {fuzzy} would be corrected, {not_found} not found"
        else:
            msg = f"Corrected {corrected} SKUs, {exact} already correct, {not_found} not found"

        return {
            "status": "ok",
            "dry_run": dry_run,
            "total": len(skus),
            "exact_matches": exact,
            "needs_correction": fuzzy,
            "not_found": not_found,
            "corrected": corrected,
            "results": match_results,
            "message": msg,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SKU correction error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── ANALYTICS ───────────────────────────────────────────────────

from .analytics import (
    compute_brand_scorecards,
    compute_sku_details,
    compute_inventory_summary,
    compute_action_lists,
    compute_purchase_history,
    compute_vendor_purchasing_analysis,
    capture_daily_snapshot,
    get_inventory_trend,
)


@app.get("/api/analytics/summary")
async def analytics_summary(token: str = Depends(verify_token)):
    """High-level inventory health summary."""
    try:
        return compute_inventory_summary(db)
    except Exception as e:
        logger.error(f"Analytics summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/brands")
async def analytics_brands(token: str = Depends(verify_token)):
    """Brand scorecards sorted by inventory value."""
    try:
        return compute_brand_scorecards(db)
    except Exception as e:
        logger.error(f"Brand scorecards error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/skus")
async def analytics_skus(
    vendor: Optional[str] = None,
    aging: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """SKU-level detail with aging and action recommendations."""
    try:
        return compute_sku_details(db, vendor=vendor, aging_bucket=aging)
    except Exception as e:
        logger.error(f"SKU details error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/actions")
async def analytics_actions(token: str = Depends(verify_token)):
    """Prioritized action lists: stop buying, liquidate, increase buying."""
    try:
        result = compute_action_lists(db)
        # Enrich every list with waiter_count + demand_boost from the latest snapshot
        from .waiters import waiter_counts_map, compute_demand_boost
        waiter_map = waiter_counts_map(db)
        if waiter_map:
            for list_name, items in result.items():
                if isinstance(items, list):
                    for item in items:
                        sku = (item.get("sku") or "").upper()
                        wc = waiter_map.get(sku, 0)
                        item["waiter_count"] = wc
                        item["demand_boost"] = compute_demand_boost(wc) if wc else 0
        return result
    except Exception as e:
        logger.error(f"Action lists error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/purchases")
async def analytics_purchases(
    vendor: Optional[str] = None,
    months: int = Query(default=12, ge=1, le=36),
    token: str = Depends(verify_token),
):
    """Purchase order history by vendor."""
    try:
        return compute_purchase_history(db, vendor=vendor, months=months)
    except Exception as e:
        logger.error(f"Purchase history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/vendor-purchasing")
async def analytics_vendor_purchasing(
    months: int = Query(default=12, ge=1, le=24),
    token: str = Depends(verify_token),
):
    """Vendor purchasing analysis: spend vs inventory health."""
    try:
        return compute_vendor_purchasing_analysis(db, months=months)
    except Exception as e:
        logger.error(f"Vendor purchasing analysis error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analytics/snapshot")
async def analytics_capture_snapshot(token: str = Depends(verify_token)):
    """Capture today's inventory snapshot (idempotent — safe to call multiple times)."""
    try:
        return capture_daily_snapshot(db)
    except Exception as e:
        logger.error(f"Snapshot capture error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/trend")
async def analytics_trend(
    days: int = Query(default=90, ge=7, le=365),
    token: str = Depends(verify_token),
):
    """Inventory value trend over time."""
    try:
        return get_inventory_trend(db, days=days)
    except Exception as e:
        logger.error(f"Trend retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── MARKET INTELLIGENCE ─────────────────────────────────────────

from .market_intel import (
    get_products_for_comparison,
    get_competitor_prices,
    upsert_competitor_price,
    get_price_comparison_summary,
    delete_competitor_price,
    COMPETITORS,
)


@app.get("/api/market/products")
async def market_products(
    vendor: Optional[str] = None,
    limit: int = Query(default=100, ge=10, le=500),
    token: str = Depends(verify_token),
):
    """Get top products for competitor comparison."""
    try:
        return get_products_for_comparison(db, vendor=vendor, limit=limit)
    except Exception as e:
        logger.error(f"Market products error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market/competitors")
async def market_competitors(token: str = Depends(verify_token)):
    """Get list of tracked competitors."""
    return COMPETITORS


@app.get("/api/market/prices")
async def market_prices(
    sku: Optional[str] = None,
    competitor: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """Get stored competitor prices."""
    try:
        return get_competitor_prices(db, sku=sku, competitor=competitor)
    except Exception as e:
        logger.error(f"Competitor prices error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class CompetitorPriceEntry(BaseModel):
    sku: str
    product_title: str = ""
    competitor: str
    competitor_price: Optional[float] = None
    competitor_url: Optional[str] = None
    competitor_in_stock: Optional[bool] = None
    our_price: float = 0
    notes: Optional[str] = None


@app.post("/api/market/prices")
async def market_upsert_price(
    entry: CompetitorPriceEntry,
    token: str = Depends(verify_token),
):
    """Add or update a competitor price."""
    try:
        return upsert_competitor_price(db, entry.dict())
    except Exception as e:
        logger.error(f"Upsert competitor price error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/market/prices/{entry_id}")
async def market_delete_price(
    entry_id: int,
    token: str = Depends(verify_token),
):
    """Delete a competitor price entry."""
    try:
        return delete_competitor_price(db, entry_id)
    except Exception as e:
        logger.error(f"Delete competitor price error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market/summary")
async def market_summary(token: str = Depends(verify_token)):
    """Price comparison summary across all tracked products."""
    try:
        return get_price_comparison_summary(db)
    except Exception as e:
        logger.error(f"Market summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── SINGLE SKU COST UPDATE ─────────────────────────────────────

class SingleCostUpdateRequest(BaseModel):
    sku: str
    cost: float
    variant_id: Optional[str] = None


class SkuUpdateRequest(BaseModel):
    product_id: str
    variant_id: str
    new_sku: str
    old_sku: Optional[str] = None  # for cache update fallback


@app.post("/api/products/update-variant-sku")
async def products_update_variant_sku(
    data: SkuUpdateRequest, token: str = Depends(verify_token),
):
    """Rename a variant's SKU in Shopify and sync the velocity cache.

    Used by the pricelist comparison UI when a Shopify SKU was matched to
    a vendor pricelist via the vendor-name prefix bridge — letting the user
    drop the prefix in one click so future matches are exact."""
    new_sku = (data.new_sku or "").strip()
    if not new_sku:
        raise HTTPException(400, "new_sku is required")
    try:
        result = await shopify_client.update_variant_sku(
            data.product_id, data.variant_id, new_sku
        )
        applied_sku = result.get("sku") or new_sku

        # Sync the velocity cache. The PK is the SKU itself, so an UPDATE
        # WHERE variant_id = ? rewrites the row in place. Watch for collision
        # with an existing row sharing the new SKU (unlikely but possible).
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM product_velocity_cache WHERE sku = ? AND variant_id <> ?",
                applied_sku, data.variant_id,
            )
            collision = cursor.fetchone() is not None
            if collision:
                logger.warning(
                    "Velocity cache already has a row with sku=%s under a different variant — skipping rewrite. "
                    "Run a full refresh to reconcile.", applied_sku,
                )
            else:
                cursor.execute(
                    "UPDATE product_velocity_cache SET sku = ? WHERE variant_id = ?",
                    applied_sku, data.variant_id,
                )
                conn.commit()
        finally:
            conn.close()

        return {
            "status": "ok",
            "variant_id": data.variant_id,
            "old_sku": data.old_sku,
            "new_sku": applied_sku,
            "cache_synced": not collision,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"SKU update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/products/update-cost")
async def products_update_single_cost(
    data: SingleCostUpdateRequest, token: str = Depends(verify_token),
):
    """Update cost for a single SKU in Shopify and the velocity cache."""
    try:
        # Look up inventory_item_id
        inv_data = await shopify_client.lookup_inventory_by_skus([data.sku])
        inv_info = inv_data.get(data.sku)
        if not inv_info or not inv_info.get('inventory_item_id'):
            raise HTTPException(400, f"Could not find inventory item for SKU {data.sku}")

        await shopify_client.update_unit_cost(inv_info['inventory_item_id'], data.cost, 'CAD')

        # Update velocity cache
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE product_velocity_cache SET cost = ? WHERE sku = ?", data.cost, data.sku)
            conn.commit()
        finally:
            conn.close()

        return {"status": "ok", "sku": data.sku, "new_cost": data.cost}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Single cost update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── BULK COST UPDATE ────────────────────────────────────────────

class BulkCostUpdateRequest(BaseModel):
    vendor: str
    fx_rate: float
    items: List[dict]  # [{sku, cost_foreign, cost_cad}]


@app.post("/api/vendors/{vendor}/update-costs")
async def vendors_bulk_update_costs(
    vendor: str,
    token: str = Depends(verify_token),
):
    """Update Shopify costs from stored pricelist using FX rate."""
    try:
        # Get vendor settings — use pricelist_cost_currency (the currency the costs
        # were uploaded in) rather than invoice_currency (the vendor's billing currency)
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT invoice_currency, pricelist_cost_currency
                FROM vendor_settings WHERE vendor = ?
            """, vendor)
            row = cursor.fetchone()
            # Use pricelist cost currency if set, otherwise fall back to invoice currency
            cost_currency = (row[1] if row and row[1] else (row[0] if row else 'USD')).upper()

            # Get FX rate for the cost currency
            fx_rate = 1.0
            if cost_currency != 'CAD':
                pair = cost_currency + 'CAD'
                cursor.execute("SELECT effective_rate FROM fx_rates WHERE currency_pair = ?", pair)
                fx_row = cursor.fetchone()
                if fx_row and fx_row[0]:
                    fx_rate = float(fx_row[0])
                else:
                    raise HTTPException(status_code=400, detail=f"No FX rate stored for {pair}. Refresh the rate first.")

            # Get matched pricelist items. Prefer the resolved Shopify SKU
            # stored on the pricelist row (``matched_shopify_sku``) so items
            # bridged via the vendor-prefix or title matcher (e.g. pricelist
            # 'EXOS2CWB' → Shopify 'EXOS2CWB5') are still picked up here.
            # Falls back to the supplier SKU itself when no resolved value
            # was recorded — matches the original behavior for legacy rows.
            cursor.execute("""
                SELECT vpi.supplier_sku, vpi.supplier_cost,
                       pvc.sku, pvc.cost, pvc.variant_id
                FROM vendor_pricelist_items vpi
                INNER JOIN product_velocity_cache pvc
                    ON UPPER(pvc.sku) = UPPER(COALESCE(vpi.matched_shopify_sku, vpi.supplier_sku))
                   AND pvc.vendor = vpi.vendor
                WHERE vpi.vendor = ? AND vpi.match_status = 'matched'
            """, vendor)

            # Two pricelist rows can resolve to the same Shopify variant when
            # they share a saved SKU mapping (e.g. supplier 'EAF-...R11v2' and
            # 'EAF-...R11v2-Q' both pointing at the same Shopify SKU). Without
            # dedup the bulk update writes both costs to the same variant in
            # whatever order the JOIN returned, then the next compare flags
            # the loser as a "change" → cost oscillates between values. Pick
            # the row whose supplier_sku matches the Shopify SKU exactly
            # (case-insensitive) — that's the right pricelist row for that
            # variant. Skip the rest with a warning.
            by_variant = {}
            collisions = []
            no_variant = []
            for row in cursor.fetchall():
                supplier_sku = row[0]
                supplier_cost = float(row[1]) if row[1] else 0
                shopify_sku = row[2]
                current_cost = float(row[3]) if row[3] else 0
                variant_id = row[4]
                if not variant_id:
                    # No variant id cached, so there is nothing to update.
                    # Only worth reporting when the cost actually differs —
                    # an invisible skip on a real change is exactly why costs
                    # looked "identified but not applied".
                    if abs(round(supplier_cost * fx_rate, 2) - current_cost) > 0.50:
                        no_variant.append(shopify_sku)
                    continue
                is_exact = (supplier_sku or '').upper() == (shopify_sku or '').upper()
                existing = by_variant.get(variant_id)
                if existing is None:
                    by_variant[variant_id] = (is_exact, supplier_sku, supplier_cost,
                                              shopify_sku, current_cost)
                else:
                    # Prefer the exact-match row; if neither is exact, keep
                    # the first and log the collision.
                    if is_exact and not existing[0]:
                        collisions.append({
                            'shopify_sku': shopify_sku,
                            'kept': supplier_sku,
                            'dropped': existing[1],
                        })
                        by_variant[variant_id] = (is_exact, supplier_sku, supplier_cost,
                                                  shopify_sku, current_cost)
                    else:
                        collisions.append({
                            'shopify_sku': shopify_sku,
                            'kept': existing[1],
                            'dropped': supplier_sku,
                        })

            if collisions:
                logger.warning(
                    "Bulk cost update collision (multiple pricelist rows → same "
                    "Shopify variant): %s", collisions,
                )

            items_to_update = []
            for variant_id, (_, supplier_sku, supplier_cost, shopify_sku, current_cost) in by_variant.items():
                cost_cad = round(supplier_cost * fx_rate, 2)
                if abs(cost_cad - current_cost) > 0.50:
                    items_to_update.append({
                        'sku': shopify_sku,
                        'supplier_cost': supplier_cost,
                        'cost_cad': cost_cad,
                        'current_cost': current_cost,
                        'variant_id': variant_id,
                    })
        finally:
            conn.close()

        if not items_to_update:
            if no_variant:
                return {
                    'status': 'ok', 'updated': 0, 'total': len(no_variant),
                    'results': [
                        {'sku': sku, 'status': 'skipped',
                         'message': 'No Shopify variant cached — refresh inventory'}
                        for sku in no_variant
                    ],
                }
            return {'status': 'ok', 'updated': 0, 'total': 0, 'message': 'No cost changes to apply (all costs within $0.50 of pricelist)'}

        # Look up inventory_item_ids for each variant
        skus = [item['sku'] for item in items_to_update]
        inventory_data = await shopify_client.lookup_inventory_by_skus(skus)

        results = [
            {'sku': sku, 'status': 'skipped',
             'message': 'No Shopify variant cached — refresh inventory'}
            for sku in no_variant
        ]
        updated = 0
        for item in items_to_update:
            inv_info = inventory_data.get(item['sku'])
            if not inv_info or not inv_info.get('inventory_item_id'):
                results.append({'sku': item['sku'], 'status': 'skipped', 'message': 'No inventory item found'})
                continue
            try:
                await shopify_client.update_unit_cost(inv_info['inventory_item_id'], item['cost_cad'], 'CAD')
                # Update velocity cache too
                conn2 = db._get_connection()
                try:
                    c2 = conn2.cursor()
                    c2.execute("UPDATE product_velocity_cache SET cost = ? WHERE sku = ?", item['cost_cad'], item['sku'])
                    conn2.commit()
                finally:
                    conn2.close()
                results.append({'sku': item['sku'], 'status': 'ok', 'old_cost': item['current_cost'], 'new_cost': item['cost_cad']})
                updated += 1
            except Exception as e:
                results.append({'sku': item['sku'], 'status': 'error', 'message': str(e)})

        return {
            'status': 'ok',
            'updated': updated,
            'total': len(items_to_update) + len(no_variant),
            'fx_rate': fx_rate,
            'currency': cost_currency,
            'results': results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk cost update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/update-prices")
async def vendors_bulk_update_prices(
    vendor: str,
    token: str = Depends(verify_token),
):
    """Update Shopify selling prices from stored pricelist MAP/MSRP values."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT invoice_currency FROM vendor_settings WHERE vendor = ?", vendor)
            row = cursor.fetchone()
            currency = row[0] if row else 'USD'

            # Get FX rates
            fx_rates = {'CAD': 1.0}
            for curr in ['USD', 'EUR']:
                pair = curr + 'CAD'
                cursor.execute("SELECT effective_rate FROM fx_rates WHERE currency_pair = ?", pair)
                fx_row = cursor.fetchone()
                if fx_row and fx_row[0]:
                    fx_rates[curr] = float(fx_row[0])

            # Get matched pricelist items with sale prices, excluding "On Sale" tagged.
            # Same COALESCE fix as bulk-update-costs: prefer the matcher's resolved
            # Shopify SKU so prefix/title-bridged items are picked up.
            cursor.execute("""
                SELECT vpi.supplier_sku, vpi.supplier_msrp,
                       pvc.sku, pvc.price, pvc.variant_id, pvc.tags, pvc.product_id
                FROM vendor_pricelist_items vpi
                INNER JOIN product_velocity_cache pvc
                    ON UPPER(pvc.sku) = UPPER(COALESCE(vpi.matched_shopify_sku, vpi.supplier_sku))
                   AND pvc.vendor = vpi.vendor
                WHERE vpi.vendor = ? AND vpi.match_status = 'matched'
                    AND vpi.supplier_msrp IS NOT NULL AND vpi.supplier_msrp > 0
            """, vendor)

            # Dedupe by Shopify variant — same anti-oscillation logic as
            # bulk-update-costs above.
            by_variant = {}
            for row in cursor.fetchall():
                supplier_sku = row[0]
                supplier_msrp = row[1]
                shopify_sku = row[2]
                current_price = float(row[3]) if row[3] else 0
                variant_id = row[4]
                tags = (row[5] or '').lower()
                product_id = row[6]
                if not variant_id or not product_id:
                    continue
                if 'on sale' in tags:
                    continue
                is_exact = (supplier_sku or '').upper() == (shopify_sku or '').upper()
                existing = by_variant.get(variant_id)
                if existing is None or (is_exact and not existing[0]):
                    by_variant[variant_id] = (is_exact, supplier_sku, supplier_msrp,
                                              shopify_sku, current_price, product_id)

            items_to_update = []
            for variant_id, (_, _supplier_sku, supplier_msrp, shopify_sku, current_price, product_id) in by_variant.items():
                sale_price_cad = round(float(supplier_msrp))  # already stored as CAD in supplier_msrp
                if abs(sale_price_cad - current_price) > 0.50:
                    items_to_update.append({
                        'sku': shopify_sku,
                        'new_price': sale_price_cad,
                        'current_price': current_price,
                        'variant_id': variant_id,
                        'product_id': product_id,
                    })
        finally:
            conn.close()

        if not items_to_update:
            return {'status': 'ok', 'updated': 0, 'message': 'No price changes to apply'}

        results = []
        updated = 0
        for item in items_to_update:
            try:
                await shopify_client.update_variant_price(item['product_id'], item['variant_id'], item['new_price'])
                # Update velocity cache
                conn2 = db._get_connection()
                try:
                    c2 = conn2.cursor()
                    c2.execute("UPDATE product_velocity_cache SET price = ? WHERE sku = ?",
                               item['new_price'], item['sku'])
                    conn2.commit()
                finally:
                    conn2.close()
                results.append({'sku': item['sku'], 'status': 'ok',
                               'old_price': item['current_price'], 'new_price': item['new_price']})
                updated += 1
            except Exception as e:
                results.append({'sku': item['sku'], 'status': 'error', 'message': str(e)})

        return {
            'status': 'ok',
            'updated': updated,
            'total': len(items_to_update),
            'results': results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk price update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/update-country-of-origin")
async def vendors_bulk_update_coo(
    vendor: str,
    token: str = Depends(verify_token),
):
    """Set Shopify's inventory-item Country of Origin from stored pricelist COO.

    For every matched pricelist row that carries a COO code, look up the
    variant's inventory item and its current countryCodeOfOrigin, then push
    the pricelist code where it differs. Items already correct are skipped;
    invalid/unresolvable codes were filtered at upload time (stored as NULL),
    so anything here is already a 2-letter code.
    """
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            # Matched pricelist rows with a COO, joined to the live variant.
            # Prefer the resolved Shopify SKU (matched_shopify_sku) like the
            # cost/price pushers do, so prefix/title-bridged rows are included.
            cursor.execute("""
                SELECT pvc.sku, vpi.supplier_coo
                FROM vendor_pricelist_items vpi
                INNER JOIN product_velocity_cache pvc
                    ON UPPER(pvc.sku) = UPPER(COALESCE(vpi.matched_shopify_sku, vpi.supplier_sku))
                   AND pvc.vendor = vpi.vendor
                WHERE vpi.vendor = ? AND vpi.match_status = 'matched'
                  AND vpi.supplier_coo IS NOT NULL AND vpi.supplier_coo <> ''
            """, vendor)
            # One COO per SKU (dedup; last write wins — same code expected anyway).
            coo_by_sku = {}
            for row in cursor.fetchall():
                if row[0] and row[1]:
                    coo_by_sku[row[0]] = row[1].strip().upper()
        finally:
            conn.close()

        if not coo_by_sku:
            return {'status': 'ok', 'updated': 0, 'total': 0,
                    'message': 'No matched items have a Country of Origin in the pricelist.'}

        # Resolve inventory item ids + current COO so we only write changes.
        skus = list(coo_by_sku.keys())
        inventory_data = await shopify_client.lookup_inventory_by_skus(skus)

        results = []
        updated = 0
        skipped_unchanged = 0
        for sku, target_coo in coo_by_sku.items():
            inv_info = inventory_data.get(sku)
            if not inv_info or not inv_info.get('inventory_item_id'):
                results.append({'sku': sku, 'status': 'skipped', 'message': 'No inventory item found'})
                continue
            current = (inv_info.get('country_code_of_origin') or '').upper()
            if current == target_coo:
                skipped_unchanged += 1
                continue
            try:
                await shopify_client.update_country_of_origin(
                    inv_info['inventory_item_id'], target_coo)
                results.append({'sku': sku, 'status': 'ok',
                                'old_coo': current or None, 'new_coo': target_coo})
                updated += 1
            except Exception as e:
                results.append({'sku': sku, 'status': 'error', 'message': str(e)})

        return {
            'status': 'ok',
            'updated': updated,
            'total': len(coo_by_sku),
            'skipped_unchanged': skipped_unchanged,
            'results': results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk COO update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class BulkStatusRequest(BaseModel):
    product_ids: List[str]
    status: str = "DRAFT"


class BulkInventoryPolicyRequest(BaseModel):
    items: List[dict]  # [{product_id, variant_id}]
    policy: str = "DENY"


@app.post("/api/products/set-inventory-policy")
async def products_set_inventory_policy(data: BulkInventoryPolicyRequest, token: str = Depends(verify_token)):
    """Set inventory policy (DENY/CONTINUE) on multiple variants."""
    try:
        results = []
        for item in data.items:
            try:
                await shopify_client.set_inventory_policy(
                    item['product_id'], item['variant_id'], data.policy
                )
                results.append({"sku": item.get('sku', ''), "status": "ok"})
            except Exception as e:
                results.append({"sku": item.get('sku', ''), "status": "error", "message": str(e)})

        ok_count = sum(1 for r in results if r["status"] == "ok")
        return {"status": "ok", "updated": ok_count, "total": len(data.items), "results": results}
    except Exception as e:
        logger.error(f"Bulk inventory policy error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/products/set-status")
async def products_set_status(data: BulkStatusRequest, token: str = Depends(verify_token)):
    """Set status (DRAFT, ACTIVE, ARCHIVED) on multiple products."""
    try:
        results = []
        for pid in data.product_ids:
            try:
                await shopify_client.set_product_status(pid, data.status)
                results.append({"product_id": pid, "status": "ok"})
            except Exception as e:
                results.append({"product_id": pid, "status": "error", "message": str(e)})

        ok_count = sum(1 for r in results if r["status"] == "ok")
        return {"status": "ok", "updated": ok_count, "total": len(data.product_ids), "results": results}
    except Exception as e:
        logger.error(f"Bulk status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── PRODUCT TAGGING ─────────────────────────────────────────────

class BulkTagRequest(BaseModel):
    product_ids: List[str]
    tag: str


@app.post("/api/products/add-tag")
async def products_add_tag(data: BulkTagRequest, token: str = Depends(verify_token)):
    """Add a tag to multiple products in Shopify and update the velocity cache."""
    try:
        results = []
        for pid in data.product_ids:
            try:
                await shopify_client.add_tags_to_product(pid, [data.tag])
                results.append({"product_id": pid, "status": "ok"})
            except Exception as e:
                results.append({"product_id": pid, "status": "error", "message": str(e)})

        # Update the velocity cache tags for these products
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            for pid in data.product_ids:
                cursor.execute("""
                    UPDATE product_velocity_cache
                    SET tags = CASE
                        WHEN tags IS NULL OR tags = '' THEN ?
                        WHEN tags NOT LIKE '%' + ? + '%' THEN tags + ',' + ?
                        ELSE tags
                    END
                    WHERE product_id = ?
                """, data.tag, data.tag, data.tag, pid)
            conn.commit()
        finally:
            conn.close()

        ok_count = sum(1 for r in results if r["status"] == "ok")
        return {"status": "ok", "tagged": ok_count, "total": len(data.product_ids), "results": results}
    except Exception as e:
        logger.error(f"Bulk tag error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/products/remove-tag")
async def products_remove_tag(data: BulkTagRequest, token: str = Depends(verify_token)):
    """Remove a tag from multiple products in Shopify and update the velocity cache."""
    try:
        results = []
        for pid in data.product_ids:
            try:
                await shopify_client.remove_tags_from_product(pid, [data.tag])
                results.append({"product_id": pid, "status": "ok"})
            except Exception as e:
                results.append({"product_id": pid, "status": "error", "message": str(e)})

        # Update the velocity cache
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            for pid in data.product_ids:
                cursor.execute("""
                    UPDATE product_velocity_cache
                    SET tags = REPLACE(REPLACE(tags, ? + ',', ''), ',' + ?, '')
                    WHERE product_id = ?
                """, data.tag, data.tag, pid)
                # Handle case where tag is the only one
                cursor.execute("""
                    UPDATE product_velocity_cache
                    SET tags = REPLACE(tags, ?, '')
                    WHERE product_id = ? AND tags = ?
                """, data.tag, pid, data.tag)
            conn.commit()
        finally:
            conn.close()

        ok_count = sum(1 for r in results if r["status"] == "ok")
        return {"status": "ok", "removed": ok_count, "total": len(data.product_ids), "results": results}
    except Exception as e:
        logger.error(f"Bulk remove tag error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── VENDOR MANAGEMENT ──────────────────────────────────────────

from .vendor_mgmt import (
    get_all_vendor_settings,
    upsert_vendor_settings,
    record_stock_check,
    fetch_fx_rate,
    get_fx_rate,
    save_fx_rate,
    process_pricelist_upload,
    get_pricelist_items,
    get_stored_pricelist_csv,
    recompare_stored_pricelist,
)


@app.get("/api/vendors")
async def vendors_list(token: str = Depends(verify_token)):
    """Get all vendor settings."""
    try:
        return get_all_vendor_settings(db)
    except Exception as e:
        logger.error(f"Vendor list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class VendorSettingsUpdate(BaseModel):
    vendor: str
    lead_time_days: int = 14
    min_order_value: Optional[float] = None
    notes: Optional[str] = None
    invoice_currency: str = "CAD"
    enforces_map: bool = False
    default_markup_pct: Optional[float] = None
    website_url: Optional[str] = None
    pricelist_release_date: Optional[str] = None  # YYYY-MM-DD
    requires_barcode_labels: bool = False
    # Reminder popped up when a PO is created for this vendor. Editing the
    # text re-arms it (po_reminder_active defaults True).
    po_reminder: Optional[str] = None
    po_reminder_active: bool = True


@app.post("/api/vendors")
async def vendors_upsert(data: VendorSettingsUpdate, token: str = Depends(verify_token)):
    """Create or update vendor settings."""
    try:
        return upsert_vendor_settings(db, data.dict())
    except Exception as e:
        logger.error(f"Vendor upsert error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class StockCheckRequest(BaseModel):
    """All fields optional: the common case is a one-click 'checked today',
    which stamps today's date and the caller's own name."""
    date: Optional[str] = None   # YYYY-MM-DD; defaults to today (UTC)
    by: Optional[str] = None     # defaults to the signed-in user
    notes: Optional[str] = None
    clear: bool = False          # reset to "never counted" (undo a mis-click)


@app.post("/api/vendors/{vendor}/stock-check")
async def vendors_record_stock_check(
    vendor: str,
    data: StockCheckRequest = StockCheckRequest(),
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """Record a physical stock count for this vendor.

    Replaces the hand-maintained "Inventory Check" tab of the ops Google
    Sheet. `by` falls back to the caller's token identity so the count is
    always attributable.
    """
    try:
        return record_stock_check(
            db, vendor,
            check_date=data.date,
            by=(data.by or (user if user != "Unknown" else None)),
            notes=data.notes,
            clear=data.clear,
        )
    except Exception as e:
        logger.error(f"Stock check error for {vendor}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fx-rate")
async def fx_rate_get(pair: str = "USDCAD", token: str = Depends(verify_token)):
    """Get stored FX rate and offset."""
    try:
        return get_fx_rate(db, pair)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FXRateUpdate(BaseModel):
    pair: str = "USDCAD"
    offset_pct: float = 0


@app.post("/api/fx-rate/refresh")
async def fx_rate_refresh(data: FXRateUpdate, token: str = Depends(verify_token)):
    """Fetch live FX rate and save with offset."""
    try:
        rate = await fetch_fx_rate(data.pair)
        if rate <= 0:
            raise HTTPException(status_code=502, detail="Could not fetch FX rate")
        result = save_fx_rate(db, data.pair, rate, data.offset_pct)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FXOffsetUpdate(BaseModel):
    pair: str = "USDCAD"
    offset_pct: float = 0


@app.post("/api/fx-rate/offset")
async def fx_rate_set_offset(data: FXOffsetUpdate, token: str = Depends(verify_token)):
    """Update the FX offset without re-fetching the rate."""
    try:
        stored = get_fx_rate(db, data.pair)
        if stored['rate'] <= 0:
            raise HTTPException(status_code=400, detail="No rate stored yet — refresh first")
        result = save_fx_rate(db, data.pair, stored['rate'], data.offset_pct)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _decode_csv_bytes(raw: bytes) -> str:
    """Decode a CSV/TSV byte payload, trying common encodings in order.

    Excel-exported CSVs often arrive as Windows-1252 (cp1252) rather than
    UTF-8 — that's where the 'utf-8' codec can't decode byte 0x91 errors
    come from (0x91 is the cp1252 left curly quote). Try UTF-8 with BOM
    first, fall back to cp1252 (covers >99% of Windows Excel exports), and
    finally Latin-1 with replacement so we never crash on a malformed file."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _xlsx_to_headers_and_csv(raw: bytes):
    """Load an XLSX payload and return ``(headers, csv_content)``.

    Vendor pricelists frequently put a title/banner block above the real
    column headers — a logo cell, a "Confidential ... 2026" line, an issue
    date, a reference FX rate, etc. (the Alpine/Baader Canada list has five
    such rows). So we can't assume the header is row 1: we scan the first
    rows and pick the one with the most non-empty *text* cells (banner rows
    have at most one or two), then emit a CSV that starts at that row.

    Because both the column-detection step and the parse step (csv.DictReader,
    which treats its first row as the header) go through this one function,
    the headers offered in the UI are guaranteed to match the CSV's header
    row exactly. Header cells have internal whitespace/newlines collapsed to
    single spaces so a multi-line header survives the round-trip through an
    HTML dropdown value and back as a query param.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Find the header row: the row (within the first 25) with the most
    # non-empty string cells. Numbers/dates aren't headers, so they don't
    # count — that's what separates the real header from banner/data rows.
    SCAN = 25
    best_idx, best_score = 0, 0
    for i, row in enumerate(rows[:SCAN]):
        score = sum(1 for v in row if isinstance(v, str) and v.strip())
        if score > best_score:
            best_score, best_idx = score, i
    header_idx = best_idx if best_score >= 2 else 0

    def _norm_header(v):
        # Collapse all runs of whitespace (incl. embedded newlines) to one space.
        return " ".join(str(v).split()) if v is not None else ""

    header_row = rows[header_idx] if header_idx < len(rows) else ()
    headers = [_norm_header(v) for v in header_row]
    headers = [h for h in headers if h]

    output = io.StringIO()
    writer = csv.writer(output)
    # First emitted row = normalized headers (matches `headers` above);
    # subsequent rows are the data cells, lightly stripped.
    writer.writerow([_norm_header(v) for v in header_row])
    for row in rows[header_idx + 1:]:
        writer.writerow([str(c).strip() if c is not None else "" for c in row])
    return headers, output.getvalue()


def _extract_pdf_table(raw_bytes):
    """Extract the largest table from a PDF using pdfplumber. Returns (headers, csv_content)."""
    import pdfplumber
    pdf = pdfplumber.open(io.BytesIO(raw_bytes))
    all_rows = []
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                # Clean cells
                cleaned = [str(cell).strip() if cell else "" for cell in row]
                if any(c for c in cleaned):
                    all_rows.append(cleaned)
    pdf.close()

    if not all_rows:
        raise ValueError("No tables found in PDF. The invoice may need to be converted to CSV manually.")

    # First row with mostly non-empty cells is likely the header
    headers = all_rows[0]
    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    for row in all_rows:
        # Pad or truncate to header length
        padded = row[:len(headers)] + [''] * max(0, len(headers) - len(row))
        writer.writerow(padded)
    return headers, output.getvalue()


@app.post("/api/vendors/pricelist/detect-columns")
async def vendors_detect_columns(
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """Detect column names from a CSV, XLSX, or PDF file. Returns column list."""
    try:
        raw = await file.read()
        filename = file.filename or ""

        if filename.lower().endswith('.pdf'):
            headers, _ = _extract_pdf_table(raw)
            headers = [h for h in headers if h]
        elif filename.lower().endswith(('.xlsx', '.xls')):
            headers, _ = _xlsx_to_headers_and_csv(raw)
        else:
            text = _decode_csv_bytes(raw)
            first_line = text.split("\n")[0]
            delim = "\t" if "\t" in first_line else ","
            headers = [h.strip().strip('"') for h in first_line.split(delim) if h.strip()]

        return {"columns": headers, "filename": filename}
    except Exception as e:
        logger.error(f"Column detection error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/pricelist")
async def vendors_upload_pricelist(
    vendor: str,
    sku_column: str = Query(...),
    cost_column: str = Query(...),
    description_column: Optional[str] = None,
    msrp_column: Optional[str] = None,
    cost_currency: str = Query(default="USD"),
    msrp_currency: str = Query(default="CAD"),
    fallback_msrp_column: Optional[str] = None,
    fallback_msrp_currency: str = Query(default="USD"),
    sku_secondary_column: Optional[str] = None,
    sku_separator: str = Query(default=" "),
    barcode_column: Optional[str] = None,
    map_cad_column: Optional[str] = None,
    coo_column: Optional[str] = None,
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """Upload a vendor pricelist (CSV, XLSX, or PDF) and compare against Shopify."""
    try:
        raw = await file.read()
        filename = file.filename or ""

        # Convert to CSV based on file type. The xlsx path uses the shared
        # header-aware helper so a vendor banner above the real headers (and
        # the matching column-detection step) stay consistent.
        if filename.lower().endswith('.pdf'):
            _, content = _extract_pdf_table(raw)
        elif filename.lower().endswith(('.xlsx', '.xls')):
            _, content = _xlsx_to_headers_and_csv(raw)
        else:
            content = _decode_csv_bytes(raw)

        result = process_pricelist_upload(
            db, vendor, content,
            sku_column=sku_column,
            cost_column=cost_column,
            description_column=description_column,
            msrp_column=msrp_column,
            cost_currency=cost_currency,
            msrp_currency=msrp_currency,
            fallback_msrp_column=fallback_msrp_column,
            fallback_msrp_currency=fallback_msrp_currency,
            sku_secondary_column=sku_secondary_column,
            sku_separator=sku_separator,
            barcode_column=barcode_column,
            map_cad_column=map_cad_column,
            coo_column=coo_column,
        )
        return result
    except Exception as e:
        logger.error(f"Pricelist upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class PricelistConfirmRequest(BaseModel):
    supplier_sku: str
    shopify_sku: str


@app.post("/api/vendors/{vendor}/pricelist/confirm-mapping")
async def vendors_confirm_pricelist_mapping(
    vendor: str,
    data: PricelistConfirmRequest,
    token: str = Depends(verify_token),
):
    """Save a vendor SKU → Shopify SKU mapping picked from a pricelist
    suggestion. Future uploads will resolve this supplier SKU automatically
    via the saved-mappings branch in the matcher."""
    supplier = (data.supplier_sku or '').strip()
    shopify = (data.shopify_sku or '').strip()
    if not supplier or not shopify:
        raise HTTPException(400, "supplier_sku and shopify_sku are required")
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE sku_mappings AS target
            USING (SELECT ? AS vendor, ? AS vendor_sku, ? AS shopify_sku) AS source
            ON target.vendor = source.vendor AND target.vendor_sku = source.vendor_sku
            WHEN MATCHED THEN UPDATE SET shopify_sku = source.shopify_sku
            WHEN NOT MATCHED THEN INSERT (vendor, vendor_sku, shopify_sku)
                VALUES (source.vendor, source.vendor_sku, source.shopify_sku);
        """, vendor, supplier, shopify)
        # Tag the corresponding pricelist row as matched so the UI can
        # reflect the change without a full re-compare.
        cursor.execute("""
            UPDATE vendor_pricelist_items
            SET matched_shopify_sku = ?, match_status = 'matched'
            WHERE vendor = ? AND supplier_sku = ?
        """, shopify, vendor, supplier)
        conn.commit()
        return {"status": "ok", "vendor": vendor,
                "supplier_sku": supplier, "shopify_sku": shopify}
    finally:
        conn.close()


@app.get("/api/vendors/{vendor}/pricelist")
async def vendors_get_pricelist(
    vendor: str,
    status: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """Get stored pricelist items for a vendor."""
    try:
        return get_pricelist_items(db, vendor, status=status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vendors/{vendor}/pricelist/download")
async def vendors_download_pricelist(vendor: str, token: str = Depends(verify_token)):
    """Download the stored raw pricelist CSV for a vendor."""
    try:
        result = get_stored_pricelist_csv(db, vendor)
        if not result:
            raise HTTPException(status_code=404, detail="No pricelist stored for this vendor")
        from fastapi.responses import Response
        return Response(
            content=result['content'],
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{result["filename"]}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/pricelist/recompare")
async def vendors_recompare_pricelist(vendor: str, token: str = Depends(verify_token)):
    """Re-run comparison using the stored pricelist and saved column mappings."""
    try:
        result = recompare_stored_pricelist(db, vendor)
        if not result:
            raise HTTPException(status_code=404, detail="No stored pricelist or column mappings for this vendor")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Recompare error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── VENDOR SALES (PROMOTIONAL PRICING) ─────────────────────────
# Upload, list, and clear vendor sale CSVs. Sales are date-windowed
# overrides on supplier_cost; the PO + replenishment code consults
# Database.resolve_effective_cost() to figure out what we'd pay today.

def _parse_sale_datetime(value: str, *, end_of_day: bool = False):
    """Parse a sale start/end date string into a naive UTC datetime.

    Accepts:
      - ISO 8601 (with or without time): 2026-05-15, 2026-05-15T14:30:00,
        2026-05-15 14:30, 2026-05-15T14:30:00-05:00
      - Common US formats: 5/15/2026, 5/15/2026 2:30 PM
    A bare date with no time becomes 00:00:00 (start) or 23:59:59 (end)
    depending on ``end_of_day``.
    """
    from datetime import timezone
    if value is None:
        raise ValueError("date is required")
    s = str(value).strip()
    if not s:
        raise ValueError("date is required")

    # Try ISO first (handles both date-only and full datetime)
    try:
        # fromisoformat handles "2026-05-15", "2026-05-15T14:30",
        # "2026-05-15 14:30:00", and offset-aware strings.
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        # If the input was date-only, time portion is exactly 00:00:00.
        # Detect by checking the original string for 'T' or ':'.
        looked_like_date_only = ('T' not in s) and (':' not in s)
        if looked_like_date_only and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    except ValueError:
        pass

    # US formats: try a couple of common patterns
    patterns = [
        '%m/%d/%Y %I:%M %p', '%m/%d/%Y %H:%M', '%m/%d/%Y',
        '%m-%d-%Y %I:%M %p', '%m-%d-%Y %H:%M', '%m-%d-%Y',
    ]
    for pat in patterns:
        try:
            dt = datetime.strptime(s, pat)
            if pat in ('%m/%d/%Y', '%m-%d-%Y') and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {value!r}")


@app.post("/api/vendors/{vendor}/sales")
async def vendors_upload_sale(
    vendor: str,
    sku_column: str = Query(...),
    sale_cost_column: str = Query(...),
    starts_at_column: Optional[str] = None,
    ends_at_column: Optional[str] = None,
    default_starts_at: Optional[str] = None,
    default_ends_at: Optional[str] = None,
    sale_currency: str = Query(default="USD"),
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """Upload a vendor sale list (CSV / XLSX). Date columns are optional;
    if omitted, ``default_starts_at`` and ``default_ends_at`` apply to every
    row. Both date sources accept ISO or US format with optional time-of-day
    (e.g. ``2026-05-15T09:00:00`` or ``5/15/2026 9:00 AM``)."""
    try:
        import re
        raw = await file.read()
        filename = file.filename or ""

        if filename.lower().endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv.writer(output)
            for row in ws.iter_rows():
                writer.writerow([str(cell.value).strip() if cell.value is not None else "" for cell in row])
            wb.close()
            content = output.getvalue()
        else:
            content = _decode_csv_bytes(raw)

        # Parse the CSV
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise HTTPException(400, "Empty file or no header row")

        # Validate that the requested columns exist
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        sku_col = cols.get(sku_column.strip().lower())
        cost_col = cols.get(sale_cost_column.strip().lower())
        starts_col = cols.get(starts_at_column.strip().lower()) if starts_at_column else None
        ends_col = cols.get(ends_at_column.strip().lower()) if ends_at_column else None
        if not sku_col or not cost_col:
            raise HTTPException(400, f"Required column not found. Headers seen: {reader.fieldnames}")

        # Resolve defaults once
        if not starts_col:
            if not default_starts_at:
                raise HTTPException(400, "Either starts_at_column or default_starts_at is required")
            default_starts_dt = _parse_sale_datetime(default_starts_at, end_of_day=False)
        else:
            default_starts_dt = None
        if not ends_col:
            if not default_ends_at:
                raise HTTPException(400, "Either ends_at_column or default_ends_at is required")
            default_ends_dt = _parse_sale_datetime(default_ends_at, end_of_day=True)
        else:
            default_ends_dt = None

        items = []
        skipped = 0
        for row in reader:
            sku = (row.get(sku_col) or '').strip()
            cost_raw = (row.get(cost_col) or '').strip()
            if not sku or not cost_raw:
                skipped += 1
                continue
            # Reuse the robust parse used by the pricelist matcher
            try:
                cost_clean = re.sub(r'[^\d.\-]', '', cost_raw)
                cost = float(cost_clean) if cost_clean else 0
            except ValueError:
                skipped += 1
                continue
            if cost <= 0:
                skipped += 1
                continue
            # Date resolution
            try:
                if starts_col:
                    starts_at = _parse_sale_datetime(row.get(starts_col) or '', end_of_day=False)
                else:
                    starts_at = default_starts_dt
                if ends_col:
                    ends_at = _parse_sale_datetime(row.get(ends_col) or '', end_of_day=True)
                else:
                    ends_at = default_ends_dt
            except ValueError as e:
                logger.warning(f"Sale upload: bad date for SKU {sku!r}: {e}")
                skipped += 1
                continue
            if ends_at < starts_at:
                skipped += 1
                continue
            items.append({
                'supplier_sku': sku,
                'sale_cost': cost,
                'sale_currency': sale_currency,
                'starts_at': starts_at,
                'ends_at': ends_at,
            })

        inserted = db.insert_vendor_sales(vendor, items, source_filename=filename)
        return {
            'inserted': inserted,
            'skipped': skipped,
            'total_rows': inserted + skipped,
            'filename': filename,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sale upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vendors/{vendor}/sales")
async def vendors_list_sales(vendor: str, token: str = Depends(verify_token)):
    """Return all sale rows (active, future, expired) for a vendor."""
    try:
        sales = db.list_vendor_sales(vendor)
        # serialize datetimes
        for s in sales:
            for k in ('starts_at', 'ends_at', 'uploaded_at'):
                if s.get(k) is not None:
                    s[k] = s[k].isoformat() if hasattr(s[k], 'isoformat') else str(s[k])
            if s.get('sale_cost') is not None:
                s['sale_cost'] = float(s['sale_cost'])
        return {'sales': sales}
    except Exception as e:
        logger.error(f"List sales error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/vendors/{vendor}/sales/{sale_id}")
async def vendors_delete_sale(
    vendor: str, sale_id: int, token: str = Depends(verify_token),
):
    """Delete a single sale row by id."""
    try:
        ok = db.delete_vendor_sale(sale_id)
        if not ok:
            raise HTTPException(404, "Sale not found")
        return {'deleted': True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/sales/clear")
async def vendors_clear_sales(
    vendor: str,
    only_expired: bool = Query(default=False),
    token: str = Depends(verify_token),
):
    """Delete all (or only-expired) sale rows for a vendor."""
    try:
        deleted = db.clear_vendor_sales(vendor, only_expired=only_expired)
        return {'deleted': deleted}
    except Exception as e:
        logger.error(f"Clear sales error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── AI DOCUMENT EXTRACTION ──────────────────────────────────────

from .ai_extract import extract_table_from_pdf
from .draft_creator import create_draft_from_pricelist, test_scrape_config
from .vendor_scraper import get_default_config, get_all_default_vendors, DEFAULT_CONFIGS


class CreateDraftRequest(BaseModel):
    vendor: str
    sku: str
    description: str = ""
    cost_foreign: float = 0
    cost_cad: float = 0
    sale_price_cad: float = 0
    product_url: str = ""
    barcode: Optional[str] = None
    map_cad: Optional[float] = None


@app.post("/api/products/create-draft")
async def products_create_draft(data: CreateDraftRequest, token: str = Depends(verify_token)):
    """Create a draft Shopify product from pricelist data, enriched by manufacturer website."""
    try:
        # Get vendor website URL
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT website_url FROM vendor_settings WHERE vendor = ?", data.vendor)
            row = cursor.fetchone()
            vendor_url = row[0] if row else None
        finally:
            conn.close()

        result = await create_draft_from_pricelist(
            shopify_client, db, data.vendor, data.sku,
            data.description, data.cost_foreign, data.cost_cad,
            data.sale_price_cad, vendor_url,
            product_url=data.product_url,
            barcode=data.barcode,
            map_cad=data.map_cad,
        )
        return result
    except Exception as e:
        logger.error(f"Draft creation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ai-extract")
async def ai_extract_pdf(
    file: UploadFile = File(...),
    model: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """Extract table data from a PDF using AI (OpenRouter)."""
    try:
        raw = await file.read()
        filename = file.filename or ""
        if not filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files supported for AI extraction")
        result = await extract_table_from_pdf(raw, model)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"AI extract error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── VENDOR SCRAPE CONFIG ───────────────────────────────────────


class ScrapeConfigTestRequest(BaseModel):
    product_url: str


class ScrapeConfigSaveRequest(BaseModel):
    config: dict


@app.get("/api/vendors/{vendor}/scrape-config")
async def get_vendor_scrape_config(vendor: str, token: str = Depends(verify_token)):
    """Get the scrape config for a vendor (from DB or defaults)."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scrape_config FROM vendor_settings WHERE vendor = ?",
            vendor
        )
        row = cursor.fetchone()
        if row and row[0]:
            return {
                "vendor": vendor,
                "source": "saved",
                "config": json.loads(row[0]),
            }
    finally:
        conn.close()

    default = get_default_config(vendor)
    if default:
        return {
            "vendor": vendor,
            "source": "default",
            "config": default,
        }

    return {
        "vendor": vendor,
        "source": "none",
        "config": None,
    }


@app.put("/api/vendors/{vendor}/scrape-config")
async def save_vendor_scrape_config(vendor: str, data: ScrapeConfigSaveRequest,
                                     token: str = Depends(verify_token)):
    """Save or update the scrape config for a vendor."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        config_json = json.dumps(data.config)

        cursor.execute(
            "SELECT vendor FROM vendor_settings WHERE vendor = ?", vendor
        )
        if cursor.fetchone():
            cursor.execute(
                "UPDATE vendor_settings SET scrape_config = ? WHERE vendor = ?",
                config_json, vendor
            )
        else:
            cursor.execute(
                "INSERT INTO vendor_settings (vendor, scrape_config) VALUES (?, ?)",
                vendor, config_json
            )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "vendor": vendor}


@app.delete("/api/vendors/{vendor}/scrape-config")
async def reset_vendor_scrape_config(vendor: str,
                                      token: str = Depends(verify_token)):
    """Reset a vendor's scrape config back to defaults (removes saved config)."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE vendor_settings SET scrape_config = NULL WHERE vendor = ?",
            vendor
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "vendor": vendor, "reset": True}


@app.post("/api/vendors/{vendor}/scrape-config/test")
async def test_vendor_scrape(vendor: str, data: ScrapeConfigTestRequest,
                              token: str = Depends(verify_token)):
    """Test a vendor's scrape config against a product URL."""
    result = await test_scrape_config(vendor, data.product_url, db)
    return result


@app.get("/api/vendors/scrape-config/defaults")
async def list_default_scrape_configs(token: str = Depends(verify_token)):
    """List all vendors with built-in default scrape configs."""
    return {
        "vendors": get_all_default_vendors(),
        "configs": {v: c for v, c in DEFAULT_CONFIGS.items()},
    }


@app.post("/api/vendors/{vendor}/scrape-config/load-default")
async def load_default_scrape_config(vendor: str,
                                      token: str = Depends(verify_token)):
    """Load a built-in default config into the vendor's saved config."""
    default = get_default_config(vendor)
    if not default:
        raise HTTPException(404, f"No default config for vendor: {vendor}")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        config_json = json.dumps(default)
        cursor.execute(
            "SELECT vendor FROM vendor_settings WHERE vendor = ?", vendor
        )
        if cursor.fetchone():
            cursor.execute(
                "UPDATE vendor_settings SET scrape_config = ? WHERE vendor = ?",
                config_json, vendor
            )
        else:
            cursor.execute(
                "INSERT INTO vendor_settings (vendor, scrape_config) VALUES (?, ?)",
                vendor, config_json
            )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "vendor": vendor, "config": default}


# ─── SKU MAPPINGS ───────────────────────────────────────────────

from .sku_matching import (
    list_mappings as sku_list_mappings,
    upsert_mapping as sku_upsert_mapping,
    delete_mapping as sku_delete_mapping,
    bulk_import_mappings as sku_bulk_import_mappings,
)


class SkuMappingUpsert(BaseModel):
    vendor_sku: str
    shopify_sku: str


@app.get("/api/vendors/{vendor}/sku-mappings")
async def api_list_vendor_sku_mappings(
    vendor: str, search: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """List all SKU mappings for a vendor, optionally filtered by search text."""
    try:
        return {"mappings": sku_list_mappings(db, vendor, search)}
    except Exception as e:
        logger.error(f"List SKU mappings error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/sku-mappings")
async def api_upsert_sku_mapping(
    vendor: str, data: SkuMappingUpsert,
    token: str = Depends(verify_token),
):
    """Create or update a vendor SKU → TC SKU mapping."""
    try:
        if not data.vendor_sku.strip() or not data.shopify_sku.strip():
            raise HTTPException(400, "vendor_sku and shopify_sku are required")
        return sku_upsert_mapping(db, vendor, data.vendor_sku, data.shopify_sku)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upsert SKU mapping error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sku-mappings/{mapping_id}")
async def api_delete_sku_mapping(mapping_id: int, token: str = Depends(verify_token)):
    """Delete a single SKU mapping."""
    try:
        return sku_delete_mapping(db, mapping_id)
    except Exception as e:
        logger.error(f"Delete SKU mapping error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendors/{vendor}/sku-mappings/bulk-import")
async def api_bulk_import_sku_mappings(
    vendor: str,
    file: UploadFile = File(...),
    vendor_sku_col: str = Query('vendor_sku'),
    tc_sku_col: str = Query('tc_sku'),
    token: str = Depends(verify_token),
):
    """Bulk import SKU mappings from a CSV or XLSX file."""
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()

        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            import csv as csv_mod
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv_mod.writer(output)
            for row in ws.iter_rows():
                writer.writerow([str(cell.value).strip() if cell.value is not None else "" for cell in row])
            wb.close()
            csv_content = output.getvalue()
        else:
            try:
                csv_content = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_content = raw.decode('latin-1')

        return sku_bulk_import_mappings(db, vendor, csv_content, vendor_sku_col, tc_sku_col)
    except Exception as e:
        logger.error(f"Bulk import SKU mappings error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── SALES MANAGER ──────────────────────────────────────────────

from .sales_manager import (
    create_sale, list_sales, get_sale, update_sale, delete_sale,
    preview_sale_pricelist, confirm_sale_items,
    activate_sale, revert_sale, resubmit_sale, exclude_item_from_sale,
    check_scheduled_sales,
)


class CreateSaleRequest(BaseModel):
    vendor: str
    name: str
    currency: str = "USD"
    fx_rate: float = 1.0
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    notes: Optional[str] = None
    collection_name: Optional[str] = None


class UpdateSaleRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    notes: Optional[str] = None
    collection_name: Optional[str] = None


class SaleConfirmRequest(BaseModel):
    items: List[dict]


class SaleExcludeRequest(BaseModel):
    sku: str


@app.get("/api/sales")
async def api_list_sales(
    vendor: Optional[str] = None,
    status: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """List all sales, optionally filtered by vendor or status."""
    try:
        return list_sales(db, vendor, status)
    except Exception as e:
        logger.error(f"List sales error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales")
async def api_create_sale(data: CreateSaleRequest, token: str = Depends(verify_token)):
    """Create a new vendor sale."""
    try:
        return create_sale(db, data.vendor, data.name, data.currency,
                          data.fx_rate, data.start_at, data.end_at, data.notes,
                          data.collection_name)
    except Exception as e:
        logger.error(f"Create sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sales/{sale_id}")
async def api_get_sale(sale_id: int, token: str = Depends(verify_token)):
    """Get a sale with all its items."""
    try:
        result = get_sale(db, sale_id)
        if not result:
            raise HTTPException(status_code=404, detail="Sale not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/sales/{sale_id}")
async def api_update_sale(sale_id: int, data: UpdateSaleRequest,
                          token: str = Depends(verify_token)):
    """Update sale metadata."""
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        return update_sale(db, sale_id, updates)
    except Exception as e:
        logger.error(f"Update sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sales/{sale_id}")
async def api_delete_sale(sale_id: int, token: str = Depends(verify_token)):
    """Delete a pending or cancelled sale."""
    try:
        return delete_sale(db, sale_id)
    except Exception as e:
        logger.error(f"Delete sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/detect-headers")
async def api_detect_file_headers(
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """Read the first row of a CSV/XLSX file and return the column headers."""
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()

        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
            ws = wb.active
            first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if first_row:
                headers = [str(h).strip() for h in first_row if h is not None and str(h).strip()]
                return {"headers": headers}
            return {"headers": []}
        else:
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')
            first_line = text.split('\n')[0].strip()
            if '\t' in first_line:
                headers = [h.strip() for h in first_line.split('\t') if h.strip()]
            else:
                headers = [h.strip().strip('"').strip("'") for h in first_line.split(',') if h.strip()]
            return {"headers": headers}
    except Exception as e:
        logger.error(f"Detect headers error: {e}", exc_info=True)
        return {"headers": []}


@app.post("/api/sales/{sale_id}/upload")
async def api_upload_sale_pricelist(
    sale_id: int,
    file: UploadFile = File(...),
    sku_col: str = Query("SKU"),
    price_col: str = Query("Sale Price"),
    price_currency: str = Query("USD"),
    fx_rate: float = Query(1.0),
    dealer_cost_col: Optional[str] = Query(None),
    dealer_cost_currency: Optional[str] = Query(None),
    dealer_cost_fx_rate: Optional[float] = Query(None),
    token: str = Depends(verify_token),
):
    """Upload a sale pricelist and get a preview of changes."""
    try:
        raw = await file.read()
        filename = file.filename or ""

        if filename.lower().endswith(('.xlsx', '.xls')):
            import openpyxl
            import csv as csv_mod
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                raise HTTPException(400, "Empty spreadsheet")
            headers = [str(h or '').strip() for h in rows[0]]
            # Use proper CSV writer to handle commas, quotes in cell values
            output = io.StringIO()
            writer = csv_mod.writer(output)
            writer.writerow(headers)
            for row in rows[1:]:
                writer.writerow([str(c or '') for c in row])
            csv_text = output.getvalue()
        else:
            try:
                csv_text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_text = raw.decode('latin-1')

        return preview_sale_pricelist(db, sale_id, csv_text,
                                      sku_col, price_col,
                                      price_currency, fx_rate,
                                      dealer_cost_col, dealer_cost_currency,
                                      dealer_cost_fx_rate)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sale pricelist upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/{sale_id}/confirm")
async def api_confirm_sale(sale_id: int, data: SaleConfirmRequest,
                           token: str = Depends(verify_token)):
    """Save previewed items to the sale (does not activate)."""
    try:
        return await confirm_sale_items(db, shopify_client, sale_id, data.items)
    except Exception as e:
        logger.error(f"Confirm sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/{sale_id}/activate")
async def api_activate_sale(sale_id: int, token: str = Depends(verify_token)):
    """Activate a sale: apply sale prices to Shopify."""
    try:
        return await activate_sale(db, shopify_client, sale_id)
    except Exception as e:
        logger.error(f"Activate sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/{sale_id}/revert")
async def api_revert_sale(sale_id: int, token: str = Depends(verify_token)):
    """Revert a sale: restore original prices."""
    try:
        return await revert_sale(db, shopify_client, sale_id)
    except Exception as e:
        logger.error(f"Revert sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/{sale_id}/resubmit")
async def api_resubmit_sale(sale_id: int, token: str = Depends(verify_token)):
    """Resubmit a completed/cancelled sale back to pending status for re-activation."""
    try:
        return resubmit_sale(db, sale_id)
    except Exception as e:
        logger.error(f"Resubmit sale error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/{sale_id}/exclude")
async def api_exclude_sale_item(sale_id: int, data: SaleExcludeRequest,
                                 token: str = Depends(verify_token)):
    """Remove a single SKU from an active sale."""
    try:
        return await exclude_item_from_sale(db, shopify_client, sale_id, data.sku)
    except Exception as e:
        logger.error(f"Exclude sale item error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sales/check-schedule")
async def api_check_sale_schedule(token: str = Depends(verify_token)):
    """Check for sales that need to be activated or reverted based on schedule."""
    try:
        actions = check_scheduled_sales(db)
        results = []
        for action in actions:
            if action["action"] == "activate":
                r = await activate_sale(db, shopify_client, action["sale_id"])
                results.append({**action, "result": r})
            elif action["action"] == "revert":
                r = await revert_sale(db, shopify_client, action["sale_id"])
                results.append({**action, "result": r})
        return {"actions_processed": len(results), "results": results}
    except Exception as e:
        logger.error(f"Schedule check error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



# ─── CO-PURCHASE RECOMMENDATIONS ─────────────────────────────────────────
# The analysis runs in the func-freshdesk-bot function app. TC-Planner
# proxies to it so the function key stays server-side, and so the UI has a
# single origin to talk to.
#
# The job runs for several minutes, well past the ~230s Azure holds an idle
# HTTP connection open. The recompute call therefore fires and returns; the
# outcome is read back from the status endpoint.

def _copurchase_headers():
    if not config.COPURCHASE_FUNCTION_KEY:
        raise HTTPException(
            status_code=503,
            detail="COPURCHASE_FUNCTION_KEY is not configured on this server",
        )
    return {"x-functions-key": config.COPURCHASE_FUNCTION_KEY}


@app.post("/api/copurchase/recompute")
async def copurchase_recompute(
    months: int = Query(0, ge=0, le=120),   # 0 = entire order history
    token: str = Depends(verify_token),
    user: str = Depends(current_user),
):
    """Kick off a co-purchase recompute. Returns as soon as it is running."""
    url = config.COPURCHASE_FUNCTION_URL.rstrip("/") + "/api/recompute_copurchase"
    headers = _copurchase_headers()
    try:
        # Short timeout on purpose: we only need to know the request landed.
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, params={"months": months}, headers=headers)
        if resp.status_code == 409:
            return {"status": "already_running", "detail": resp.json()}
        if resp.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail=f"Function returned {resp.status_code}: {resp.text[:200]}")
        # Finished inside 20s (only plausible for a tiny catalogue).
        return {"status": "ok", "result": resp.json()}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout):
        # Expected path. The function keeps running after we stop waiting.
        logger.info("Co-purchase recompute started by %s (%d months)", user, months)
        return {"status": "started",
                "message": "Recompute started — this takes several minutes."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Co-purchase recompute error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/copurchase/status")
async def copurchase_status(token: str = Depends(verify_token)):
    """Progress of a running recompute, or the last run's summary."""
    url = config.COPURCHASE_FUNCTION_URL.rstrip("/") + "/api/copurchase_status"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_copurchase_headers())
        if resp.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail=f"Function returned {resp.status_code}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Co-purchase status error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))

# ─── SKU LOOKUP ──────────────────────────────────────────────────

@app.get("/api/products/lookup-sku")
async def products_lookup_sku(sku: str = Query(...), token: str = Depends(verify_token)):
    """Look up a SKU in the velocity cache and return product details."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT product_title, variant_title, vendor, price, cost, current_stock
                FROM product_velocity_cache WHERE sku = ?
            """, sku)
            row = cursor.fetchone()
            if row:
                return {
                    "found": True, "sku": sku,
                    "product_title": row[0], "variant_title": row[1],
                    "vendor": row[2], "price": float(row[3] or 0),
                    "cost": float(row[4] or 0), "current_stock": row[5] or 0,
                }
            return {"found": False, "sku": sku}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"SKU lookup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── DONATIONS TRACKING ─────────────────────────────────────────

class DonationItemInput(BaseModel):
    type: str = "product"
    sku: Optional[str] = None
    title: str
    value: float = 0


class DonationCreate(BaseModel):
    date: Optional[str] = None
    donated_to: str
    comments: Optional[str] = None
    items: List[DonationItemInput]


class DonationUpdate(BaseModel):
    date: Optional[str] = None
    donated_to: Optional[str] = None
    comments: Optional[str] = None
    items: Optional[List[DonationItemInput]] = None


def _parse_date(val):
    if not val or not val.strip():
        return None
    try:
        return datetime.strptime(val.strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@app.get("/api/donations")
async def api_list_donations(token: str = Depends(verify_token)):
    """List all donations with their line items."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()

            # Get all donation headers
            cursor.execute("""
                SELECT d.id, d.date, d.donated_to, d.comments, d.created_at,
                       (SELECT COUNT(*) FROM donation_items di WHERE di.donation_id = d.id) AS item_count,
                       (SELECT COALESCE(SUM(di.value), 0) FROM donation_items di WHERE di.donation_id = d.id) AS total_value
                FROM donations d
                ORDER BY d.date DESC, d.created_at DESC
            """)
            donations = []
            for row in cursor.fetchall():
                d = {
                    'id': row[0],
                    'date': row[1].isoformat() if row[1] and hasattr(row[1], 'isoformat') else str(row[1]) if row[1] else None,
                    'donated_to': row[2],
                    'comments': row[3],
                    'created_at': row[4].isoformat() if row[4] and hasattr(row[4], 'isoformat') else None,
                    'item_count': row[5] or 0,
                    'total_value': float(row[6] or 0),
                }
                donations.append(d)

            # Get all items grouped by donation_id
            if donations:
                donation_ids = [d['id'] for d in donations]
                ph = ','.join(['?'] * len(donation_ids))
                cursor.execute(f"""
                    SELECT id, donation_id, type, sku, title, value
                    FROM donation_items
                    WHERE donation_id IN ({ph})
                    ORDER BY id
                """, *donation_ids)
                items_by_donation = {}
                for row in cursor.fetchall():
                    did = row[1]
                    if did not in items_by_donation:
                        items_by_donation[did] = []
                    items_by_donation[did].append({
                        'id': row[0], 'type': row[2], 'sku': row[3],
                        'title': row[4], 'value': float(row[5] or 0),
                    })
                for d in donations:
                    d['items'] = items_by_donation.get(d['id'], [])

            return {"donations": donations}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"List donations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/donations")
async def api_create_donation(data: DonationCreate, token: str = Depends(verify_token)):
    """Create a donation with line items."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            date_val = _parse_date(data.date)

            cursor.execute("""
                INSERT INTO donations (date, donated_to, comments, created_at)
                OUTPUT INSERTED.id
                VALUES (?, ?, ?, GETUTCDATE())
            """, date_val, data.donated_to, data.comments or None)
            donation_id = cursor.fetchone()[0]

            for item in data.items:
                cursor.execute("""
                    INSERT INTO donation_items (donation_id, type, sku, title, value)
                    VALUES (?, ?, ?, ?, ?)
                """, donation_id, item.type, item.sku or None, item.title, item.value or 0)

            conn.commit()
            return {"status": "ok", "id": donation_id, "items_created": len(data.items)}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Create donation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/donations/{donation_id}")
async def api_update_donation(donation_id: int, data: DonationUpdate,
                               token: str = Depends(verify_token)):
    """Update a donation header and replace its line items."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()

            # Update header fields
            sets = []
            params = []
            if data.date is not None:
                sets.append("date = ?")
                params.append(_parse_date(data.date))
            if data.donated_to is not None:
                sets.append("donated_to = ?")
                params.append(data.donated_to)
            if data.comments is not None:
                sets.append("comments = ?")
                params.append(data.comments or None)
            if sets:
                params.append(donation_id)
                cursor.execute(f"UPDATE donations SET {', '.join(sets)} WHERE id = ?", *params)

            # Replace line items if provided
            if data.items is not None:
                cursor.execute("DELETE FROM donation_items WHERE donation_id = ?", donation_id)
                for item in data.items:
                    cursor.execute("""
                        INSERT INTO donation_items (donation_id, type, sku, title, value)
                        VALUES (?, ?, ?, ?, ?)
                    """, donation_id, item.type, item.sku or None, item.title, item.value or 0)

            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Update donation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/donations/{donation_id}")
async def api_delete_donation(donation_id: int, token: str = Depends(verify_token)):
    """Delete a donation and all its line items."""
    try:
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM donation_items WHERE donation_id = ?", donation_id)
            cursor.execute("DELETE FROM donations WHERE id = ?", donation_id)
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Delete donation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── APP SETTINGS / THRESHOLDS ───────────────────────────────────

_THRESHOLD_KEYS = {
    'stop_buying_min_dos': (180.0, 'float', 'Minimum days-of-stock for Stop Buying list'),
    'buy_more_max_dos': (60.0, 'float', 'Maximum days-of-stock for Buy More list'),
    'buy_more_min_velocity': (0.03, 'float', 'Minimum avg daily velocity for Buy More list'),
    'buy_more_min_margin_pct': (10.0, 'float', 'Minimum gross margin % for Buy More list'),
    'replenishment_cycle_days': (30.0, 'float',
        'How long each PO should last after it arrives (the order cycle). '
        'Combined with the projection multiplier to set the demand window: '
        '(cycle days × multiplier) + lead time. Default 30; raise to order '
        'less often, lower to keep less stock on hand.'),
    'projection_multiplier': (1.0, 'float',
        'Multiplier on the order cycle. 1.0 = cycle days as-is; 1.5 = 1.5× '
        'cycle (45 days at default cycle); 2.0 = 60 days. Lead time is '
        'added on top automatically as a fixed buffer.'),
    'waiter_conversion_rate': (0.6, 'float',
        'Fraction of back-in-stock waiters expected to actually buy when restocked. '
        'Used as a hard floor on replenish_qty: if 10 customers are waiting and the '
        'rate is 0.6, the system orders at least 6 units regardless of velocity. '
        'Set to 0 to disable the floor entirely.'),
}


class ThresholdsUpdate(BaseModel):
    stop_buying_min_dos: Optional[float] = None
    buy_more_max_dos: Optional[float] = None
    buy_more_min_velocity: Optional[float] = None
    buy_more_min_margin_pct: Optional[float] = None
    replenishment_cycle_days: Optional[float] = None
    projection_multiplier: Optional[float] = None
    waiter_conversion_rate: Optional[float] = None


@app.get("/api/settings/thresholds")
async def api_get_thresholds(token: str = Depends(verify_token)):
    """Return current threshold settings with their defaults."""
    result = {}
    for key, (default, _type, desc) in _THRESHOLD_KEYS.items():
        result[key] = {
            'value': db.get_float_setting(key, default),
            'default': default,
            'description': desc,
        }
    return result


@app.put("/api/settings/thresholds")
async def api_update_thresholds(
    data: ThresholdsUpdate, token: str = Depends(verify_token)
):
    """Update threshold settings. Only non-null fields are changed."""
    updates = {}
    for key, val in data.dict().items():
        if val is None:
            continue
        if key not in _THRESHOLD_KEYS:
            continue
        # Basic sanity
        if val < 0:
            raise HTTPException(400, f"{key} must be >= 0")
        if key == 'buy_more_min_margin_pct' and val > 100:
            raise HTTPException(400, f"{key} is a percent (0-100), not a ratio")
        if key == 'projection_multiplier' and (val < 0.5 or val > 5.0):
            raise HTTPException(400,
                f"{key} should be between 0.5 and 5.0 — values outside this range "
                f"produce unreasonable recommendations")
        if key == 'replenishment_cycle_days' and (val < 7 or val > 365):
            raise HTTPException(400,
                f"{key} should be between 7 and 365 days — outside this range "
                f"the math is meaningless")
        if key == 'waiter_conversion_rate' and val > 2.0:
            raise HTTPException(400,
                f"{key} should be 0–2.0. Values above 1.0 mean ordering more units "
                f"than waiters; above 2.0 likely indicates a typo")
        db.set_setting(key, val)
        updates[key] = val
    return {"status": "ok", "updated": updates}


# ─── IP vs TC PO COMPARISON ──────────────────────────────────────

from .po_comparison import compare_po as po_compare


@app.post("/api/po-comparison/compare")
async def api_compare_po(
    file: UploadFile = File(...),
    vendor: Optional[str] = Query(None, description="Optional vendor filter"),
    tc_order_id: Optional[int] = Query(None, description="Optional TC stock order ID to compare against"),
    token: str = Depends(verify_token),
):
    """
    Compare an IP purchase order export against TC's recommendations.
    Returns a per-SKU verdict showing agreements, disagreements, and reasons.
    If tc_order_id is provided, compares against that specific saved TC PO's
    line items instead of the live increase_buying list.
    """
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()

        # Convert XLSX to CSV text
        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            import csv as csv_mod
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv_mod.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else str(c) for c in row])
            wb.close()
            ip_content = output.getvalue()
        else:
            try:
                ip_content = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                ip_content = raw.decode('latin-1')

        # Optionally load TC items from a saved stock order
        tc_items = None
        if tc_order_id:
            tc_order = db.get_stock_order(tc_order_id)
            if not tc_order:
                raise HTTPException(404, f"TC stock order {tc_order_id} not found")
            tc_items = [
                {'sku': item['sku'], 'qty': item['ordered_qty'],
                 'title': item.get('product_title', '')}
                for item in (tc_order.get('items') or [])
            ]

        return po_compare(db, ip_content, tc_items=tc_items, vendor_filter=vendor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PO comparison error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── BACK-IN-STOCK WAITERS ───────────────────────────────────────

from .waiters import (
    preview_upload as waiter_preview_upload,
    import_snapshot as waiter_import_snapshot,
    list_snapshots as waiter_list_snapshots,
    delete_snapshot as waiter_delete_snapshot,
    current_waiters_per_sku as waiter_current_per_sku,
    get_waiters_for_sku as waiter_get_for_sku,
)


@app.post("/api/waiters/preview")
async def api_preview_waiters(
    file: UploadFile = File(...),
    snapshot_date: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Parse a back-in-stock waiters report and preview what would be imported."""
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()

        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            import csv as csv_mod
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv_mod.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else str(c) for c in row])
            wb.close()
            csv_content = output.getvalue()
        else:
            try:
                csv_content = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_content = raw.decode('latin-1')

        return waiter_preview_upload(db, csv_content, snapshot_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Waiter preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/waiters/import")
async def api_import_waiters(
    file: UploadFile = File(...),
    snapshot_date: Optional[str] = Query(None),
    notes: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Parse and insert a back-in-stock waiters report as a new snapshot."""
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()

        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            import csv as csv_mod
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv_mod.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else str(c) for c in row])
            wb.close()
            csv_content = output.getvalue()
        else:
            try:
                csv_content = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_content = raw.decode('latin-1')

        return waiter_import_snapshot(db, csv_content, file.filename, snapshot_date, notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Waiter import error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/waiters/snapshots")
async def api_list_waiter_snapshots(token: str = Depends(verify_token)):
    """List all waiter report snapshots."""
    try:
        return {"snapshots": waiter_list_snapshots(db)}
    except Exception as e:
        logger.error(f"List waiter snapshots error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/waiters/snapshots/{snapshot_id}")
async def api_delete_waiter_snapshot(snapshot_id: int, token: str = Depends(verify_token)):
    """Delete a waiter snapshot and all its records."""
    try:
        return waiter_delete_snapshot(db, snapshot_id)
    except Exception as e:
        logger.error(f"Delete waiter snapshot error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/waiters/current")
async def api_current_waiters(
    vendor: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """Return per-SKU waiter counts from the latest snapshot."""
    try:
        return {"waiters": waiter_current_per_sku(db, vendor)}
    except Exception as e:
        logger.error(f"Current waiters error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/waiters/sku/{sku}")
async def api_waiters_for_sku(
    sku: str,
    include_history: bool = Query(False),
    token: str = Depends(verify_token),
):
    """Return detailed waiter info for a single SKU."""
    try:
        return waiter_get_for_sku(db, sku, include_history)
    except Exception as e:
        logger.error(f"Waiters for SKU error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── COGS TRACKING ───────────────────────────────────────────────

from .cogs import (
    preview_invoice, confirm_invoice, list_invoices, delete_invoice,
    get_invoice_lots, get_cogs_summary,
)


@app.get("/api/cogs/invoices")
async def cogs_list_invoices(vendor: Optional[str] = None, token: str = Depends(verify_token)):
    """List purchase invoices."""
    try:
        return list_invoices(db, vendor)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/invoices/{invoice_id}/lots")
async def cogs_invoice_lots(invoice_id: int, token: str = Depends(verify_token)):
    """Get line items for an invoice."""
    try:
        return get_invoice_lots(db, invoice_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cogs/invoices/preview")
async def cogs_preview_invoice(
    vendor: str = Query(...),
    sku_column: str = Query(...),
    qty_column: str = Query(...),
    cost_column: str = Query(...),
    currency: str = Query(default="USD"),
    description_column: Optional[str] = None,
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """Parse invoice and return matching preview with SKU suggestions."""
    try:
        raw = await file.read()
        filename = file.filename or ""

        if filename.lower().endswith('.pdf'):
            _, content = _extract_pdf_table(raw)
        elif filename.lower().endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv.writer(output)
            for row in ws.iter_rows():
                writer.writerow([str(cell.value).strip() if cell.value is not None else "" for cell in row])
            wb.close()
            content = output.getvalue()
        else:
            content = raw.decode("utf-8-sig")

        result = preview_invoice(
            db, vendor, content,
            sku_column=sku_column,
            qty_column=qty_column,
            cost_column=cost_column,
            currency=currency,
            description_column=description_column,
        )
        return result
    except Exception as e:
        logger.error(f"Invoice preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class InvoiceConfirmRequest(BaseModel):
    vendor: str
    invoice_number: str
    invoice_date: str
    currency: str
    fx_rate: float
    items: List[dict]


@app.post("/api/cogs/invoices/confirm")
async def cogs_confirm_invoice(data: InvoiceConfirmRequest, token: str = Depends(verify_token)):
    """Confirm matched items and create purchase lots."""
    try:
        result = confirm_invoice(
            db, data.vendor, data.invoice_number, data.invoice_date,
            data.currency, data.fx_rate, data.items,
        )
        return result
    except Exception as e:
        logger.error(f"Invoice confirm error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/cogs/invoices/{invoice_id}")
async def cogs_delete_invoice(invoice_id: int, token: str = Depends(verify_token)):
    """Delete an invoice and its purchase lots."""
    try:
        return delete_invoice(db, invoice_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/summary")
async def cogs_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    token: str = Depends(verify_token),
):
    """Get COGS summary with monthly breakdown."""
    try:
        return get_cogs_summary(db, start_date, end_date)
    except Exception as e:
        logger.error(f"COGS summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── FIFO ORDER MATCHING ─────────────────────────────────────────

from .cogs import run_fifo_matcher, list_fifo_orders, get_fifo_order_detail


class FifoRunRequest(BaseModel):
    since_date: str
    until_date: Optional[str] = None
    reprocess: bool = False


@app.post("/api/cogs/fifo/run")
async def cogs_run_fifo(data: FifoRunRequest, token: str = Depends(verify_token)):
    """Pull Shopify orders in a date range and FIFO-match them to purchase lots."""
    try:
        since_dt = datetime.fromisoformat(data.since_date)
        until_dt = datetime.fromisoformat(data.until_date) if data.until_date else None
        line_items_by_order = await shopify_client.fetch_orders_for_fifo(since_dt, until_dt)
        return run_fifo_matcher(db, line_items_by_order, reprocess=data.reprocess)
    except Exception as e:
        logger.error(f"FIFO run error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/orders")
async def cogs_orders(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    token: str = Depends(verify_token),
):
    """Per-order FIFO P&L list."""
    try:
        return list_fifo_orders(db, start_date, end_date, vendor, limit, offset)
    except Exception as e:
        logger.error(f"COGS orders error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/orders/{order_number:path}")
async def cogs_order_detail(order_number: str, token: str = Depends(verify_token)):
    """Per-line FIFO breakdown for one order."""
    try:
        return get_fifo_order_detail(db, order_number)
    except Exception as e:
        logger.error(f"COGS order detail error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── OPERATING EXPENSES ──────────────────────────────────────────

from .expenses import (
    CATEGORIES as EXPENSE_CATEGORIES,
    add_expense, update_expense, delete_expense, list_expenses,
    get_expenses_summary, upload_expenses_csv,
    list_recurring_templates, add_recurring_template,
    update_recurring_template, delete_recurring_template,
    materialize_recurring_expenses,
)


class ExpenseRequest(BaseModel):
    expense_date: str
    category: str
    amount_foreign: float
    currency: str = 'CAD'
    fx_rate: Optional[float] = None
    description: Optional[str] = None
    vendor: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/expenses")
async def expenses_list(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    token: str = Depends(verify_token),
):
    try:
        return list_expenses(db, start_date, end_date, category, limit, offset)
    except Exception as e:
        logger.error(f"Expenses list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/expenses/summary")
async def expenses_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    token: str = Depends(verify_token),
):
    try:
        return get_expenses_summary(db, start_date, end_date)
    except Exception as e:
        logger.error(f"Expenses summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/expenses/categories")
async def expenses_categories(token: str = Depends(verify_token)):
    return {'categories': EXPENSE_CATEGORIES}


@app.post("/api/expenses")
async def expenses_create(data: ExpenseRequest, token: str = Depends(verify_token)):
    try:
        return add_expense(
            db, expense_date=data.expense_date, category=data.category,
            amount_foreign=data.amount_foreign, currency=data.currency,
            fx_rate=data.fx_rate, description=data.description, vendor=data.vendor,
            period_start=data.period_start, period_end=data.period_end, notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Expense create error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/expenses/{expense_id}")
async def expenses_update(expense_id: int, data: ExpenseRequest, token: str = Depends(verify_token)):
    try:
        return update_expense(
            db, expense_id, expense_date=data.expense_date, category=data.category,
            amount_foreign=data.amount_foreign, currency=data.currency, fx_rate=data.fx_rate,
            description=data.description, vendor=data.vendor,
            period_start=data.period_start, period_end=data.period_end, notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Expense update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/expenses/{expense_id}")
async def expenses_delete(expense_id: int, token: str = Depends(verify_token)):
    try:
        return delete_expense(db, expense_id)
    except Exception as e:
        logger.error(f"Expense delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/expenses/upload")
async def expenses_upload(
    date_column: str = Query(...),
    category_column: str = Query(...),
    amount_column: str = Query(...),
    description_column: Optional[str] = None,
    vendor_column: Optional[str] = None,
    period_start_column: Optional[str] = None,
    period_end_column: Optional[str] = None,
    currency_column: Optional[str] = None,
    default_currency: str = "CAD",
    file: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    try:
        raw = await file.read()
        filename = file.filename or ""
        if filename.lower().endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            output = io.StringIO()
            writer = csv.writer(output)
            for row in ws.iter_rows():
                writer.writerow([str(cell.value).strip() if cell.value is not None else "" for cell in row])
            wb.close()
            content = output.getvalue()
        else:
            content = raw.decode("utf-8-sig")
        return upload_expenses_csv(
            db, content, date_col=date_column, category_col=category_column,
            amount_col=amount_column, description_col=description_column,
            vendor_col=vendor_column, period_start_col=period_start_column,
            period_end_col=period_end_column, currency_col=currency_column,
            default_currency=default_currency,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Expenses upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class RecurringTemplateRequest(BaseModel):
    name: str
    category: str
    amount_foreign: float
    currency: str = 'CAD'
    day_of_month: int = 1
    vendor: Optional[str] = None
    notes: Optional[str] = None
    start_date: str
    end_date: Optional[str] = None
    active: Optional[bool] = True


@app.get("/api/expenses/templates")
async def expenses_templates_list(include_inactive: bool = False, token: str = Depends(verify_token)):
    try:
        return {'templates': list_recurring_templates(db, include_inactive=include_inactive)}
    except Exception as e:
        logger.error(f"List templates error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/expenses/templates")
async def expenses_templates_create(data: RecurringTemplateRequest, token: str = Depends(verify_token)):
    try:
        result = add_recurring_template(
            db, name=data.name, category=data.category,
            amount_foreign=data.amount_foreign, currency=data.currency,
            day_of_month=data.day_of_month, vendor=data.vendor, notes=data.notes,
            start_date=data.start_date, end_date=data.end_date,
        )
        try:
            materialize_recurring_expenses(db)
        except Exception as me:
            logger.warning("Post-create materialize failed: %s", me)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create template error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/expenses/templates/{template_id}")
async def expenses_templates_update(template_id: int, data: RecurringTemplateRequest, token: str = Depends(verify_token)):
    try:
        return update_recurring_template(
            db, template_id, name=data.name, category=data.category,
            amount_foreign=data.amount_foreign, currency=data.currency,
            day_of_month=data.day_of_month, vendor=data.vendor, notes=data.notes,
            start_date=data.start_date, end_date=data.end_date, active=data.active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Update template error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/expenses/templates/{template_id}")
async def expenses_templates_delete(template_id: int, cascade: bool = False, token: str = Depends(verify_token)):
    try:
        return delete_recurring_template(db, template_id, cascade=cascade)
    except Exception as e:
        logger.error(f"Delete template error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/expenses/templates/materialize")
async def expenses_templates_materialize(token: str = Depends(verify_token)):
    try:
        return materialize_recurring_expenses(db)
    except Exception as e:
        logger.error(f"Materialize error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── SALE PERFORMANCE ────────────────────────────────────────────

from .sale_performance import get_sku_leaderboard, list_orders_with_overhead


@app.get("/api/sale-performance/skus")
async def sale_performance_skus(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
    method: str = "revenue",
    sort_by: str = "profit",
    limit: int = 50,
    token: str = Depends(verify_token),
):
    try:
        return get_sku_leaderboard(db, start_date, end_date, vendor,
                                   method=method, sort_by=sort_by, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"SKU leaderboard error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sale-performance/orders")
async def sale_performance_orders(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
    method: str = "revenue",
    limit: int = 200,
    offset: int = 0,
    token: str = Depends(verify_token),
):
    try:
        return list_orders_with_overhead(db, start_date, end_date, vendor,
                                         method=method, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Sale performance orders error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── MIN STOCK LEVELS ────────────────────────────────────────────

from .min_stock import (
    list_min_stock as _list_min_stock,
    set_min_stock as _set_min_stock,
    bulk_set_min_stock as _bulk_set_min_stock,
)


class MinStockRequest(BaseModel):
    sku: str
    min_stock: int
    notes: Optional[str] = None


class MinStockBulkRequest(BaseModel):
    items: List[dict]


@app.get("/api/min-stock")
async def min_stock_list(only_set: bool = True, token: str = Depends(verify_token)):
    try:
        return {'items': _list_min_stock(db, only_set=only_set)}
    except Exception as e:
        logger.error(f"List min stock error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/min-stock")
async def min_stock_set(data: MinStockRequest, token: str = Depends(verify_token)):
    try:
        result = _set_min_stock(db, data.sku, data.min_stock, notes=data.notes)
        try:
            db.recalc_on_order_for_skus([data.sku])
        except Exception as re:
            logger.warning("Post-set min stock recalc failed: %s", re)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Set min stock error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/min-stock/bulk")
async def min_stock_bulk(data: MinStockBulkRequest, token: str = Depends(verify_token)):
    try:
        result = _bulk_set_min_stock(db, data.items)
        skus = [it.get('sku') for it in data.items if it.get('sku')]
        if skus:
            try:
                db.recalc_on_order_for_skus(skus)
            except Exception as re:
                logger.warning("Post-bulk-set min stock recalc failed: %s", re)
        return result
    except Exception as e:
        logger.error(f"Bulk set min stock error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── PENDING INVOICES + VENDOR ALIASES + EMAIL INGEST ────────────

from fastapi import Header
from .email_ingest import (
    EMAIL_INGEST_SECRET as _EMAIL_INGEST_SECRET,
    ingest_email as _ingest_email,
    list_pending_invoices as _list_pending_invoices,
    get_pending_invoice as _get_pending_invoice,
    preview_pending_match as _preview_pending_match,
    approve_pending as _approve_pending,
    reject_pending as _reject_pending,
    delete_pending as _delete_pending,
    list_vendor_aliases as _list_vendor_aliases,
    add_vendor_alias as _add_vendor_alias,
    delete_vendor_alias as _delete_vendor_alias,
)


class EmailIngestPayload(BaseModel):
    message_id: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    body_text: Optional[str] = None
    attachments: List[dict] = []
    class Config:
        extra = 'allow'


def _verify_ingest_secret(provided: Optional[str]):
    if not _EMAIL_INGEST_SECRET:
        raise HTTPException(status_code=503, detail="EMAIL_INGEST_SECRET not configured.")
    if not provided or provided != _EMAIL_INGEST_SECRET:
        raise HTTPException(status_code=401, detail="Invalid ingest secret.")


@app.post("/api/email-ingest/invoice")
async def email_ingest_invoice(
    payload: EmailIngestPayload,
    x_ingest_secret: Optional[str] = Header(default=None, convert_underscores=True),
):
    _verify_ingest_secret(x_ingest_secret)
    try:
        return await _ingest_email(db, payload.dict())
    except Exception as e:
        logger.error(f"Email ingest error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/invoices/pending")
async def cogs_pending_list(status: Optional[str] = None, token: str = Depends(verify_token)):
    try:
        return {'pending': _list_pending_invoices(db, status_filter=status)}
    except Exception as e:
        logger.error(f"List pending error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/invoices/pending/{pending_id}")
async def cogs_pending_detail(pending_id: int, token: str = Depends(verify_token)):
    try:
        row = _get_pending_invoice(db, pending_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pending detail error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cogs/invoices/pending/{pending_id}/preview-match")
async def cogs_pending_preview_match(pending_id: int, vendor: Optional[str] = None, token: str = Depends(verify_token)):
    try:
        return _preview_pending_match(db, pending_id, vendor_override=vendor)
    except Exception as e:
        logger.error(f"Pending preview-match error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class PendingApproveRequest(BaseModel):
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: Optional[str] = None
    is_vendor_sale: bool = False
    save_alias: bool = False
    items: Optional[List[dict]] = None


@app.post("/api/cogs/invoices/pending/{pending_id}/approve")
async def cogs_pending_approve(pending_id: int, data: PendingApproveRequest, token: str = Depends(verify_token)):
    try:
        return _approve_pending(
            db, pending_id, vendor_override=data.vendor,
            invoice_number_override=data.invoice_number,
            invoice_date_override=data.invoice_date,
            currency_override=data.currency,
            is_vendor_sale=data.is_vendor_sale,
            save_alias=data.save_alias, items_override=data.items,
        )
    except Exception as e:
        logger.error(f"Pending approve error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class PendingRejectRequest(BaseModel):
    reason: Optional[str] = None


@app.post("/api/cogs/invoices/pending/{pending_id}/reject")
async def cogs_pending_reject(pending_id: int, data: PendingRejectRequest, token: str = Depends(verify_token)):
    try:
        return _reject_pending(db, pending_id, reason=data.reason)
    except Exception as e:
        logger.error(f"Pending reject error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/cogs/invoices/pending/{pending_id}")
async def cogs_pending_delete(pending_id: int, token: str = Depends(verify_token)):
    try:
        return _delete_pending(db, pending_id)
    except Exception as e:
        logger.error(f"Pending delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class VendorAliasRequest(BaseModel):
    alias: str
    canonical_vendor: str


@app.get("/api/vendor-aliases")
async def vendor_aliases_list(token: str = Depends(verify_token)):
    try:
        return {'aliases': _list_vendor_aliases(db)}
    except Exception as e:
        logger.error(f"List aliases error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vendor-aliases")
async def vendor_aliases_create(data: VendorAliasRequest, token: str = Depends(verify_token)):
    try:
        return _add_vendor_alias(db, data.alias, data.canonical_vendor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create alias error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/vendor-aliases/{alias_id}")
async def vendor_aliases_delete(alias_id: int, token: str = Depends(verify_token)):
    try:
        return _delete_vendor_alias(db, alias_id)
    except Exception as e:
        logger.error(f"Delete alias error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── LLM INVOICE PARSE ───────────────────────────────────────────

from .llm_invoice_extractor import (
    extract_invoice as _llm_extract_invoice,
    items_to_csv as _llm_items_to_csv,
    MODEL_PRESETS as _LLM_MODEL_PRESETS,
)


@app.get("/api/cogs/invoices/llm-models")
async def cogs_llm_models(token: str = Depends(verify_token)):
    return {'models': _LLM_MODEL_PRESETS}


@app.post("/api/cogs/invoices/llm-parse")
async def cogs_llm_parse_invoice(
    file: UploadFile = File(...),
    model: str = Query("claude"),
    token: str = Depends(verify_token),
):
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()
        if filename.endswith(".pdf"):
            kind = "pdf"
        elif filename.endswith((".xlsx", ".xls")):
            kind = "xlsx"
        else:
            kind = "txt"
        result = await _llm_extract_invoice(raw, kind, model=model)
        result['csv_content'] = _llm_items_to_csv(result.get('items') or [])
        result['suggested_columns'] = {
            'sku_column': 'vendor_sku',
            'qty_column': 'quantity',
            'cost_column': 'unit_cost',
            'description_column': 'description',
        }
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM invoice parse error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── STATIC FILES & SPA FALLBACK ─────────────────────────────────

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA for any non-API route."""
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(static_dir / "index.html"))


# ─── RUN ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
