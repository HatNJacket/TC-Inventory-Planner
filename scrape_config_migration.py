"""
Database & API additions for vendor scrape config feature.

== DATABASE MIGRATION ==
Add to database.py _ensure_tables() or run directly:

    ALTER TABLE vendor_settings ADD scrape_config NVARCHAR(MAX) NULL;

== NEW API ENDPOINTS ==
Add these to main.py alongside the existing vendor management endpoints.
"""

# ─────────────────────────────────────────────────────────────────
# DATABASE MIGRATION — add to database.py _ensure_tables()
# ─────────────────────────────────────────────────────────────────
DB_MIGRATION_SQL = """
-- Add scrape_config column to vendor_settings if not exists
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'vendor_settings'
    AND COLUMN_NAME = 'scrape_config'
)
BEGIN
    ALTER TABLE vendor_settings ADD scrape_config NVARCHAR(MAX) NULL;
END
"""


# ─────────────────────────────────────────────────────────────────
# API ENDPOINTS — add to main.py
# ─────────────────────────────────────────────────────────────────
ENDPOINT_CODE = '''
# ── Vendor Scrape Config Endpoints ──────────────────────────────

from app.vendor_scraper import get_default_config, get_all_default_vendors, DEFAULT_CONFIGS
from app.draft_creator import test_scrape_config


@app.get("/api/vendors/{vendor}/scrape-config")
async def get_vendor_scrape_config(vendor: str, token: str = Depends(verify_token)):
    """Get the scrape config for a vendor (from DB or defaults)."""
    # Try DB first
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

    # Fall back to defaults
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
async def save_vendor_scrape_config(vendor: str, request: Request,
                                     token: str = Depends(verify_token)):
    """Save or update the scrape config for a vendor."""
    body = await request.json()
    config = body.get("config")

    if not config:
        raise HTTPException(400, "config is required")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        config_json = json.dumps(config)

        # Check if vendor exists in vendor_settings
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
async def test_vendor_scrape(vendor: str, request: Request,
                              token: str = Depends(verify_token)):
    """
    Test a vendor's scrape config against a product URL.
    Returns the raw extraction results so the user can verify.
    """
    body = await request.json()
    product_url = body.get("product_url")

    if not product_url:
        raise HTTPException(400, "product_url is required")

    result = await test_scrape_config(vendor, product_url, db)
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
'''


# ─────────────────────────────────────────────────────────────────
# API.JS CLIENT FUNCTIONS — add to frontend/src/api.js
# ─────────────────────────────────────────────────────────────────
API_JS_CODE = '''
// ── Vendor Scrape Config ──────────────────────────────────────

export async function getVendorScrapeConfig(vendor) {
  const resp = await fetch(`${BASE}/api/vendors/${encodeURIComponent(vendor)}/scrape-config`, {
    headers: authHeaders(),
  });
  return resp.json();
}

export async function saveVendorScrapeConfig(vendor, config) {
  const resp = await fetch(`${BASE}/api/vendors/${encodeURIComponent(vendor)}/scrape-config`, {
    method: 'PUT',
    headers: authHeaders(),
    body: JSON.stringify({ config }),
  });
  return resp.json();
}

export async function resetVendorScrapeConfig(vendor) {
  const resp = await fetch(`${BASE}/api/vendors/${encodeURIComponent(vendor)}/scrape-config`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return resp.json();
}

export async function testVendorScrapeConfig(vendor, productUrl) {
  const resp = await fetch(`${BASE}/api/vendors/${encodeURIComponent(vendor)}/scrape-config/test`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ product_url: productUrl }),
  });
  return resp.json();
}

export async function listDefaultScrapeConfigs() {
  const resp = await fetch(`${BASE}/api/vendors/scrape-config/defaults`, {
    headers: authHeaders(),
  });
  return resp.json();
}

export async function loadDefaultScrapeConfig(vendor) {
  const resp = await fetch(`${BASE}/api/vendors/${encodeURIComponent(vendor)}/scrape-config/load-default`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return resp.json();
}
'''
