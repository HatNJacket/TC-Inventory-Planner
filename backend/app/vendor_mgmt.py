"""
TC Inventory Planner - Vendor Management Module
Vendor settings, pricelist comparison, FX rates, and draft product creation.
"""
import csv
import io
import logging
from decimal import Decimal
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _f(val):
    if val is None: return 0.0
    if isinstance(val, Decimal): return float(val)
    return float(val)


# Common full-name → ISO-3166 alpha-2 aliases seen in vendor pricelists.
# Most lists (e.g. Baader) already use 2-letter codes, but a few spell the
# country out. Anything not resolvable to a 2-letter code is dropped (we'd
# rather store nothing than push an invalid code to Shopify).
_COO_ALIASES = {
    'USA': 'US', 'UNITED STATES': 'US', 'U.S.': 'US', 'U.S.A.': 'US',
    'CHINA': 'CN', 'PRC': 'CN', 'TAIWAN': 'TW', 'JAPAN': 'JP',
    'GERMANY': 'DE', 'DEUTSCHLAND': 'DE', 'CANADA': 'CA',
    'UNITED KINGDOM': 'GB', 'UK': 'GB', 'ENGLAND': 'GB',
    'SOUTH KOREA': 'KR', 'KOREA': 'KR', 'FRANCE': 'FR', 'ITALY': 'IT',
    'MEXICO': 'MX', 'INDIA': 'IN', 'PHILIPPINES': 'PH', 'VIETNAM': 'VN',
}


def normalize_coo(val) -> Optional[str]:
    """Normalize a pricelist Country-of-Origin cell to an ISO-3166 alpha-2
    code (uppercase), or None if it can't be resolved. Accepts a 2-letter
    code directly; otherwise tries a small full-name alias map. Blank/sentinel
    values ('', '-', 'N/A') return None."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if not s or s in ('-', '—', 'N/A', 'NA', 'TBD', '?'):
        return None
    if len(s) == 2 and s.isalpha():
        return s
    return _COO_ALIASES.get(s)


# ─── FX RATES ────────────────────────────────────────────────────

async def fetch_fx_rate(pair: str = "USDCAD") -> float:
    """Fetch current exchange rate from Bank of Canada or fallback API."""
    # Map pair to Bank of Canada series name
    boc_series = {
        'USDCAD': 'FXUSDCAD',
        'EURCAD': 'FXEURCAD',
    }
    source_currency = pair[:3]  # e.g. USD, EUR

    # Try Bank of Canada first
    series = boc_series.get(pair.upper())
    if series:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    obs = data.get("observations", [])
                    if obs:
                        rate = float(obs[-1][series]["v"])
                        logger.info(f"Fetched {pair} rate: {rate}")
                        return rate
        except Exception as e:
            logger.warning(f"Bank of Canada API failed for {pair}: {e}")

    # Fallback: open exchange rate API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://open.er-api.com/v6/latest/{source_currency}")
            if resp.status_code == 200:
                data = resp.json()
                rate = data.get("rates", {}).get("CAD", 0)
                if rate:
                    logger.info(f"Fetched {pair} rate (fallback): {rate}")
                    return float(rate)
    except Exception as e:
        logger.warning(f"Fallback FX API failed for {pair}: {e}")

    return 0.0


def get_fx_rate(db, pair: str = "USDCAD") -> Dict:
    """Get stored FX rate and offset."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT currency_pair, rate, offset_pct, effective_rate, fetched_at FROM fx_rates WHERE currency_pair = ?",
            pair
        )
        row = cursor.fetchone()
        if row:
            return {
                'currency_pair': row[0],
                'rate': _f(row[1]),
                'offset_pct': _f(row[2]),
                'effective_rate': _f(row[3]),
                'fetched_at': row[4].isoformat() if row[4] else None,
            }
        return {'currency_pair': pair, 'rate': 0, 'offset_pct': 0, 'effective_rate': 0, 'fetched_at': None}
    finally:
        conn.close()


