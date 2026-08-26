"""
TC Inventory Planner - Shopify GraphQL API Client
Fetches products, inventory levels, and order line item history.
"""
import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import config

logger = logging.getLogger(__name__)


# ─── SKU NORMALIZATION ───────────────────────────────────────────
# Converts supplier-style SKUs to standard ASCII for matching.
# Handles: Unicode Roman numerals (Ⅱ→II), en/em dashes (–/—→-),
# Unicode minus (−→-), and other exotic dashes.

_ROMAN_NUMERAL_MAP = {
    "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
    "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X",
    "ⅱ": "II", "ⅲ": "III", "ⅳ": "IV", "ⅴ": "V",
    "ⅵ": "VI", "ⅶ": "VII", "ⅷ": "VIII", "ⅸ": "IX", "ⅹ": "X",
    # Single Roman numeral chars (less common but possible)
    "Ⅰ": "I", "ⅰ": "I",
}

# All Unicode dash/hyphen-like characters → standard ASCII hyphen
_DASH_CHARS = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]")

# Double-quote / prime variants -> ASCII double-quote.
# U+2033 (prime, used for inches), U+201C/D (curly), U+201E/F,
# U+2036 (reversed double prime), U+275D/E, U+301D-F, U+FF02 (fullwidth).
# Different upstream systems normalize quotes differently, so we fold
# them all to ASCII " for matching — keeps SKUs like 'ZWO HF1.25"'
# matchable across spreadsheets, Shopify, and supplier pricelists.
_DOUBLE_QUOTE_CHARS = re.compile(
    r"[\u2033\u201C\u201D\u201E\u201F\u2036\u275D\u275E\u301D\u301E\u301F\uFF02]"
)

# Single-quote / apostrophe / prime variants -> ASCII single-quote.
# U+2018/9 (curly), U+201A/B (low/reversed), U+2032 (prime),
# U+2035 (reversed prime), U+275B/C, U+FF07 (fullwidth).
_SINGLE_QUOTE_CHARS = re.compile(
    r"[\u2018\u2019\u201A\u201B\u2032\u2035\u275B\u275C\uFF07]"
)


