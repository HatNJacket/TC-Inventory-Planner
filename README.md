# TC Inventory Planner

Replacement for the Inventory Planner by Sage app ($164.99/month), built for
Telescopes Canada. Connects to Shopify for product/inventory/order data and
generates replenishment recommendations using seasonal sales velocity.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────┐
│  React Frontend │────▶│  FastAPI Backend  │────▶│   Azure SQL   │
│  (Static Files) │     │  (Python 3.11)   │     │  (TC Server)  │
└─────────────────┘     └────────┬─────────┘     └───────────────┘
                                 │
                        ┌────────▼─────────┐
                        │  Shopify GraphQL  │
                        │  Admin API       │
                        │  (2025-01)       │
                        └──────────────────┘
```

## Forecasting Approach

1. **Base velocity**: Average daily units sold per SKU from trailing 12 months
2. **Seasonal multiplier**: Month-level adjustment from TC's actual revenue pattern
3. **Replenishment qty**: `(lead_time + safety_stock) × seasonal_velocity - stock - on_order`

### TC Seasonal Multipliers (from FY2025 data)

| Month | Multiplier | Notes |
|-------|-----------|-------|
| Jan   | 0.895     | Post-holiday slowdown |
| Feb   | 0.610     | Deepest trough |
| Mar   | 1.292     | Spring ramp-up |
| Apr   | 1.019     | Steady |
| May   | 0.901     | Slight dip |
| Jun   | 0.738     | Summer lull |
| Jul   | 0.962     | Recovering |
| Aug   | 1.004     | Back to baseline |
| Sep   | 0.887     | Early fall dip |
| Oct   | 0.854     | Pre-holiday quiet |
| Nov   | 1.457     | Black Friday / holiday peak |
| Dec   | 1.381     | Holiday peak continues |

## Local Development

### Quick start (Windows)

One-time installs (any Python 3.11 or newer works, including 3.13/3.14):

```
winget install --id Python.Python.3.13 -e
winget install --id Microsoft.msodbcsql.18 -e
winget install --id OpenJS.NodeJS.LTS -e
```

Then double-click `run-local.bat` (or run `py run_local.py`). The first
run creates `backend\.env` from the template and stops. Fill in the real
values (Azure portal → App Services → tc-planner-app → Settings →
Environment variables) and run it again. It sets up `backend\.venv`,
builds the web page, and serves everything at http://localhost:8000
(API docs at http://localhost:8000/docs).

- `py run_local.py --lan` lets other PCs on the network connect.
- **Your local copy uses the LIVE database and Shopify store.** Saving
  a stock order locally changes real data. Leave `RFID_STATION_KEY`
  blank unless you mean to send label jobs to the live RFID app.
- If the database won't connect, the Azure SQL firewall may be blocking
  your IP: add it on the SQL server's Networking page in the portal.
- For live frontend editing, run `npm run dev` in `frontend/` alongside
  the backend and open http://localhost:3000.

### Manual backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Copy and edit environment variables
cp .env.template .env
# Edit .env with your credentials

# Run
uvicorn app.main:app --reload --port 8000
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/refresh` | Refresh data from Shopify (heavy) |
| GET | `/api/replenishment` | Get replenishment recommendations |
| GET | `/api/replenishment/summary` | Overview stats |
| GET | `/api/config/vendors` | Vendor list with lead times |
| GET | `/api/config/seasonal-multipliers` | Seasonal multipliers |
| GET | `/api/stock-orders` | List stock orders |
| GET | `/api/stock-orders/{id}` | Get stock order detail |
| POST | `/api/stock-orders` | Create stock order |
| PATCH | `/api/stock-orders/{id}/status` | Update order status |
| POST | `/api/stock-orders/{id}/receive` | Receive items |

All endpoints (except `/api/health`) require Bearer token auth:
```
Authorization: Bearer your-secret-token
```

### Data Refresh Flow

```
POST /api/refresh
  └── Fetch products from Shopify (paginated GraphQL)
  └── Fetch 12 months of order line items
  └── Calculate velocity per SKU (seasonal-adjusted)
  └── Compute replenishment recommendations
  └── Cache results in Azure SQL
  └── Return summary stats
```

## Azure Deployment

### Option 1: Azure App Service (recommended)

```bash
# Build Docker image
docker build -t tc-planner .

# Push to Azure Container Registry
az acr login --name yourregistry
docker tag tc-planner yourregistry.azurecr.io/tc-planner:latest
docker push yourregistry.azurecr.io/tc-planner:latest

# Deploy to App Service
az webapp create \
  --resource-group tc-rg \
  --plan tc-app-plan \
  --name tc-inventory-planner \
  --deployment-container-image-name yourregistry.azurecr.io/tc-planner:latest

# Set environment variables
az webapp config appsettings set \
  --name tc-inventory-planner \
  --resource-group tc-rg \
  --settings \
    SHOPIFY_ACCESS_TOKEN=shpat_xxx \
    AZURE_SQL_SERVER=your-server.database.windows.net \
    AZURE_SQL_DATABASE=telescopes-canada-db \
    AZURE_SQL_USER=tc-user \
    AZURE_SQL_PASSWORD=your-password \
    TC_PLANNER_AUTH_TOKEN=your-token
```

### Option 2: Azure Functions (if preferred)

The FastAPI app can also be wrapped as an Azure Function using the
`azure-functions` package with an ASGI adapter. This keeps it on the
existing Linux Consumption plan.

### Scheduled Data Refresh

Set up an Azure Timer Trigger (or cron job) to call `POST /api/refresh`
nightly. Recommended schedule: 2:00 AM ET daily.

```bash
# Via curl
curl -X POST https://tc-inventory-planner.azurewebsites.net/api/refresh \
  -H "Authorization: Bearer your-token"
```

## Database Schema

- `stock_orders` - Purchase orders with status tracking
- `stock_order_items` - Line items within each order
- `product_velocity_cache` - Cached replenishment data (refreshed on /api/refresh)
- `vendor_settings` - Vendor-specific lead times and settings
- `app_settings` - App configuration (reference number counter, etc.)

Tables are auto-created on first startup.

## Phase 2 Roadmap

- [ ] Overview dashboard screen
- [ ] Stock Orders list and detail screens
- [ ] Export to CSV/PDF
- [ ] Email stock orders to vendors
- [ ] Receive items with barcode scanning
- [ ] Automatic Shopify inventory updates on receive
- [ ] Timer-triggered nightly data refresh