def save_fx_rate(db, pair: str, rate: float, offset_pct: float = 0) -> Dict:
    """Save FX rate with offset. Effective rate = rate * (1 + offset_pct/100)."""
    effective = rate * (1 + offset_pct / 100)
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE fx_rates AS target
            USING (SELECT ? AS currency_pair) AS source
            ON target.currency_pair = source.currency_pair
            WHEN MATCHED THEN UPDATE SET
                rate = ?, offset_pct = ?, effective_rate = ?,
                fetched_at = GETUTCDATE(), updated_at = GETUTCDATE()
            WHEN NOT MATCHED THEN INSERT
                (currency_pair, rate, offset_pct, effective_rate)
                VALUES (?, ?, ?, ?);
        """, pair, rate, offset_pct, effective, pair, rate, offset_pct, effective)
        conn.commit()
        return {'currency_pair': pair, 'rate': rate, 'offset_pct': offset_pct, 'effective_rate': round(effective, 6)}
    finally:
        conn.close()


# ─── VENDOR SETTINGS ─────────────────────────────────────────────

def get_all_vendor_settings(db) -> List[Dict]:
    """Get all vendor settings with extended fields."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                vs.vendor, vs.lead_time_days, vs.min_order_value, vs.notes,
                vs.invoice_currency, vs.enforces_map, vs.default_markup_pct,
                vs.website_url, vs.pricelist_filename, vs.pricelist_uploaded_at,
                vs.pricelist_release_date, vs.requires_barcode_labels,
                vs.pricelist_sku_column, vs.pricelist_cost_column,
                vs.pricelist_desc_column, vs.pricelist_msrp_column,
                vs.pricelist_cost_currency, vs.pricelist_msrp_currency,
                vs.pricelist_fallback_msrp_column, vs.pricelist_fallback_msrp_currency,
                vs.pricelist_sku_secondary_column, vs.pricelist_sku_separator,
                vs.pricelist_barcode_column, vs.pricelist_map_cad_column,
                vs.pricelist_coo_column,
                vs.po_reminder, vs.po_reminder_active,
                vs.last_stock_check_date, vs.last_stock_check_by,
                vs.last_stock_check_notes,
                vs.updated_at,
                -- Inventory summary from velocity cache
                (SELECT COUNT(*) FROM product_velocity_cache pvc WHERE pvc.vendor = vs.vendor) AS total_skus,
                (SELECT ISNULL(SUM(pvc.current_stock * pvc.cost), 0) FROM product_velocity_cache pvc WHERE pvc.vendor = vs.vendor) AS inventory_value,
                (SELECT ISNULL(SUM(pvc.total_sold_365d * pvc.price), 0) FROM product_velocity_cache pvc WHERE pvc.vendor = vs.vendor) AS revenue_365d
            FROM vendor_settings vs
            ORDER BY vs.vendor
        """)

        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row))
            results.append({
                'vendor': d['vendor'],
                'lead_time_days': d['lead_time_days'],
                'min_order_value': _f(d['min_order_value']),
                'notes': d['notes'],
                'invoice_currency': d['invoice_currency'] or 'CAD',
                'enforces_map': bool(d['enforces_map']),
                'default_markup_pct': _f(d['default_markup_pct']) if d['default_markup_pct'] else None,
                'website_url': d['website_url'],
                'pricelist_filename': d['pricelist_filename'],
                'pricelist_uploaded_at': d['pricelist_uploaded_at'].isoformat() if d['pricelist_uploaded_at'] else None,
                'pricelist_release_date': d['pricelist_release_date'].isoformat() if d['pricelist_release_date'] else None,
                'requires_barcode_labels': bool(d.get('requires_barcode_labels')),
                'pricelist_sku_column': d.get('pricelist_sku_column'),
                'pricelist_cost_column': d.get('pricelist_cost_column'),
                'pricelist_desc_column': d.get('pricelist_desc_column'),
                'pricelist_msrp_column': d.get('pricelist_msrp_column'),
                'pricelist_cost_currency': d.get('pricelist_cost_currency'),
                'pricelist_msrp_currency': d.get('pricelist_msrp_currency'),
                'pricelist_fallback_msrp_column': d.get('pricelist_fallback_msrp_column'),
                'pricelist_fallback_msrp_currency': d.get('pricelist_fallback_msrp_currency'),
                'pricelist_sku_secondary_column': d.get('pricelist_sku_secondary_column'),
                'pricelist_sku_separator': d.get('pricelist_sku_separator'),
                'pricelist_barcode_column': d.get('pricelist_barcode_column'),
                'pricelist_map_cad_column': d.get('pricelist_map_cad_column'),
                'pricelist_coo_column': d.get('pricelist_coo_column'),
                'po_reminder': d.get('po_reminder'),
                'po_reminder_active': bool(d.get('po_reminder_active')) if d.get('po_reminder_active') is not None else True,
                'last_stock_check_date': d['last_stock_check_date'].isoformat() if d.get('last_stock_check_date') else None,
                'last_stock_check_by': d.get('last_stock_check_by'),
                'last_stock_check_notes': d.get('last_stock_check_notes'),
                'updated_at': d['updated_at'].isoformat() if d['updated_at'] else None,
                'total_skus': d['total_skus'] or 0,
                'inventory_value': round(_f(d['inventory_value']), 2),
                'revenue_365d': round(_f(d['revenue_365d']), 2),
            })

        # Also include vendors from velocity cache that aren't in vendor_settings yet
        cursor.execute("""
            SELECT DISTINCT vendor FROM product_velocity_cache
            WHERE vendor IS NOT NULL AND vendor != ''
            AND vendor NOT IN (SELECT vendor FROM vendor_settings)
        """)
        for row in cursor.fetchall():
            results.append({
                'vendor': row[0],
                'lead_time_days': 14,
                'min_order_value': None,
                'notes': None,
                'invoice_currency': 'CAD',
                'enforces_map': False,
                'default_markup_pct': None,
                'website_url': None,
                'pricelist_filename': None,
                'pricelist_uploaded_at': None,
                'pricelist_release_date': None,
                'requires_barcode_labels': False,
                'po_reminder': None,
                'po_reminder_active': True,
                'last_stock_check_date': None,
                'last_stock_check_by': None,
                'last_stock_check_notes': None,
                'updated_at': None,
                'total_skus': 0,
                'inventory_value': 0,
                'revenue_365d': 0,
                'is_new': True,
            })

        results.sort(key=lambda v: v['vendor'])
        return results
    finally:
        conn.close()


def record_stock_check(db, vendor: str, check_date=None, by: str = None,
                       notes: str = None, clear: bool = False) -> Dict:
    """Record that this vendor's stock was physically counted.

    Deliberately separate from upsert_vendor_settings: that one writes every
    settings column, so routing stock checks through it would blank whatever
    the caller happened not to send. This touches only the three columns.

    check_date defaults to today (UTC). Pass clear=True to wipe the check
    back to "never counted" — the undo for a mis-clicked ✓.

    MERGE so a vendor that has no settings row yet still gets one.
    """
    from datetime import datetime, timezone
    if clear:
        check_date, by, notes = None, None, None
    elif not check_date:
        check_date = datetime.now(timezone.utc).date().isoformat()

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE vendor_settings AS target
            USING (SELECT ? AS vendor) AS source
            ON target.vendor = source.vendor
            WHEN MATCHED THEN UPDATE SET
                last_stock_check_date = ?, last_stock_check_by = ?,
                last_stock_check_notes = ?, updated_at = GETUTCDATE()
            WHEN NOT MATCHED THEN INSERT
                (vendor, last_stock_check_date, last_stock_check_by,
                 last_stock_check_notes)
                VALUES (?, ?, ?, ?);
        """,
            vendor,
            check_date, (by or None), (notes or None),
            vendor, check_date, (by or None), (notes or None),
        )
        conn.commit()
        return {'status': 'ok', 'vendor': vendor,
                'last_stock_check_date': check_date,
                'last_stock_check_by': by}
    finally:
        conn.close()


