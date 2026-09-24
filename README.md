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

## Branches (read this first)

- **`development`** - do all your work here. Push to it freely; nothing
  goes live.
- **`main`** - production. **Every push to main deploys to Azure
  automatically** (GitHub Actions, `.github/workflows/deploy.yml`).
  Only merge into main when the change is tested and approved.

```
git checkout development
git pull origin development
```

## Local Development

### Quick start (Windows)

For fast shipping development/testing, double-click **`Start Shipping Planner.bat`**.
It starts the backend and frontend together in one window and opens
http://localhost:3000 once both are ready. Select **Shipping** after signing in.
Keep the launcher window open; press **Ctrl+C** there to stop both servers.
Close any previously started backend/frontend terminals before the first run.
Python and frontend dependencies are installed on the first run (and when their
dependency files change). Later launches skip those installs and the frontend
build; frontend edits refresh automatically and Python edits restart the API.
It uses the same `backend/.env` and local shipping data as your manual launch.
The existing live-database/Shopify precautions below still apply.

In **Shipping → Custom Shipment**, search by SKU or product name, add products,
set quantities, and click **Load Custom Shipment**, then **Build Packing Plan**.
This uses the Package Database (including every part of multi-package products)
without fetching or creating a Shopify order. Up to 50 SKUs / 100 product units
can be loaded. Editing the selection clears the old plan; switching shipping
tabs preserves both the custom shipment and the separate Shopify order.
Warehouse profiles are cached on this browser and refreshed in the background;
staff still choose their profile each time they enter Shipping.

Double-click `run-local.bat`. That's it. On the first run it:

1. Installs Python (if missing), ODBC Driver 18 for SQL Server and
   Node.js LTS with winget. Windows asks for permission once; running it
   accepts those installers' licence terms.
2. Writes `backend\.env` automatically. Normally it installs the Azure
   CLI, opens a browser to sign you in to Azure, and copies the live
   tc-planner-app settings (your account needs access - see "Azure
   access" below). Alternatively, a gitignored **`setup_env.py`** with
   the settings built in (made with `py run_local.py --make-setup-script`,
   holds live secrets, never commit it) is used first if it's present.
3. Checks the database connects. If the Azure SQL firewall blocks your
   IP and you're signed into the Azure CLI, it offers to add a rule.
4. Builds the web page and serves everything at http://localhost:8000
   (API docs at http://localhost:8000/docs).

Later runs skip whatever is already done. Other options:

- `py run_local.py --lan` lets other PCs on the network connect.
- `py run_local.py --refresh-env` re-pulls `backend\.env` from Azure
  after secrets change (or get a fresh `setup_env.py` and delete
  `backend\.env`).
- **Your local copy uses the LIVE database and Shopify store.** Saving
  a stock order locally changes real data. The RFID label bridge is
  always switched off locally (`RFID_STATION_KEY` blank) so a dev copy
  can't queue prints in the warehouse.
- For live frontend editing, run `npm run dev` in `frontend/` alongside
  the backend and open http://localhost:3000.

### Azure access (one time per developer)

Reading the settings needs the **Website Contributor** role on
tc-planner-app. An owner of the subscription grants it once (Azure
portal → tc-planner-app → Access control (IAM) → Add role assignment),
or with the CLI:

```
az role assignment create --assignee THEIR-EMAIL --role "Website Contributor" --scope /subscriptions/cbb1bba1-5404-4caa-961e-a39b36d3fa86/resourceGroups/shopify-automation-rg/providers/Microsoft.Web/sites/tc-planner-app
```

Someone outside the organization's Microsoft account first needs a guest
invite (Microsoft Entra ID → Users → Invite external user). The optional
automatic SQL firewall rule additionally needs **SQL Server
Contributor** on the SQL server; without it, the launcher prints the IP
for an owner to allow.

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
