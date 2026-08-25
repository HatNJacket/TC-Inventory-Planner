"""
TC Inventory Planner - Configuration
Environment variables for Shopify API, Azure SQL, and app settings.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

# Load .env from the backend directory (handles running from any cwd)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)



@dataclass
class Config:
    # Shopify API
    SHOPIFY_STORE: str = os.getenv("SHOPIFY_STORE", "telescopes-canada")
    SHOPIFY_API_VERSION: str = os.getenv("SHOPIFY_API_VERSION", "2025-01")
    SHOPIFY_ACCESS_TOKEN: str = os.getenv("SHOPIFY_ACCESS_TOKEN", "")

    # Azure SQL
    AZURE_SQL_SERVER: str = os.getenv("AZURE_SQL_SERVER", "")
    AZURE_SQL_DATABASE: str = os.getenv("AZURE_SQL_DATABASE", "")
    AZURE_SQL_USER: str = os.getenv("AZURE_SQL_USER", "")
    AZURE_SQL_PASSWORD: str = os.getenv("AZURE_SQL_PASSWORD", "")

    # Shopify location that received stock is added to. Must match the
    # location name in Shopify exactly (case-insensitive). Event locations
    # such as "Starfest" are deliberately NOT the default — stock is received
    # into the warehouse and only moves to an event location manually.
    RECEIVING_LOCATION_NAME: str = os.getenv(
        "SHOPIFY_RECEIVING_LOCATION", "Telescopes Canada Warehouse"
    )

    # App settings
    AUTH_TOKEN: str = os.getenv("TC_PLANNER_AUTH_TOKEN", "tc-planner-dev-token")
    # Per-user tokens so actions (e.g. receiving stock) can be attributed to a
    # person. Format: "Name:token,Name:token" in TC_PLANNER_USER_TOKENS.
    # The shared AUTH_TOKEN above keeps working (attributed to "Unknown"), so
    # existing sessions/integrations don't break when user tokens are added.
    USER_TOKENS_RAW: str = os.getenv("TC_PLANNER_USER_TOKENS", "")
    DEFAULT_LEAD_TIME_DAYS: int = int(os.getenv("DEFAULT_LEAD_TIME_DAYS", "14"))
    PLANNING_HORIZON_DAYS: int = int(os.getenv("PLANNING_HORIZON_DAYS", "30"))
    SAFETY_STOCK_DAYS: int = int(os.getenv("SAFETY_STOCK_DAYS", "7"))
    SALES_HISTORY_MONTHS: int = int(os.getenv("SALES_HISTORY_MONTHS", "12"))

    # RFID Stickers app (telcan-rfid): the Stock Orders "Print labels"
    # button sends received items there to queue RFID label prints.
    # The bridge is OFF until RFID_STATION_KEY is set.
    RFID_APP_URL: str = os.getenv(
        "RFID_APP_URL", "https://telcan-rfid.azurewebsites.net"
    )
    RFID_STATION_KEY: str = os.getenv("RFID_STATION_KEY", "")

    # Email (Azure Communication Services)
    ACS_CONNECTION_STRING: str = os.getenv("ACS_CONNECTION_STRING", "")
    PO_EMAIL_FROM: str = os.getenv("PO_EMAIL_FROM", "support@telescopescanada.ca")  # Azure-managed sender e.g. DoNotReply@<guid>.azurecomm.net
    PO_EMAIL_TO: str = os.getenv("PO_EMAIL_TO", "billing@telescopescanada.ca")  # Default recipient for PO emails

    # Vendors that require extra metadata columns in PO emails.
    # Vendor name → list of extra fields to include in the PO CSV.
    # Supported field keys (sourced from product_velocity_cache):
    #   "cost_usd"     — manufacturer USD cost from Shopify metafields
    #   "system_code"  — manufacturer system / catalogue code
    VENDORS_WITH_EXTRA_FIELDS: Dict[str, list] = field(default_factory=lambda: {
        "ZWO": ["system_code"],
        "Celestron": ["cost_usd"],
        "Sky-Watcher": ["cost_usd"],
    })

    # Vendor-specific lead times (days)
    VENDOR_LEAD_TIMES: Dict[str, int] = field(default_factory=lambda: {
        "Celestron": 30,
        "ZWO": 21,
        "Sky-Watcher": 30,
        "iOptron": 21,
        "Baader Planetarium": 14,
        "Explore Scientific": 14,
        "Software Bisque": 14,
        "Meade Instruments": 14,
        "William Optics": 30,
        "Svbony": 21,
        "Pegasus Astro": 14,
        "QHYCCD": 21,
        "Askar": 30,
        "Optolong": 21,
        "Antares": 14,
        "Unistellar": 21,
        "DWARFLab": 21,
        "Seestar": 21,
        "Hubble Optics": 30,
        "Firefly Books": 7,
    })

    # Seasonal multipliers derived from TC's FY2025 monthly revenue data
    # Each month's gross sales relative to the 12-month average
    SEASONAL_MULTIPLIERS: Dict[int, float] = field(default_factory=lambda: {
        1: 0.895,   # January  - post-holiday slowdown
        2: 0.610,   # February - deepest trough
        3: 1.292,   # March    - spring ramp-up
        4: 1.019,   # April    - steady
        5: 0.901,   # May      - slight dip
        6: 0.738,   # June     - summer lull
        7: 0.962,   # July     - recovering
        8: 1.004,   # August   - back to baseline
        9: 0.887,   # September - early fall dip
        10: 0.854,  # October  - pre-holiday quiet
        11: 1.457,  # November - Black Friday / holiday peak
        12: 1.381,  # December - holiday peak continues
    })

    @property
    def user_tokens(self) -> dict:
        """Parse TC_PLANNER_USER_TOKENS ("Name:token,Name:token") into
        {token: user_name}. Malformed entries are skipped rather than
        breaking startup."""
        mapping = {}
        for pair in (self.USER_TOKENS_RAW or "").split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, _, tok = pair.partition(":")
            name, tok = name.strip(), tok.strip()
            if name and tok:
                mapping[tok] = name
        return mapping

    @property
    def shopify_graphql_url(self) -> str:
        return f"https://{self.SHOPIFY_STORE}.myshopify.com/admin/api/{self.SHOPIFY_API_VERSION}/graphql.json"

    @property
    def azure_sql_connection_string(self) -> str:
        return (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={self.AZURE_SQL_SERVER};"
            f"DATABASE={self.AZURE_SQL_DATABASE};"
            f"UID={self.AZURE_SQL_USER};"
            f"PWD={self.AZURE_SQL_PASSWORD};"
            f"Encrypt=yes;TrustServerCertificate=no;"
        )

    def get_lead_time(self, vendor: str) -> int:
        return self.VENDOR_LEAD_TIMES.get(vendor, self.DEFAULT_LEAD_TIME_DAYS)

    def get_seasonal_multiplier(self, month: int) -> float:
        return self.SEASONAL_MULTIPLIERS.get(month, 1.0)


config = Config()