def normalize_sku(sku: str) -> str:
    """
    Normalize a SKU to standard ASCII for matching.
    - Replaces Unicode Roman numerals (Ⅱ, Ⅲ, etc.) with ASCII (II, III)
    - Replaces en-dash, em-dash, minus sign, etc. with standard hyphen
    - Strips leading/trailing whitespace
    - Collapses multiple spaces to one
    """
    if not sku:
        return ""
    s = sku.strip()
    # Replace Roman numeral characters
    for unicode_char, ascii_equiv in _ROMAN_NUMERAL_MAP.items():
        s = s.replace(unicode_char, ascii_equiv)
    # Replace all dash-like characters with standard hyphen
    s = _DASH_CHARS.sub("-", s)
    # Fold quote-like characters to ASCII forms so prime marks (″) and
    # typographic quotes (" " etc.) don't break matches against ASCII " .
    s = _DOUBLE_QUOTE_CHARS.sub('"', s)
    s = _SINGLE_QUOTE_CHARS.sub("'", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    return s


BACKSLASH = chr(92)
QUOTE = chr(34)


def escape_search_term(value: str) -> str:
    """Escape a value for interpolation into a quoted Shopify search term.

    Shopify's search syntax uses double quotes to force an exact match, so a
    SKU that itself contains a double quote (very common here -- 1.25", 2")
    closes the quote early and the term silently matches nothing. Backslash
    is escaped first so it cannot re-escape the quote we add.
    """
    if value is None:
        return ""
    return str(value).replace(BACKSLASH, BACKSLASH + BACKSLASH).replace(QUOTE, BACKSLASH + QUOTE)


class ShopifyClient:
    """Async Shopify Admin GraphQL API client."""

    def __init__(self):
        self.url = config.shopify_graphql_url
        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": config.SHOPIFY_ACCESS_TOKEN,
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def _query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query with automatic rate limit handling."""
        client = await self._get_client()
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(3):
            response = await client.post(self.url, json=payload, headers=self.headers)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "2"))
                logger.warning(f"Rate limited, retrying in {retry_after}s")
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                raise Exception(f"GraphQL error: {data['errors']}")

            # Check remaining query cost
            ext = data.get("extensions", {})
            cost = ext.get("cost", {})
            if cost:
                available = cost.get("throttleStatus", {}).get("currentlyAvailable", 1000)
                if available < 100:
                    await asyncio.sleep(1)

            return data["data"]

        raise Exception("Max retries exceeded for Shopify API")

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ─── PRODUCTS & INVENTORY ────────────────────────────────────

    async def fetch_products_with_inventory(
        self, cursor: Optional[str] = None, limit: int = 50
    ) -> Tuple[List[Dict], Optional[str], bool]:
        """
        Fetch products with their variants, vendor, inventory levels, and cost.
        Returns (products, next_cursor, has_next_page).
        """
        query = """
        query($first: Int!, $after: String) {
            products(first: $first, after: $after, query: "status:active") {
                edges {
                    cursor
                    node {
                        id
                        title
                        vendor
                        productType
                        tags
                        status
                        publishedAt
                        createdAt
                        featuredImage {
                            url(transform: {maxWidth: 80, maxHeight: 80})
                        }
                        variants(first: 20) {
                            edges {
                                node {
                                    id
                                    title
                                    sku
                                    barcode
                                    price
                                    inventoryQuantity
                                    inventoryPolicy
                                    inventoryItem {
                                        id
                                        tracked
                                        unitCost {
                                            amount
                                            currencyCode
                                        }
                                    }
                                    cost_usd: metafield(namespace: "custom", key: "cost_usd") {
                                        value
                                    }
                                    system_code: metafield(namespace: "custom", key: "system_code") {
                                        value
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        variables = {"first": limit}
        if cursor:
            variables["after"] = cursor

        data = await self._query(query, variables)
        edges = data["products"]["edges"]
        has_next = data["products"]["pageInfo"]["hasNextPage"]

        products = []
        last_cursor = None
        for edge in edges:
            last_cursor = edge["cursor"]
            node = edge["node"]
            # Prefer publishedAt (when product became visible on storefront);
            # fall back to createdAt for products that are somehow still null.
            listed_at = node.get("publishedAt") or node.get("createdAt") or ""
            for v_edge in node["variants"]["edges"]:
                v = v_edge["node"]
                inv_item = v.get("inventoryItem", {})
                unit_cost = inv_item.get("unitCost", {})

                # Skip variants with untracked inventory
                tracked = inv_item.get("tracked", True)
                if not tracked:
                    continue

                products.append({
                    "product_id": node["id"],
                    "product_title": node["title"],
                    "vendor": node["vendor"],
                    "product_type": node["productType"],
                    "tags": node.get("tags", []),
                    "image_url": (node.get("featuredImage") or {}).get("url", ""),
                    "listed_at": listed_at,
                    "variant_id": v["id"],
                    "variant_title": v["title"],
                    "sku": v.get("sku", ""),
                    "barcode": v.get("barcode", ""),
                    "price": float(v["price"]),
                    "cost": float(unit_cost.get("amount", 0)) if unit_cost else 0,
                    "inventory_quantity": v["inventoryQuantity"],
                    "inventory_item_id": inv_item.get("id", ""),
                    "cost_usd": (v.get("cost_usd") or {}).get("value", ""),
                    "system_code": (v.get("system_code") or {}).get("value", ""),
                    "inventory_policy": v.get("inventoryPolicy", "DENY"),
                })

        return products, last_cursor, has_next

    async def fetch_all_products(self) -> List[Dict]:
        """Fetch all active products with inventory. Handles pagination."""
        all_products = []
        cursor = None
        page = 0

        while True:
            page += 1
            logger.info(f"Fetching products page {page}...")
            products, cursor, has_next = await self.fetch_products_with_inventory(cursor)
            all_products.extend(products)

            if not has_next:
                break

        logger.info(f"Fetched {len(all_products)} total variants across {page} pages")
        return all_products

    async def fetch_inventory_levels_only(
        self, cursor: Optional[str] = None, limit: int = 100
    ) -> Tuple[List[Dict], Optional[str], bool]:
        """
        Lightweight fetch of just SKU + inventory quantity for all active products.
        Much faster than fetch_products_with_inventory since it skips metadata.
        """
        query = """
        query($first: Int!, $after: String) {
            productVariants(first: $first, after: $after) {
                edges {
                    cursor
                    node {
                        sku
                        inventoryQuantity
                        inventoryItem {
                            tracked
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        variables = {"first": limit}
        if cursor:
            variables["after"] = cursor

        data = await self._query(query, variables)
        edges = data["productVariants"]["edges"]
        has_next = data["productVariants"]["pageInfo"]["hasNextPage"]

        items = []
        last_cursor = None
        for edge in edges:
            last_cursor = edge["cursor"]
            v = edge["node"]
            if not v.get("sku") or not v.get("inventoryItem", {}).get("tracked", True):
                continue
            items.append({
                "sku": v["sku"],
                "inventory_quantity": v["inventoryQuantity"],
            })

        return items, last_cursor, has_next

    async def fetch_all_inventory_levels(self) -> List[Dict]:
        """Fetch all inventory levels (lightweight). For hourly cache refresh."""
        all_items = []
        cursor = None
        page = 0

        while True:
            page += 1
            items, cursor, has_next = await self.fetch_inventory_levels_only(cursor)
            all_items.extend(items)
            if not has_next:
                break

        logger.info(f"Lightweight inventory refresh: {len(all_items)} variants across {page} pages")
        return all_items

    # ─── ORDER HISTORY (for sales velocity) ──────────────────────

    async def fetch_order_line_items(
        self,
        since_date: Optional[datetime] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Tuple[List[Dict], Optional[str], bool]:
        """
        Fetch order line items for sales velocity calculation.
        Returns (line_items, next_cursor, has_next_page).
        """
        if since_date is None:
            since_date = datetime.utcnow() - timedelta(days=365)

        since_str = since_date.strftime("%Y-%m-%dT00:00:00Z")

        query = """
        query($first: Int!, $after: String, $query: String!) {
            orders(first: $first, after: $after, query: $query) {
                edges {
                    cursor
                    node {
                        id
                        name
                        createdAt
                        cancelledAt
                        lineItems(first: 50) {
                            edges {
                                node {
                                    sku
                                    quantity
                                    variant {
                                        id
                                    }
                                    product {
                                        id
                                        vendor
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        query_filter = f"created_at:>'{since_str}' financial_status:paid"
        variables = {"first": limit, "query": query_filter}
        if cursor:
            variables["after"] = cursor

        data = await self._query(query, variables)
        edges = data["orders"]["edges"]
        has_next = data["orders"]["pageInfo"]["hasNextPage"]

        line_items = []
        last_cursor = None
        for edge in edges:
            last_cursor = edge["cursor"]
            order = edge["node"]

            # Skip cancelled orders
            if order.get("cancelledAt"):
                continue

            order_date = datetime.fromisoformat(
                order["createdAt"].replace("Z", "+00:00")
            )

            for li_edge in order["lineItems"]["edges"]:
                li = li_edge["node"]
                if not li.get("sku"):
                    continue
                line_items.append({
                    "order_id": order["id"],
                    "order_name": order["name"],
                    "order_date": order_date,
                    "order_month": order_date.month,
                    "sku": li["sku"],
                    "quantity": li["quantity"],
                    "variant_id": (li.get("variant") or {}).get("id", ""),
                    "product_id": (li.get("product") or {}).get("id", ""),
                    "vendor": (li.get("product") or {}).get("vendor", ""),
                })

        return line_items, last_cursor, has_next

    async def fetch_all_order_line_items(
        self, months: int = 12
    ) -> List[Dict]:
        """Fetch all order line items for the trailing N months."""
        since_date = datetime.utcnow() - timedelta(days=months * 30)
        all_items = []
        cursor = None
        page = 0

        while True:
            page += 1
            logger.info(f"Fetching orders page {page}...")
            items, cursor, has_next = await self.fetch_order_line_items(
                since_date, cursor
            )
            all_items.extend(items)

            if not has_next:
                break

        logger.info(f"Fetched {len(all_items)} line items from {page} pages of orders")
        return all_items

    # ─── ORDERS FOR FIFO (with prices) ───────────────────────────

    async def fetch_orders_for_fifo(
        self,
        since_date: datetime,
        until_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> Dict[str, List[Dict]]:
        """Fetch paid, non-cancelled orders with line item revenue for FIFO matching.

        Returns a dict keyed by order name (e.g. ``"#1234"``) → list of line dicts:
        ``{order_number, order_date, sku, quantity, sale_price, total_revenue, vendor}``.
        ``sale_price`` is the discounted unit price actually paid; ``total_revenue``
        is the discounted line total in shop currency. Refunds are NOT subtracted
        in this iteration — callers should treat revenue as gross of refunds.
        """
        since_str = since_date.strftime("%Y-%m-%dT00:00:00Z")
        query_filter = f"created_at:>'{since_str}' financial_status:paid"
        if until_date is not None:
            until_str = until_date.strftime("%Y-%m-%dT23:59:59Z")
            query_filter += f" created_at:<'{until_str}'"

        gql = """
        query($first: Int!, $after: String, $query: String!) {
            orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
                edges {
                    cursor
                    node {
                        id
                        name
                        createdAt
                        cancelledAt
                        currencyCode
                        lineItems(first: 100) {
                            edges {
                                node {
                                    sku
                                    quantity
                                    originalUnitPriceSet { shopMoney { amount } }
                                    discountedUnitPriceSet { shopMoney { amount } }
                                    discountedTotalSet { shopMoney { amount } }
                                    product { vendor }
                                }
                            }
                        }
                    }
                }
                pageInfo { hasNextPage }
            }
        }
        """

        out: Dict[str, List[Dict]] = {}
        cursor: Optional[str] = None
        page = 0
        while True:
            page += 1
            variables = {"first": limit, "query": query_filter}
            if cursor:
                variables["after"] = cursor
            data = await self._query(gql, variables)

            edges = data["orders"]["edges"]
            has_next = data["orders"]["pageInfo"]["hasNextPage"]

            last_cursor = None
            for edge in edges:
                last_cursor = edge["cursor"]
                order = edge["node"]
                if order.get("cancelledAt"):
                    continue
                order_date = datetime.fromisoformat(
                    order["createdAt"].replace("Z", "+00:00")
                ).date()
                order_number = order["name"]

                lines = []
                for li_edge in order["lineItems"]["edges"]:
                    li = li_edge["node"]
                    raw_sku = li.get("sku")
                    if not raw_sku:
                        continue
                    sku = normalize_sku(raw_sku)
                    qty = int(li.get("quantity") or 0)
                    if qty <= 0:
                        continue
                    discounted_unit = (
                        ((li.get("discountedUnitPriceSet") or {}).get("shopMoney") or {}).get("amount")
                    )
                    original_unit = (
                        ((li.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount")
                    )
                    line_total = (
                        ((li.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount")
                    )
                    unit_price = float(discounted_unit or original_unit or 0)
                    total_revenue = float(line_total) if line_total is not None else unit_price * qty
                    lines.append({
                        "order_number": order_number,
                        "order_date": order_date,
                        "sku": sku,
                        "quantity": qty,
                        "sale_price": unit_price,
                        "total_revenue": total_revenue,
                        "vendor": (li.get("product") or {}).get("vendor", ""),
                    })

                if lines:
                    out[order_number] = lines

            cursor = last_cursor
            if not has_next:
                break

        logger.info(f"Fetched {len(out)} orders for FIFO across {page} pages")
        return out

    # ─── PURCHASE ORDERS (on-order quantities) ───────────────────

    async def fetch_purchase_orders(self) -> List[Dict]:
        """Placeholder — PO data comes from our own database."""
        return []

    # ─── INVENTORY MANAGEMENT ────────────────────────────────────

    async def get_receiving_location_id(self) -> str:
        """Location that received stock is added to.

        This used to be ``locations(first: 1)`` — whatever Shopify happened to
        return first. When the "Starfest" event location was created it sorted
        ahead of the warehouse, so every receive silently added stock to
        Starfest instead. Now the location is resolved explicitly:

          1. exact name match on ``config.RECEIVING_LOCATION_NAME``
             (default "Telescopes Canada Warehouse"), else
          2. the active location that ships inventory / fulfils online orders
             — event locations like Starfest have both flags off, else
          3. the first active location, with a loud warning.

        Resolved once per process and cached; location IDs don't change.
        """
        if getattr(self, "_receiving_location_id", None):
            return self._receiving_location_id

        query = """
        query {
            locations(first: 50) {
                edges {
                    node {
                        id
                        name
                        isActive
                        shipsInventory
                        fulfillsOnlineOrders
                    }
                }
            }
        }
        """
        data = await self._query(query)
        nodes = [e["node"] for e in (data.get("locations") or {}).get("edges", [])]
        if not nodes:
            raise Exception("No locations found in Shopify")

        wanted = (config.RECEIVING_LOCATION_NAME or "").strip().lower()
        chosen = None

        if wanted:
            chosen = next(
                (n for n in nodes if (n.get("name") or "").strip().lower() == wanted),
                None,
            )
            if chosen:
                logger.info(
                    "Receiving location: %s (%s) — matched configured name",
                    chosen["name"], chosen["id"],
                )

        if not chosen:
            chosen = next(
                (n for n in nodes
                 if n.get("isActive") and (n.get("shipsInventory") or n.get("fulfillsOnlineOrders"))),
                None,
            )
            if chosen:
                logger.warning(
                    "Receiving location '%s' not found; falling back to '%s' (%s) "
                    "because it ships inventory. Set SHOPIFY_RECEIVING_LOCATION to "
                    "silence this.",
                    config.RECEIVING_LOCATION_NAME, chosen["name"], chosen["id"],
                )

        if not chosen:
            chosen = next((n for n in nodes if n.get("isActive")), nodes[0])
            logger.error(
                "Could not identify a shipping location — defaulting to '%s' (%s). "
                "Received stock may land in the wrong place; check "
                "SHOPIFY_RECEIVING_LOCATION.",
                chosen["name"], chosen["id"],
            )

        self._receiving_location_id = chosen["id"]
        return self._receiving_location_id

    # Backwards-compatible alias — the old name implied "whichever is first",
    # which is exactly the bug. Kept so any stray caller still works.
    async def get_primary_location_id(self) -> str:
        return await self.get_receiving_location_id()

    async def lookup_inventory_by_skus(
        self, skus: List[str]
    ) -> Dict[str, Dict]:
        """
        Look up current inventory levels and inventory_item_ids for a list of SKUs.
        Returns: {sku: {inventory_item_id, available, variant_id, title}}
        """
        results = {}

        # Process in batches of 10 SKUs (Shopify query limit)
        for i in range(0, len(skus), 10):
            batch = skus[i:i + 10]
            # Build case-insensitive + normalized lookup: normalized_lower -> original SKU
            batch_lookup = {normalize_sku(s).lower(): s for s in batch}
            # Also keep plain lowercase for exact case-insensitive match
            batch_lower = {s.lower(): s for s in batch}
            # Quote SKUs to force exact match — without quotes, Shopify tokenizes
            # on hyphens and special chars, causing partial/failed matches
            sku_query = " OR ".join(
                f'sku:"{escape_search_term(sku)}"' for sku in batch)
            # Also search with normalized versions in case Shopify has the ASCII form
            normalized_batch = [normalize_sku(s) for s in batch]
            extra_skus = [n for n in normalized_batch if n not in batch]
            if extra_skus:
                sku_query += " OR " + " OR ".join(
                    f'sku:"{escape_search_term(sku)}"' for sku in extra_skus)

            query = """
            query($query: String!) {
                productVariants(first: 50, query: $query) {
                    edges {
                        node {
                            id
                            sku
                            title
                            displayName
                            inventoryQuantity
                            product {
                                id
                            }
                            inventoryItem {
                                id
                                countryCodeOfOrigin
                            }
                        }
                    }
                }
            }
            """
            data = await self._query(query, {"query": sku_query})
            for edge in data["productVariants"]["edges"]:
                v = edge["node"]
                shopify_sku = v.get("sku", "")
                if not shopify_sku:
                    continue
                # Match case-insensitively, then try normalized match
                original_sku = batch_lower.get(shopify_sku.lower())
                if not original_sku:
                    # Try normalized match: normalize the Shopify SKU and compare
                    original_sku = batch_lookup.get(normalize_sku(shopify_sku).lower())
                if original_sku:
                    inv_item = v.get("inventoryItem") or {}
                    results[original_sku] = {
                        "inventory_item_id": inv_item.get("id"),
                        "variant_id": v["id"],
                        "product_id": v["product"]["id"],
                        "available": v["inventoryQuantity"],
                        "title": v.get("displayName", v.get("title", "")),
                        "shopify_sku": shopify_sku,  # actual casing in Shopify
                        "country_code_of_origin": inv_item.get("countryCodeOfOrigin"),
                    }

            if i + 10 < len(skus):
                await asyncio.sleep(0.3)

        return results

    async def lookup_inventory_and_metafields_by_skus(
        self, skus: List[str]
    ) -> Dict[str, Dict]:
        """
        Same as lookup_inventory_by_skus, but also fetches current values of the
        cost_usd and system_code metafields so the UI can preview before/after.
        Returns: {sku: {..., current_metafields: {cost_usd: "...", system_code: "..."}}}
        """
        results = {}

        for i in range(0, len(skus), 10):
            batch = skus[i:i + 10]
            batch_lookup = {normalize_sku(s).lower(): s for s in batch}
            batch_lower = {s.lower(): s for s in batch}
            sku_query = " OR ".join(
                f'sku:"{escape_search_term(sku)}"' for sku in batch)
            normalized_batch = [normalize_sku(s) for s in batch]
            extra_skus = [n for n in normalized_batch if n not in batch]
            if extra_skus:
                sku_query += " OR " + " OR ".join(
                    f'sku:"{escape_search_term(sku)}"' for sku in extra_skus)

            query = """
            query($query: String!) {
                productVariants(first: 50, query: $query) {
                    edges {
                        node {
                            id
                            sku
                            title
                            displayName
                            inventoryQuantity
                            product { id }
                            inventoryItem { id }
                            cost_usd: metafield(namespace: "custom", key: "cost_usd") { value }
                            system_code: metafield(namespace: "custom", key: "system_code") { value }
                        }
                    }
                }
            }
            """
            data = await self._query(query, {"query": sku_query})
            for edge in data["productVariants"]["edges"]:
                v = edge["node"]
                shopify_sku = v.get("sku", "")
                if not shopify_sku:
                    continue
                original_sku = batch_lower.get(shopify_sku.lower())
                if not original_sku:
                    original_sku = batch_lookup.get(normalize_sku(shopify_sku).lower())
                if original_sku:
                    current_mf = {}
                    if v.get("cost_usd"):
                        current_mf["cost_usd"] = v["cost_usd"].get("value", "")
                    if v.get("system_code"):
                        current_mf["system_code"] = v["system_code"].get("value", "")
                    results[original_sku] = {
                        "inventory_item_id": v["inventoryItem"]["id"],
                        "variant_id": v["id"],
                        "product_id": v["product"]["id"],
                        "available": v["inventoryQuantity"],
                        "title": v.get("displayName", v.get("title", "")),
                        "shopify_sku": shopify_sku,
                        "current_metafields": current_mf,
                    }

            if i + 10 < len(skus):
                await asyncio.sleep(0.3)

        return results

    async def adjust_inventory(
        self,
        adjustments: List[Dict],
        location_id: str,
        reason: str = "received",
    ) -> List[Dict]:
        """
        Adjust inventory quantities in Shopify.
        adjustments: [{"inventory_item_id": "gid://...", "delta": 5, "sku": "ABC"}, ...]
        Returns results with success/error per item.
        """
        results = []

        # Process one at a time to track individual success/failure
        for adj in adjustments:
            try:
                mutation = """
                mutation($input: InventoryAdjustQuantitiesInput!) {
                    inventoryAdjustQuantities(input: $input) {
                        userErrors {
                            field
                            message
                        }
                        inventoryAdjustmentGroup {
                            changes {
                                name
                                delta
                                quantityAfterChange
                            }
                        }
                    }
                }
                """
                variables = {
                    "input": {
                        "reason": reason,
                        "name": "available",
                        "changes": [
                            {
                                "inventoryItemId": adj["inventory_item_id"],
                                "locationId": location_id,
                                "delta": adj["delta"],
                            }
                        ],
                    }
                }

                data = await self._query(mutation, variables)
                result = data["inventoryAdjustQuantities"]
                errors = result.get("userErrors", [])

                if errors:
                    results.append({
                        "sku": adj.get("sku", ""),
                        "success": False,
                        "error": errors[0].get("message", "Unknown error"),
                    })
                else:
                    changes = result.get("inventoryAdjustmentGroup", {}).get("changes", [])
                    qty_after = changes[0]["quantityAfterChange"] if changes else None
                    results.append({
                        "sku": adj.get("sku", ""),
                        "success": True,
                        "delta": adj["delta"],
                        "quantity_after": qty_after,
                    })

            except Exception as e:
                results.append({
                    "sku": adj.get("sku", ""),
                    "success": False,
                    "error": str(e),
                })

            await asyncio.sleep(0.2)  # Rate limit courtesy

        return results


    # ─── METAFIELD MANAGEMENT ────────────────────────────────────

    async def set_variant_metafields(
        self, variant_id: str, metafields: Dict[str, str]
    ) -> Dict:
        """
        Set metafields on a product variant using metafieldsSet (2025-01 API).
        metafields: {"cost_usd": "12.50", "system_code": "ABC123"}
        """
        mutation = """
        mutation($metafields: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $metafields) {
                metafields {
                    namespace
                    key
                    value
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        mf_list = []
        for key, value in metafields.items():
            mf_list.append({
                "namespace": "custom",
                "key": key,
                "value": str(value),
                "type": "single_line_text_field",
                "ownerId": variant_id,
            })

        data = await self._query(mutation, {"metafields": mf_list})
        result = data["metafieldsSet"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Metafield error: {errors[0].get('message', 'Unknown')}")
        return result

    # ─── stock.bin metafield (variant bin location) ──────────────────
    # Telescopes Canada stores each variant's warehouse bin location in a
    # `stock.bin` variant metafield (namespace "stock", key "bin"). The PO
    # receiving screen reads + writes it so staff can see/update where a
    # product lives without leaving TC-Planner.

    async def get_variant_bins_by_skus(self, skus: List[str]) -> Dict[str, str]:
        """Batch-read the `stock.bin` metafield for a list of SKUs.

        Returns {sku: bin_value} keyed by the ORIGINAL input SKU casing.
        SKUs with no variant match or no bin set are simply absent from
        the result (caller treats missing as empty).
        """
        results: Dict[str, str] = {}
        clean = [s for s in skus if s]
        for i in range(0, len(clean), 50):
            batch = clean[i:i + 50]
            # Map Shopify-cased SKU back to our input casing.
            by_upper = {s.upper(): s for s in batch}
            sku_query = " OR ".join(
                'sku:"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
                for s in batch
            )
            query = """
            query($query: String!) {
                productVariants(first: 250, query: $query) {
                    edges {
                        node {
                            sku
                            bin: metafield(namespace: "stock", key: "bin") { value }
                        }
                    }
                }
            }
            """
            try:
                data = await self._query(query, {"query": sku_query})
            except Exception as e:
                logger.warning("get_variant_bins_by_skus batch failed: %s", e)
                continue
            for edge in (data.get("productVariants", {}) or {}).get("edges", []):
                v = edge.get("node") or {}
                vsku = v.get("sku")
                if not vsku:
                    continue
                original = by_upper.get(vsku.upper())
                if not original:
                    continue
                bin_mf = v.get("bin") or {}
                bin_val = bin_mf.get("value")
                if bin_val:
                    results[original] = bin_val
        return results

    async def set_variant_stock_bin(self, variant_id: str, value: str) -> Dict:
        """Set (or clear) the `stock.bin` metafield on a single variant.

        ``variant_id`` must be the full GID (gid://shopify/ProductVariant/...).
        An empty/whitespace value clears the metafield via metafieldsDelete;
        a non-empty value upserts it via metafieldsSet.
        """
        value = (value or "").strip()

        if not value:
            # Clear: find the metafield id, then delete it. If it doesn't
            # exist this is a no-op success.
            find_q = """
            query($id: ID!) {
                productVariant(id: $id) {
                    metafield(namespace: "stock", key: "bin") { id }
                }
            }
            """
            data = await self._query(find_q, {"id": variant_id})
            mf = ((data.get("productVariant") or {}).get("metafield") or {})
            mf_id = mf.get("id")
            if not mf_id:
                return {"cleared": True, "noop": True}
            del_mutation = """
            mutation($input: [MetafieldIdentifierInput!]!) {
                metafieldsDelete(metafields: $input) {
                    deletedMetafields { key }
                    userErrors { field message }
                }
            }
            """
            # metafieldsDelete takes owner+namespace+key identifiers
            del_data = await self._query(del_mutation, {
                "input": [{
                    "ownerId": variant_id,
                    "namespace": "stock",
                    "key": "bin",
                }],
            })
            errs = (del_data.get("metafieldsDelete") or {}).get("userErrors") or []
            if errs:
                raise Exception("Bin clear error: %s" % errs[0].get("message", "Unknown"))
            return {"cleared": True}

        mutation = """
        mutation($metafields: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $metafields) {
                metafields { namespace key value }
                userErrors { field message }
            }
        }
        """
        data = await self._query(mutation, {
            "metafields": [{
                "namespace": "stock",
                "key": "bin",
                "value": value,
                "type": "single_line_text_field",
                "ownerId": variant_id,
            }],
        })
        result = data["metafieldsSet"]
        errs = result.get("userErrors", [])
        if errs:
            raise Exception("Bin set error: %s" % errs[0].get("message", "Unknown"))
        return result

    async def set_variant_barcode(self, variant_id: str, product_id: str,
                                   value: str) -> Dict:
        """Set (or clear) the native ``barcode`` field on a single variant.

        Unlike the bin (a metafield), barcode is a first-class variant field,
        so it's written via productVariantsBulkUpdate — the same mutation
        create_draft_product uses. That mutation requires the owning product's
        GID alongside the variant GID. An empty/whitespace value clears the
        barcode (Shopify accepts an empty string to unset it).
        """
        value = (value or "").strip()
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id barcode }
                userErrors { field message }
            }
        }
        """
        data = await self._query(mutation, {
            "productId": product_id,
            "variants": [{"id": variant_id, "barcode": value}],
        })
        result = data["productVariantsBulkUpdate"]
        errs = result.get("userErrors", [])
        if errs:
            raise Exception("Barcode update error: %s" % errs[0].get("message", "Unknown"))
        return result

    async def bulk_set_metafields_by_sku(
        self, sku_data: List[Dict]
    ) -> List[Dict]:
        """
        Bulk set metafields on variants by SKU.
        sku_data: [{"sku": "ABC", "cost_usd": "12.50", "system_code": "XYZ"}, ...]
        Returns results per SKU.
        """
        # First look up variant IDs for all SKUs (normalize supplier SKUs)
        skus = [d["sku"] for d in sku_data if d.get("sku")]
        # Normalize supplier SKUs for lookup, keeping mapping back to original
        normalized_map = {}  # normalized -> original supplier sku
        lookup_skus = []
        for s in skus:
            n = normalize_sku(s)
            normalized_map[n] = s
            lookup_skus.append(n)
        inventory_data = await self.lookup_inventory_by_skus(lookup_skus)

        results = []
        for item in sku_data:
            sku = item.get("sku", "")
            # Look up by normalized SKU (inventory_data is keyed by normalized values)
            shopify_info = inventory_data.get(normalize_sku(sku)) or inventory_data.get(sku)

            if not shopify_info:
                results.append({"sku": sku, "success": False, "error": "SKU not found in Shopify"})
                continue

            variant_id = shopify_info["variant_id"]
            metafields = {}
            if "cost_usd" in item and item["cost_usd"]:
                metafields["cost_usd"] = item["cost_usd"]
            if "system_code" in item and item["system_code"]:
                metafields["system_code"] = item["system_code"]

            if not metafields:
                results.append({"sku": sku, "success": True, "message": "No metafields to set"})
                continue

            try:
                await self.set_variant_metafields(variant_id, metafields)
                results.append({"sku": sku, "success": True, "metafields": metafields})
            except Exception as e:
                results.append({"sku": sku, "success": False, "error": str(e)})

            await asyncio.sleep(0.2)

        return results

    # ─── SKU CORRECTION ──────────────────────────────────────────

    def _generate_sku_variations(self, sku: str) -> List[str]:
        """Generate possible SKU variations to try fuzzy matching."""
        variations = set()

        # Try normalized version (Unicode Roman numerals → ASCII, fancy dashes → hyphen)
        normalized = normalize_sku(sku)
        if normalized != sku:
            variations.add(normalized)

        # Work with normalized version for further variations
        base = normalized

        # Try replacing first space with dash and vice versa
        if " " in base:
            variations.add(base.replace(" ", "-", 1))
        if "-" in base:
            variations.add(base.replace("-", " ", 1))
        # Try with/without dash after common vendor prefixes
        prefixes = ["ZWO", "ASI", "SV", "SW", "IP"]
        for prefix in prefixes:
            if base.upper().startswith(prefix):
                rest = base[len(prefix):]
                if rest.startswith("-"):
                    variations.add(prefix + rest[1:])       # remove dash
                    variations.add(prefix + " " + rest[1:]) # dash -> space
                elif rest.startswith(" "):
                    variations.add(prefix + "-" + rest[1:]) # space -> dash
                    variations.add(prefix + rest[1:])       # remove space
                else:
                    variations.add(prefix + "-" + rest)     # add dash
                    variations.add(prefix + " " + rest)     # add space
        # Remove the original from variations
        variations.discard(sku)
        return list(variations)

    async def find_sku_mismatches(
        self, skus: List[str]
    ) -> List[Dict]:
        """
        For each SKU, check if it exists in Shopify. If not, try variations
        to find a fuzzy match (e.g., extra dash). Returns match results.
        """
        # First try exact match for all
        exact_matches = await self.lookup_inventory_by_skus(skus)

        results = []
        unmatched_skus = []

        for sku in skus:
            if sku in exact_matches:
                results.append({
                    "csv_sku": sku,
                    "shopify_sku": sku,
                    "variant_id": exact_matches[sku]["variant_id"],
                    "product_id": exact_matches[sku].get("product_id", ""),
                    "inventory_item_id": exact_matches[sku].get("inventory_item_id", ""),
                    "title": exact_matches[sku].get("title", ""),
                    "match_type": "exact",
                    "needs_correction": False,
                })
            else:
                unmatched_skus.append(sku)

        # For unmatched, try variations
        for sku in unmatched_skus:
            variations = self._generate_sku_variations(sku)
            if not variations:
                results.append({
                    "csv_sku": sku, "shopify_sku": None, "variant_id": None,
                    "product_id": None, "inventory_item_id": None,
                    "title": "", "match_type": "not_found", "needs_correction": False,
                })
                continue

            fuzzy_matches = await self.lookup_inventory_by_skus(variations)

            found = False
            for var_sku, info in fuzzy_matches.items():
                results.append({
                    "csv_sku": sku,
                    "shopify_sku": var_sku,
                    "variant_id": info["variant_id"],
                    "product_id": info.get("product_id", ""),
                    "inventory_item_id": info.get("inventory_item_id", ""),
                    "title": info.get("title", ""),
                    "match_type": "fuzzy",
                    "needs_correction": True,
                })
                found = True
                break  # Take first match

            if not found:
                results.append({
                    "csv_sku": sku, "shopify_sku": None, "variant_id": None,
                    "product_id": None, "inventory_item_id": None,
                    "title": "", "match_type": "not_found", "needs_correction": False,
                })

            await asyncio.sleep(0.2)

        return results

    async def update_variant_sku(self, inventory_item_id: str, new_sku: str) -> Dict:
        """Update a variant's SKU via inventoryItemUpdate (SKU lives on InventoryItem since 2024-04)."""
        mutation = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem {
                    id
                    sku
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "id": inventory_item_id,
            "input": {"sku": new_sku},
        }
        data = await self._query(mutation, variables)
        result = data["inventoryItemUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"SKU update error: {errors[0].get('message', 'Unknown')}")
        return result

    async def update_unit_cost(self, inventory_item_id: str, cost: float, currency: str = "CAD") -> dict:
        """Update a variant's unit cost via inventoryItemUpdate.

        ``cost`` is the ``Decimal`` scalar on ``InventoryItemInput`` — Shopify
        accepts a string here reliably; passing a JSON number has been
        observed to silently no-op (no userErrors, but the value doesn't
        change). We send it as a 2-decimal string and then verify the
        response actually reflects the requested value, raising on a
        silent no-op."""
        mutation = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem {
                    id
                    unitCost { amount currencyCode }
                }
                userErrors { field message }
            }
        }
        """
        target_cost = round(float(cost), 2)
        variables = {
            "id": inventory_item_id,
            "input": {"cost": f"{target_cost:.2f}"},
        }
        data = await self._query(mutation, variables)
        result = data["inventoryItemUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Cost update error: {errors[0].get('message', 'Unknown')}")

        # Verify Shopify actually applied the value. If it returned with
        # something different, surface a clear error rather than letting the
        # cache record success.
        inv = (result.get("inventoryItem") or {})
        applied = (inv.get("unitCost") or {}).get("amount")
        try:
            applied_f = float(applied) if applied is not None else None
        except (TypeError, ValueError):
            applied_f = None
        if applied_f is None or abs(applied_f - target_cost) > 0.01:
            raise Exception(
                f"Shopify did not apply cost update: requested {target_cost}, "
                f"got {applied!r}. (The cost update silently failed; "
                f"the item may be untracked, archived, or otherwise locked.)"
            )
        return result

    async def update_country_of_origin(self, inventory_item_id: str,
                                        country_code: str) -> dict:
        """Set a variant's Country of Origin via inventoryItemUpdate.

        ``country_code`` must be an ISO-3166-1 alpha-2 code (e.g. 'CN'); it
        maps to InventoryItemInput.countryCodeOfOrigin (a CountryCode enum).
        Shopify rejects an unknown code with a userError, which we surface."""
        code = (country_code or "").strip().upper()
        if not code:
            raise Exception("Country of origin code is required")
        mutation = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem {
                    id
                    countryCodeOfOrigin
                }
                userErrors { field message }
            }
        }
        """
        variables = {
            "id": inventory_item_id,
            "input": {"countryCodeOfOrigin": code},
        }
        data = await self._query(mutation, variables)
        result = data["inventoryItemUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Country of origin update error: {errors[0].get('message', 'Unknown')}")
        applied = (result.get("inventoryItem") or {}).get("countryCodeOfOrigin")
        if applied != code:
            raise Exception(
                f"Shopify did not apply COO update: requested {code}, got {applied!r}."
            )
        return result

    async def update_variant_price(self, product_id: str, variant_id: str, price: float) -> dict:
        """Update a variant's selling price via productVariantsBulkUpdate."""
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    price
                }
                userErrors { field message }
            }
        }
        """
        variables = {
            "productId": product_id,
            "variants": [{
                "id": variant_id,
                "price": str(round(price, 2)),
            }],
        }
        data = await self._query(mutation, variables)
        result = data["productVariantsBulkUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Price update error: {errors[0].get('message', 'Unknown')}")
        return result

    async def update_variant_price_with_compare(self, product_id: str,
                                                  variant_id: str,
                                                  price: float,
                                                  compare_at_price: float = None) -> dict:
        """Update a variant's price and compareAtPrice in Shopify.
        Used by Sales Manager to set sale prices (with original price as compareAt)
        and to revert them (clearing compareAt).
        """
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    price
                    compareAtPrice
                }
                userErrors { field message }
            }
        }
        """
        variant_input = {
            "id": variant_id,
            "price": str(round(price, 2)),
        }
        if compare_at_price is not None:
            variant_input["compareAtPrice"] = str(round(compare_at_price, 2))
        else:
            variant_input["compareAtPrice"] = None

        variables = {
            "productId": product_id,
            "variants": [variant_input],
        }
        data = await self._query(mutation, variables)
        result = data["productVariantsBulkUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Price/compare update error: {errors[0].get('message', 'Unknown')}")
        return result

    async def update_variant_sku(self, product_id: str, variant_id: str, new_sku: str) -> dict:
        """Update a variant's SKU in Shopify. SKU lives on the InventoryItem
        in the GraphQL admin API, so we set it via inventoryItem.sku on the
        productVariantsBulkUpdate input."""
        new_sku = (new_sku or "").strip()
        if not new_sku:
            raise ValueError("new_sku is required")
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    sku
                    inventoryItem { id sku }
                }
                userErrors { field message }
            }
        }
        """
        variables = {
            "productId": product_id,
            "variants": [{
                "id": variant_id,
                "inventoryItem": {"sku": new_sku},
            }],
        }
        data = await self._query(mutation, variables)
        result = data["productVariantsBulkUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"SKU update error: {errors[0].get('message', 'Unknown')}")
        variants = result.get("productVariants") or []
        applied_sku = ""
        if variants:
            applied_sku = variants[0].get("sku") or ""
            if not applied_sku:
                inv = variants[0].get("inventoryItem") or {}
                applied_sku = inv.get("sku") or ""
        return {"variant_id": variant_id, "sku": applied_sku or new_sku}

    async def set_product_status(self, product_id: str, status: str = "DRAFT") -> dict:
        """Set a product's status (ACTIVE, DRAFT, ARCHIVED) via productUpdate."""
        mutation = """
        mutation productUpdate($product: ProductUpdateInput!) {
            productUpdate(product: $product) {
                product { id status }
                userErrors { field message }
            }
        }
        """
        variables = {
            "product": {"id": product_id, "status": status},
        }
        data = await self._query(mutation, variables)
        result = data["productUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Status update error: {errors[0].get('message', 'Unknown')}")
        return result

    async def set_inventory_policy(self, product_id: str, variant_id: str, policy: str = "DENY") -> dict:
        """Set a variant's inventory policy (DENY = stop selling when OOS, CONTINUE = keep selling)."""
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id }
                userErrors { field message }
            }
        }
        """
        variables = {
            "productId": product_id,
            "variants": [{"id": variant_id, "inventoryPolicy": policy}],
        }
        data = await self._query(mutation, variables)
        result = data["productVariantsBulkUpdate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Inventory policy error: {errors[0].get('message', 'Unknown')}")
        return result

    async def add_tags_to_product(self, product_id: str, tags: list) -> dict:
        """Add tags to a product using the tagsAdd mutation."""
        query = """
        mutation tagsAdd($id: ID!, $tags: [String!]!) {
            tagsAdd(id: $id, tags: $tags) {
                node { ... on Product { id tags } }
                userErrors { field message }
            }
        }
        """
        data = await self._query(query, {"id": product_id, "tags": tags})
        result = data["tagsAdd"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Tag add error: {errors[0].get('message', 'Unknown')}")
        return result

    async def remove_tags_from_product(self, product_id: str, tags: list) -> dict:
        """Remove tags from a product using the tagsRemove mutation."""
        query = """
        mutation tagsRemove($id: ID!, $tags: [String!]!) {
            tagsRemove(id: $id, tags: $tags) {
                node { ... on Product { id tags } }
                userErrors { field message }
            }
        }
        """
        data = await self._query(query, {"id": product_id, "tags": tags})
        result = data["tagsRemove"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Tag remove error: {errors[0].get('message', 'Unknown')}")
        return result

    async def create_draft_product(self, title: str, vendor: str, sku: str,
                                    price: float, cost: float, description: str = "",
                                    product_type: str = "", tags: list = None,
                                    barcode: str = None) -> dict:
        """Create a draft product in Shopify, populated with the given fields,
        and publish it to ALL of the store's sales channels.

        ``barcode`` (optional) is set on the variant via productVariantsBulkUpdate
        so the UPC/EAN appears in Shopify's Barcode field.

        Note that even though the product status is DRAFT, we still publish
        it to every Online Store / POS / etc. publication so that when the
        operator flips status → ACTIVE, the product is immediately live on
        every channel without needing to set channel checkboxes manually.
        """
        # Step 1: Create the product
        mutation = """
        mutation productCreate($product: ProductCreateInput!) {
            productCreate(product: $product) {
                product {
                    id
                    title
                    variants(first: 1) { edges { node { id } } }
                }
                userErrors { field message }
            }
        }
        """
        product_input = {
            "title": title,
            "vendor": vendor,
            "status": "DRAFT",
            "descriptionHtml": description,
        }
        if product_type:
            product_input["productType"] = product_type
        if tags:
            product_input["tags"] = tags

        data = await self._query(mutation, {"product": product_input})
        result = data["productCreate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception("Product create error: %s" % errors[0].get("message", "Unknown"))

        product = result["product"]
        product_id = product["id"]
        variant_id = product["variants"]["edges"][0]["node"]["id"]

        # Step 2: Set variant details (price, barcode) via productVariantsBulkUpdate.
        # Barcode lives on the variant in Shopify's data model, not the product.
        variant_mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id price barcode }
                userErrors { field message }
            }
        }
        """
        # inventoryPolicy CONTINUE = "Sell when out of stock" is ON. New drafts
        # represent products with no stock on hand yet (tagged istock-preorder),
        # so customers must be able to place orders against them — otherwise the
        # storefront would block the sale the moment stock hits zero (which is
        # always, for a fresh draft). Shopify defaults new variants to DENY.
        variant_input = {
            "id": variant_id,
            "price": str(round(price, 2)),
            "inventoryPolicy": "CONTINUE",
        }
        if barcode:
            variant_input["barcode"] = str(barcode).strip()
        await self._query(variant_mutation, {
            "productId": product_id,
            "variants": [variant_input],
        })

        # Step 3: Set SKU and cost via inventoryItemUpdate
        # First get the inventory item ID
        inv_query = """
        query getVariant($id: ID!) {
            productVariant(id: $id) {
                inventoryItem { id }
            }
        }
        """
        inv_data = await self._query(inv_query, {"id": variant_id})
        inv_item_id = inv_data["productVariant"]["inventoryItem"]["id"]

        # Set SKU + cost AND turn on inventory tracking. Drafts are created
        # with `tracked: false` by default in Shopify, which means the
        # storefront would never display "out of stock" or decrement on
        # purchase. Telescopes Canada always wants tracking on so backorder
        # workflows, replenishment forecasts, and the "More on the Way"
        # tagger see consistent on-hand numbers.
        await self._query("""
            mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
                inventoryItemUpdate(id: $id, input: $input) {
                    inventoryItem { id sku tracked }
                    userErrors { field message }
                }
            }
        """, {
            "id": inv_item_id,
            "input": {
                "sku": sku,
                "cost": round(cost, 2),
                "tracked": True,
            },
        })

        # Step 4: Publish the new product to every available sales channel.
        # publishablePublish takes a list of {publicationId} pairs; we fetch
        # the store's full publication list (Online Store, POS, Google,
        # Facebook & Instagram, Shop, etc.) and include each one.
        publications_published = 0
        publication_error = None
        try:
            publications_published = await self._publish_to_all_channels(product_id)
        except Exception as e:
            publication_error = str(e)
            logger.warning(
                "Draft created but publish-to-all-channels failed for product %s: %s",
                product_id, e,
            )

        return {
            "product_id": product_id,
            "variant_id": variant_id,
            "inventory_item_id": inv_item_id,
            "publications_published": publications_published,
            "publication_error": publication_error,
        }

    async def _publish_to_all_channels(self, product_id: str) -> int:
        """Publish ``product_id`` to every publication (sales channel) on the
        store. Returns the number of channels successfully published to.

        Raises descriptive exceptions on failure so the caller can surface
        the underlying cause (typically a missing OAuth scope) to the user.

        Notes:
          * Requires ``read_publications`` + ``write_publications`` scopes
            on the Shopify Admin API access token. Custom apps installed
            before May 2024 often pre-date these scopes and need a
            re-install with updated permissions.
          * Draft products are validly associated with publications even
            though they aren't visible on the storefront until ACTIVE.
            The Admin UI's "Publishing" panel may still say "Not included
            in any sales channels" while the product is DRAFT — that's a
            display behavior, not a missing association.
        """
        pub_query = """
        query publications {
            publications(first: 50) {
                edges { node { id name } }
            }
        }
        """
        try:
            pub_data = await self._query(pub_query, {})
        except Exception as e:
            msg = (
                "Failed to query publications. The Shopify Admin token is "
                "likely missing the `read_publications` scope. Re-install "
                "the custom app with updated scopes, or grant publications "
                "scopes in the app's API access settings. Underlying: %s"
                % e
            )
            logger.error(msg)
            raise Exception(msg)

        pub_edges = ((pub_data.get("publications") or {}).get("edges") or [])
        logger.info("Found %d publication(s) on the store", len(pub_edges))
        if not pub_edges:
            return 0

        publish_inputs = []
        pub_names = []
        for edge in pub_edges:
            node = edge.get("node") or {}
            pub_id = node.get("id")
            if pub_id:
                publish_inputs.append({"publicationId": pub_id})
                pub_names.append(node.get("name") or pub_id)
        if not publish_inputs:
            return 0

        publish_mutation = """
        mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
            publishablePublish(id: $id, input: $input) {
                publishable { ... on Product { id } }
                userErrors { field message }
            }
        }
        """
        try:
            result = await self._query(publish_mutation, {
                "id": product_id, "input": publish_inputs,
            })
        except Exception as e:
            msg = (
                "publishablePublish failed. Token is likely missing the "
                "`write_publications` scope. Underlying: %s" % e
            )
            logger.error(msg + " (product_id=%s)", product_id)
            raise Exception(msg)

        errs = (result.get("publishablePublish") or {}).get("userErrors") or []
        if errs:
            err_str = "; ".join(
                "%s: %s" % (e.get("field"), e.get("message")) for e in errs
            )
            logger.warning("publishablePublish userErrors for %s: %s",
                           product_id, err_str)
            raise Exception("publishablePublish userErrors: " + err_str)
        logger.info(
            "Published product %s to %d channel(s): %s",
            product_id, len(publish_inputs), ", ".join(pub_names),
        )
        return len(publish_inputs)

    # ─── COLLECTION MANAGEMENT ───────────────────────────────────

    async def create_manual_collection(self, title: str) -> str:
        """Create a manual (custom) collection in Shopify. Returns the collection GID."""
        mutation = """
        mutation collectionCreate($input: CollectionInput!) {
            collectionCreate(input: $input) {
                collection { id title }
                userErrors { field message }
            }
        }
        """
        variables = {
            "input": {
                "title": title,
            }
        }
        data = await self._query(mutation, variables)
        result = data["collectionCreate"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Collection create error: {errors[0].get('message', 'Unknown')}")
        return result["collection"]["id"]

    async def add_products_to_collection(self, collection_id: str, product_ids: list) -> dict:
        """Add products to a manual collection using collectionAddProducts."""
        mutation = """
        mutation collectionAddProducts($id: ID!, $productIds: [ID!]!) {
            collectionAddProducts(id: $id, productIds: $productIds) {
                collection { id productsCount { count } }
                userErrors { field message }
            }
        }
        """
        # Shopify limits to 250 products per call
        total_added = 0
        for i in range(0, len(product_ids), 250):
            batch = product_ids[i:i+250]
            variables = {"id": collection_id, "productIds": batch}
            data = await self._query(mutation, variables)
            result = data["collectionAddProducts"]
            errors = result.get("userErrors", [])
            if errors:
                raise Exception(f"Collection add products error: {errors[0].get('message', 'Unknown')}")
            total_added += len(batch)
        return {"added": total_added}

    async def delete_collection(self, collection_id: str) -> dict:
        """Delete a collection from Shopify."""
        mutation = """
        mutation collectionDelete($input: CollectionDeleteInput!) {
            collectionDelete(input: $input) {
                deletedCollectionId
                userErrors { field message }
            }
        }
        """
        variables = {"input": {"id": collection_id}}
        data = await self._query(mutation, variables)
        result = data["collectionDelete"]
        errors = result.get("userErrors", [])
        if errors:
            raise Exception(f"Collection delete error: {errors[0].get('message', 'Unknown')}")
        return {"deleted": result.get("deletedCollectionId")}


# Singleton instance
shopify_client = ShopifyClient()