def upsert_vendor_settings(db, data: Dict) -> Dict:
    """Create or update vendor settings."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE vendor_settings AS target
            USING (SELECT ? AS vendor) AS source
            ON target.vendor = source.vendor
            WHEN MATCHED THEN UPDATE SET
                lead_time_days = ?, min_order_value = ?, notes = ?,
                invoice_currency = ?, enforces_map = ?, default_markup_pct = ?,
                website_url = ?, pricelist_release_date = ?,
                requires_barcode_labels = ?,
                po_reminder = ?, po_reminder_active = ?,
                updated_at = GETUTCDATE()
            WHEN NOT MATCHED THEN INSERT
                (vendor, lead_time_days, min_order_value, notes,
                 invoice_currency, enforces_map, default_markup_pct, website_url,
                 pricelist_release_date, requires_barcode_labels,
                 po_reminder, po_reminder_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
            data['vendor'],
            data.get('lead_time_days', 14),
            data.get('min_order_value'),
            data.get('notes'),
            data.get('invoice_currency', 'CAD'),
            data.get('enforces_map', False),
            data.get('default_markup_pct'),
            data.get('website_url'),
            data.get('pricelist_release_date'),
            1 if data.get('requires_barcode_labels') else 0,
            (data.get('po_reminder') or None),
            # Editing the reminder text re-arms it, so a newly-typed reminder
            # always shows even if the previous one had been dismissed.
            1 if data.get('po_reminder_active', True) else 0,
            # INSERT values
            data['vendor'],
            data.get('lead_time_days', 14),
            data.get('min_order_value'),
            data.get('notes'),
            data.get('invoice_currency', 'CAD'),
            data.get('enforces_map', False),
            data.get('default_markup_pct'),
            data.get('website_url'),
            data.get('pricelist_release_date'),
            1 if data.get('requires_barcode_labels') else 0,
            (data.get('po_reminder') or None),
            1 if data.get('po_reminder_active', True) else 0,
        )
        conn.commit()
        return {'status': 'ok', 'vendor': data['vendor']}
    finally:
        conn.close()


# ─── PRICELIST COMPARISON ────────────────────────────────────────

def process_pricelist_upload(db, vendor: str, csv_content: str, sku_column: str, cost_column: str,
                              description_column: str = None, msrp_column: str = None,
                              cost_currency: str = 'USD', msrp_currency: str = 'CAD',
                              fallback_msrp_column: str = None, fallback_msrp_currency: str = 'USD',
                              sku_secondary_column: str = None, sku_separator: str = ' ',
                              barcode_column: str = None, map_cad_column: str = None,
                              coo_column: str = None) -> Dict:
    """
    Process a vendor pricelist CSV and compare against Shopify inventory.
    Supports multi-currency with fusion logic:
    - Primary MSRP/MAP used if available (in msrp_currency)
    - Falls back to fallback MSRP column (in fallback_msrp_currency) if primary is blank
    - Cost converted using cost_currency → CAD
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Fetch all needed FX rates
        fx_rates = {'CAD': 1.0}
        for curr in set([cost_currency, msrp_currency, fallback_msrp_currency]):
            if curr.upper() != 'CAD':
                pair = curr.upper() + 'CAD'
                cursor.execute("SELECT effective_rate FROM fx_rates WHERE currency_pair = ?", pair)
                fx_row = cursor.fetchone()
                if fx_row and fx_row[0]:
                    fx_rates[curr.upper()] = _f(fx_row[0])
                else:
                    fx_rates[curr.upper()] = 1.0  # fallback, warn later

        cost_fx = fx_rates.get(cost_currency.upper(), 1.0)
        msrp_fx = fx_rates.get(msrp_currency.upper(), 1.0)
        fallback_fx = fx_rates.get(fallback_msrp_currency.upper(), 1.0)

        def parse_price(val):
            """Parse a price cell tolerantly. Strips currency symbols, codes
            (US$, USD, CAD, EUR, €, £), commas, and whitespace before
            converting. Keeps digits, a single decimal point, and a leading
            minus. Returns None for unparseable values."""
            if val is None:
                return None
            s = str(val).strip()
            if not s:
                return None
            import re as _re_p
            # Keep only digits, decimal point, and a leading minus sign.
            cleaned = _re_p.sub(r'[^\d.\-]', '', s)
            if not cleaned or cleaned in ('-', '.', '-.'):
                return None
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                return None

        # Parse CSV. When ``sku_secondary_column`` is set, the supplier SKU is
        # formed by concatenating ``sku_column`` + ``sku_separator`` +
        # ``sku_secondary_column`` — e.g. "L-Pro" + " " + "1.25\"" → 'L-Pro 1.25"'.
        # Used for vendors who split product/size across two columns and don't
        # have stable supplier SKUs.
        reader = csv.DictReader(io.StringIO(csv_content))

        # Auto-detect barcode + CAD MAP columns when caller didn't pin them.
        # Pricelists from different suppliers use slightly different headers
        # (e.g. "UPC", "UPC ", "Barcode", "Canada MAP", "MAP CAD"). We do a
        # case- + whitespace-insensitive match on the actual headers seen.
        actual_headers = list(reader.fieldnames or [])
        norm_to_actual = {(h or '').strip().lower(): h for h in actual_headers}
        def _find_header(candidates):
            for c in candidates:
                hit = norm_to_actual.get(c.strip().lower())
                if hit:
                    return hit
            return None
        if not barcode_column:
            barcode_column = _find_header(['UPC', 'UPC Code', 'Barcode',
                                           'EAN', 'GTIN'])
        if not map_cad_column:
            map_cad_column = _find_header(['CAD MAP', 'Canada MAP',
                                           'MAP CAD', 'MAP Canada',
                                           'Canadian MAP'])

        pricelist_items = []
        for row in reader:
            primary = (row.get(sku_column) or '').strip()
            if sku_secondary_column:
                secondary = (row.get(sku_secondary_column) or '').strip()
                if primary and secondary:
                    sku = primary + (sku_separator or ' ') + secondary
                else:
                    sku = primary or secondary
            else:
                sku = primary
            sku = sku.strip()
            if not sku:
                continue

            cost = parse_price(row.get(cost_column)) or 0

            # Skip rows with no cost (likely headers or non-product rows)
            if cost <= 0:
                continue

            desc = (row.get(description_column) or '').strip() if description_column else ''

            # Primary MSRP/MAP
            msrp = parse_price(row.get(msrp_column)) if msrp_column else None

            # Fallback MSRP (used when primary is blank)
            fallback_msrp = parse_price(row.get(fallback_msrp_column)) if fallback_msrp_column else None

            # Explicit Canadian MAP column (e.g. Sky-Watcher's "Canada MAP").
            # When present and >0 this beats both primary + fallback MSRP for
            # the listing price — no FX conversion needed because it's already
            # in CAD. Stored separately on the pricelist row so draft creation
            # can prefer it over a markup-derived price.
            map_cad = parse_price(row.get(map_cad_column)) if map_cad_column else None

            # Barcode/UPC. Trim whitespace and treat empty/zero as None.
            raw_bc = row.get(barcode_column) if barcode_column else None
            barcode = str(raw_bc).strip() if raw_bc not in (None, '') else None
            if barcode in ('0', 'N/A', 'n/a', '-', '—'):
                barcode = None

            # Country of Origin → ISO-3166 alpha-2 (e.g. 'CN'), or None.
            coo = normalize_coo(row.get(coo_column)) if coo_column else None

            # Fusion: resolve sale price in CAD, rounded to nearest dollar.
            # Precedence: explicit CAD MAP column > primary MSRP × FX > fallback MSRP × FX.
            if map_cad and map_cad > 0:
                sale_price_cad = round(map_cad)
                price_source = 'cad_map'
            elif msrp and msrp > 0:
                sale_price_cad = round(msrp * msrp_fx)  # nearest dollar
                price_source = 'primary'
            elif fallback_msrp and fallback_msrp > 0:
                sale_price_cad = round(fallback_msrp * fallback_fx)  # nearest dollar
                price_source = 'fallback'
            else:
                sale_price_cad = None
                price_source = 'none'

            pricelist_items.append({
                'supplier_sku': sku,
                'description': desc,
                'cost': cost,
                'cost_cad': round(cost * cost_fx, 2),
                'msrp': msrp,
                'fallback_msrp': fallback_msrp,
                'map_cad': map_cad,
                'barcode': barcode,
                'coo': coo,
                'sale_price_cad': sale_price_cad,
                'price_source': price_source,
            })

        if not pricelist_items:
            return {'status': 'error', 'message': 'No valid SKUs found in CSV'}

        # Deduplicate by SKU (keep last occurrence)
        seen = {}
        for item in pricelist_items:
            seen[item['supplier_sku'].upper()] = item
        pricelist_items = list(seen.values())

        # Clear old pricelist for this vendor
        cursor.execute("DELETE FROM vendor_pricelist_items WHERE vendor = ?", vendor)

        # Insert new pricelist items
        for item in pricelist_items:
            desc = (item['description'] or '')[:2000]
            bc = (item.get('barcode') or '')[:50] or None
            mcad = item.get('map_cad')
            coo = item.get('coo') or None
            cursor.execute("""
                INSERT INTO vendor_pricelist_items
                (vendor, supplier_sku, supplier_description, supplier_cost,
                 supplier_currency, supplier_msrp, supplier_barcode, supplier_map_cad,
                 supplier_coo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                vendor, item['supplier_sku'], desc,
                item['cost'], cost_currency, item['sale_price_cad'],
                bc, mcad, coo,
            )

        # Match against Shopify SKUs
        cursor.execute("""
            SELECT sku, product_title, price, cost, current_stock, vendor, tags,
                   product_id, variant_id, inventory_policy, variant_title
            FROM product_velocity_cache
            WHERE vendor = ?
        """, vendor)

        # Build the Shopify lookup keyed by normalize_sku(...).upper() so the
        # match is robust to Unicode roman numerals (Ⅱ↔II), prime / typographic
        # quotes (″↔"), and dash variants. ``shopify_skus`` is keyed by the
        # normalized form; the underlying record retains the original SKU.
        from .shopify_client import normalize_sku as _norm_sku
        shopify_skus = {}
        for row in cursor.fetchall():
            key = _norm_sku(row[0]).upper() if row[0] else ''
            if not key:
                continue
            shopify_skus[key] = {
                'sku': row[0],
                'title': row[1],
                'price': _f(row[2]),
                'cost': _f(row[3]),
                'stock': row[4],
                'tags': row[6] or '',
                'product_id': row[7] or '',
                'variant_id': row[8] or '',
                'inventory_policy': row[9] or 'DENY',
                'variant_title': row[10] or '',
            }

        # Build a parallel index keyed by the prefix-stripped form. Some Shopify
        # SKUs have the vendor name baked in ("ZWO ASI120MINI") while the
        # vendor's pricelist uses the bare SKU ("ASI120MINI"). Strip ``<vendor>``
        # plus whitespace from the start (case-insensitive) so we can still
        # match these. Stored value is the original Shopify record so the UI
        # can offer to rename the variant SKU in Shopify.
        import re as _re
        vendor_prefix_re = _re.compile(
            r'^' + _re.escape(vendor.strip()) + r'[\s\-_]+',
            _re.IGNORECASE,
        )
        stripped_shopify_skus = {}
        for upper_sku, info in shopify_skus.items():
            stripped = vendor_prefix_re.sub('', info['sku']).strip()
            stripped_key = _norm_sku(stripped).upper() if stripped else ''
            if stripped_key and stripped_key != upper_sku:
                # Keep first writer wins so the highest-priority candidate
                # (alphabetical by Shopify SKU) sticks if two strip to same key.
                stripped_shopify_skus.setdefault(stripped_key, info)

        # Title-token index for vendors whose pricelist doesn't carry stable
        # SKUs (e.g. Optolong with two columns "Item's Name" + "Size"). For
        # each Shopify variant we tokenize ``product_title + variant_title``
        # into a set of upper-cased tokens; a pricelist row whose tokens are
        # all present in the title is a high-confidence match. Tokens shorter
        # than 2 chars are dropped to avoid noise from "1" / "2" splits.
        def _tokens(text):
            if not text:
                return set()
            normalized = _norm_sku(text).upper()
            # Split on whitespace AND keep alphanumeric clusters; this preserves
            # things like '1.25"' and 'EOS-FF' as single tokens while still
            # breaking 'Optolong L-Pro Canon EOS-C' into discrete pieces.
            raw_tokens = _re.findall(r"[A-Z0-9][A-Z0-9.\-/'\"]*", normalized)
            return {t for t in raw_tokens if len(t) >= 2}

        title_records = []
        for upper_sku, info in shopify_skus.items():
            combined_title = (info.get('title') or '') + ' ' + (info.get('variant_title') or '')
            tokens = _tokens(combined_title)
            if tokens:
                title_records.append({
                    'tokens': tokens,
                    'combined': combined_title.strip(),
                    'info': info,
                })

        # Identify common tokens (tokens that appear in ≥50% of vendor variants).
        # E.g. 'L-Pro' is in every Optolong title, so it's noise — only the
        # remaining discriminating tokens make a SKU unique.
        from collections import Counter as _Counter
        token_freq = _Counter()
        for rec in title_records:
            for t in rec['tokens']:
                token_freq[t] += 1
        common_threshold = max(2, len(title_records) // 2)
        common_tokens = {t for t, count in token_freq.items() if count >= common_threshold}

        def _title_candidates(supplier_sku, top_n=3, min_score=0.34):
            """Return ranked title-match candidates for a pricelist SKU.
            Score is Jaccard-like over discriminating tokens (tokens in common /
            tokens in the pricelist SKU). Vendor-name tokens and tokens
            common to most variants of this vendor are excluded so a token
            like 'L-Pro' doesn't make every variant a 0.5 tie. A substring
            tiebreaker promotes matches whose normalized title contains
            chunks of the pricelist SKU (e.g. 'Sony' in 'L-Pro Sony-FF'
            matches the 'Sony FF' variant)."""
            sku_tokens = _tokens(supplier_sku)
            vendor_tokens = _tokens(vendor)
            ignore = vendor_tokens | common_tokens
            relevant = sku_tokens - ignore
            # Fall back to all tokens if filtering removed everything (the
            # SKU was nothing but vendor + common tokens).
            if not relevant:
                relevant = sku_tokens - vendor_tokens
            if not relevant:
                return []

            sku_norm = _norm_sku(supplier_sku).upper()

            def substring_score(combined):
                """Bonus 0..1 based on how many discriminating sub-strings of
                the pricelist SKU appear in the variant's combined title."""
                norm_title = _norm_sku(combined).upper()
                if not norm_title:
                    return 0.0
                # Strip vendor + common tokens from the SKU before chunking
                stripped = sku_norm
                for t in (vendor_tokens | common_tokens):
                    stripped = _re.sub(r'\b' + _re.escape(t) + r'\b', ' ', stripped)
                chunks = [c for c in _re.split(r'\s+', stripped.strip()) if len(c) >= 2]
                if not chunks:
                    return 0.0
                hits = sum(1 for c in chunks if c in norm_title)
                return hits / len(chunks)

            scored = []
            for rec in title_records:
                overlap = relevant & rec['tokens']
                if not overlap:
                    # Even with no token overlap, a substring hit can still
                    # surface a candidate — but it has to score at min.
                    sub = substring_score(rec['combined'])
                    if sub >= min_score:
                        scored.append((sub, sub, rec))
                    continue
                jaccard = len(overlap) / len(relevant)
                if jaccard < min_score:
                    continue
                # Composite score: jaccard primary, substring tiebreaker.
                sub = substring_score(rec['combined'])
                # Composite that keeps jaccard dominant but pulls ties apart.
                composite = jaccard + 0.001 * sub
                scored.append((composite, jaccard, rec))
            # Sort by descending composite, stable on title for determinism.
            scored.sort(key=lambda s: (-s[0], s[2]['combined']))
            return [(j, rec) for _, j, rec in scored[:top_n]]

        # Load saved SKU mappings for this vendor (from prior invoice/pricelist uploads)
        cursor.execute(
            "SELECT vendor_sku, shopify_sku FROM sku_mappings WHERE vendor = ?", vendor
        )
        saved_mappings = {_norm_sku(row[0]).upper(): row[1] for row in cursor.fetchall() if row[0]}

        # Pre-scan: any pricelist row that exactly matches a Shopify SKU
        # claims that Shopify SKU. Lower-priority bridges (saved mapping /
        # prefix / title) are NOT allowed to also route to a claimed SKU —
        # otherwise two pricelist rows compete to update the same Shopify
        # variant (e.g. pricelist has both 'PEG-X' and 'PEG-X-20' and the
        # bridged 'PEG-X' resolves to Shopify 'PEG-X-20' via a saved mapping
        # while 'PEG-X-20' itself exact-matches the same variant). Resolve
        # ties by giving exact match priority.
        claimed_by_exact = set()
        for item in pricelist_items:
            sku_upper = _norm_sku(item['supplier_sku']).upper()
            if sku_upper in shopify_skus:
                claimed_by_exact.add(_norm_sku(shopify_skus[sku_upper]['sku']).upper())

        # Classify each pricelist item
        matched = []
        in_pricelist_only = []
        pricelist_sku_set = set()

        for item in pricelist_items:
            sku_upper = _norm_sku(item['supplier_sku']).upper()

            # Helper: a bridged candidate is only acceptable if it isn't
            # already claimed by some other pricelist row's exact match.
            def _bridge_ok(candidate_info):
                sku_norm = _norm_sku(candidate_info['sku']).upper()
                return sku_norm not in claimed_by_exact

            # Priority order: saved mapping → exact → vendor-prefix → title.
            # Exact wins over any bridge (incl. saved mappings) so a stale
            # mapping can't shadow a correct same-SKU pricelist entry.
            mapped_via = None
            shopify_match = None
            if sku_upper in shopify_skus:
                shopify_match = shopify_skus[sku_upper]
                mapped_via = 'exact'

            if not shopify_match and sku_upper in saved_mappings:
                mapped_tc_sku = _norm_sku(saved_mappings[sku_upper]).upper()
                if mapped_tc_sku in shopify_skus:
                    candidate = shopify_skus[mapped_tc_sku]
                    if _bridge_ok(candidate):
                        shopify_match = candidate
                        mapped_via = 'mapping'

            # Vendor-prefix bridge: e.g. pricelist 'ASI120MINI' matches
            # Shopify 'ZWO ASI120MINI'. The Shopify SKU stays unchanged unless
            # the user clicks "Fix SKU in Shopify" in the UI.
            if not shopify_match and sku_upper in stripped_shopify_skus:
                candidate = stripped_shopify_skus[sku_upper]
                if _bridge_ok(candidate):
                    shopify_match = candidate
                    mapped_via = 'prefix'

            # Title-token bridge: pricelist 'L-Pro 1.25"' matches Shopify
            # variant whose product+variant title contains both 'L-Pro' and
            # '1.25"' (Optolong-style two-column pricelists). Only auto-claims
            # the match when there's exactly one high-scoring candidate; ties
            # or weaker matches fall through to the suggestions list so the
            # user can pick.
            title_suggestions = []
            if not shopify_match:
                candidates = _title_candidates(item['supplier_sku'])
                if candidates:
                    top_score, top_rec = candidates[0]
                    runner_score = candidates[1][0] if len(candidates) > 1 else 0
                    # Auto-match only when the leader is a clear winner: tokens
                    # of the pricelist SKU are fully contained in the title and
                    # nothing else scores as high — and the candidate isn't
                    # already claimed by another exact match.
                    if top_score >= 1.0 and runner_score < 1.0 and _bridge_ok(top_rec['info']):
                        shopify_match = top_rec['info']
                        mapped_via = 'title'
                    else:
                        title_suggestions = [
                            {
                                'sku': c['info']['sku'],
                                'title': c['combined'],
                                'score': round(s, 2),
                            }
                            for s, c in candidates
                            if _bridge_ok(c['info'])  # don't suggest claimed SKUs either
                        ]

            # Add the Shopify SKU (not vendor SKU) to the matched set so we correctly
            # detect "in Shopify but not in pricelist" for missing-from-pricelist logic.
            # Stored normalized so the later check against shopify_skus.items()
            # (which is keyed normalized) reconciles cleanly.
            if shopify_match:
                pricelist_sku_set.add(_norm_sku(shopify_match['sku']).upper())
            pricelist_sku_set.add(sku_upper)

            if shopify_match:
                cost_diff = abs(item['cost_cad'] - shopify_match['cost']) > 0.50

                # Check price change, but exclude items tagged "On Sale"
                on_sale = 'on sale' in shopify_match['tags'].lower()
                price_diff = False
                if item['sale_price_cad'] and shopify_match['price'] > 0 and not on_sale:
                    price_diff = abs(item['sale_price_cad'] - shopify_match['price']) > 0.50

                matched.append({
                    **item,
                    'shopify_sku': shopify_match['sku'],
                    'shopify_title': shopify_match['title'],
                    'shopify_price': shopify_match['price'],
                    'shopify_cost': shopify_match['cost'],
                    'shopify_stock': shopify_match['stock'],
                    'shopify_product_id': shopify_match.get('product_id', ''),
                    'shopify_variant_id': shopify_match.get('variant_id', ''),
                    'on_sale': on_sale,
                    'cost_changed': cost_diff,
                    'price_changed': price_diff,
                    'matched_via': mapped_via,  # 'exact' | 'mapping' | 'prefix' | 'title'
                    # When matched via prefix, surface the proposed clean SKU
                    # so the UI can offer a one-click rename in Shopify.
                    'prefix_match_suggested_sku': item['supplier_sku'] if mapped_via == 'prefix' else None,
                })
                cursor.execute("""
                    UPDATE vendor_pricelist_items SET matched_shopify_sku = ?, match_status = 'matched'
                    WHERE vendor = ? AND supplier_sku = ?
                """, shopify_match['sku'], vendor, item['supplier_sku'])
            else:
                # Surface fuzzy title-based candidates so the UI can offer
                # "Pick a match" suggestions for the user to confirm. Once
                # confirmed, an entry is written to ``sku_mappings`` so future
                # uploads auto-resolve via the saved-mapping branch above.
                in_pricelist_only.append({**item, 'suggestions': title_suggestions})
                cursor.execute("""
                    UPDATE vendor_pricelist_items SET match_status = 'new'
                    WHERE vendor = ? AND supplier_sku = ?
                """, vendor, item['supplier_sku'])

        # Find SKUs in Shopify but not in pricelist
        # Exclude items tagged as "Replacement Part" or "Discontinued"
        in_shopify_only = []
        for sku_upper, info in shopify_skus.items():
            if sku_upper not in pricelist_sku_set:
                tags_lower = (info.get('tags') or '').lower()
                if 'replacement part' not in tags_lower and 'discontinued' not in tags_lower:
                    in_shopify_only.append(info)

        # Update vendor pricelist metadata, store raw CSV, and save column/currency mappings
        cursor.execute("""
            UPDATE vendor_settings
            SET pricelist_filename = ?, pricelist_uploaded_at = GETUTCDATE(),
                pricelist_csv = ?,
                pricelist_sku_column = ?, pricelist_cost_column = ?,
                pricelist_desc_column = ?, pricelist_msrp_column = ?,
                pricelist_cost_currency = ?,
                pricelist_msrp_currency = ?,
                pricelist_fallback_msrp_column = ?,
                pricelist_fallback_msrp_currency = ?,
                pricelist_sku_secondary_column = ?,
                pricelist_sku_separator = ?,
                pricelist_barcode_column = ?,
                pricelist_map_cad_column = ?,
                pricelist_coo_column = ?,
                updated_at = GETUTCDATE()
            WHERE vendor = ?
        """, "pricelist_%s.csv" % vendor.replace(' ', '_'), csv_content,
            sku_column, cost_column, description_column, msrp_column,
            cost_currency, msrp_currency,
            fallback_msrp_column, fallback_msrp_currency,
            sku_secondary_column, sku_separator,
            barcode_column, map_cad_column, coo_column,
            vendor)

        conn.commit()

        price_changes = [m for m in matched if m.get('price_changed')]

        return {
            'status': 'ok',
            'vendor': vendor,
            'total_pricelist_items': len(pricelist_items),
            'matched': matched,
            'matched_count': len(matched),
            'in_pricelist_only': in_pricelist_only,
            'in_pricelist_only_count': len(in_pricelist_only),
            'in_shopify_only': in_shopify_only,
            'in_shopify_only_count': len(in_shopify_only),
            'cost_changes': [m for m in matched if m.get('cost_changed')],
            'price_changes': price_changes,
            # Count of matched items carrying a usable COO — gates the
            # "Set Country of Origin" action in the UI.
            'coo_count': sum(1 for m in matched if m.get('coo')),
            'fx_rates': fx_rates,
            'cost_currency': cost_currency,
            'msrp_currency': msrp_currency,
            'fallback_msrp_currency': fallback_msrp_currency,
        }
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pricelist_items(db, vendor: str, status: str = None) -> List[Dict]:
    """Get stored pricelist items for a vendor."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = ["vendor = ?"]
        params = [vendor]
        if status:
            where.append("match_status = ?")
            params.append(status)

        cursor.execute(f"""
            SELECT id, vendor, supplier_sku, supplier_description, supplier_cost,
                   supplier_currency, supplier_msrp, matched_shopify_sku, match_status
            FROM vendor_pricelist_items
            WHERE {' AND '.join(where)}
            ORDER BY supplier_sku
        """, *params)

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_stored_pricelist_csv(db, vendor):
    """Get the stored raw CSV content for a vendor."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT pricelist_filename, pricelist_csv, pricelist_uploaded_at FROM vendor_settings WHERE vendor = ?",
            vendor
        )
        row = cursor.fetchone()
        if not row or not row[1]:
            return None
        return {
            'filename': row[0] or 'pricelist.csv',
            'content': row[1],
            'uploaded_at': row[2].isoformat() if row[2] else None,
        }
    finally:
        conn.close()

def recompare_stored_pricelist(db, vendor):
    """Re-run comparison using the stored pricelist CSV and saved column/currency mappings."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pricelist_csv, pricelist_sku_column, pricelist_cost_column,
                   pricelist_desc_column, pricelist_msrp_column, invoice_currency,
                   pricelist_cost_currency, pricelist_msrp_currency,
                   pricelist_fallback_msrp_column, pricelist_fallback_msrp_currency,
                   pricelist_sku_secondary_column, pricelist_sku_separator,
                   pricelist_barcode_column, pricelist_map_cad_column,
                   pricelist_coo_column
            FROM vendor_settings WHERE vendor = ?
        """, vendor)
        row = cursor.fetchone()
        if not row or not row[0]:
            return None

        csv_content = row[0]
        sku_col = row[1]
        cost_col = row[2]
        desc_col = row[3]
        msrp_col = row[4]
        invoice_currency = row[5] or 'USD'
        cost_currency = row[6] or invoice_currency
        msrp_currency = row[7] or 'CAD'
        fallback_msrp_col = row[8]
        fallback_msrp_currency = row[9] or 'USD'
        sku_secondary_col = row[10]
        sku_separator = row[11] if row[11] is not None else ' '
        barcode_col = row[12]
        map_cad_col = row[13]
        coo_col = row[14]

        if not sku_col or not cost_col:
            return None
    finally:
        conn.close()

    # Re-run the comparison (this uses its own connection)
    return process_pricelist_upload(
        db, vendor, csv_content,
        sku_column=sku_col,
        cost_column=cost_col,
        description_column=desc_col,
        msrp_column=msrp_col,
        cost_currency=cost_currency,
        msrp_currency=msrp_currency,
        fallback_msrp_column=fallback_msrp_col,
        fallback_msrp_currency=fallback_msrp_currency,
        sku_secondary_column=sku_secondary_col,
        sku_separator=sku_separator,
        barcode_column=barcode_col,
        map_cad_column=map_cad_col,
        coo_column=coo_col,
    )
