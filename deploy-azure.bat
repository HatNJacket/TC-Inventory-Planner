@echo off
REM ============================================================
REM TC Inventory Planner - Azure Deployment Script
REM Run this from: C:\tc-planner\
REM Prerequisites: az cli, node/npm installed
REM ============================================================

echo === Step 1: Build React frontend ===
cd frontend
call npm install
call npm run build
cd ..
echo Frontend built to backend\static\

echo.
echo === Step 2: Create Azure Container Registry (first time only) ===
az acr create --name tcplanneracr --resource-group shopify-automation-rg --sku Basic --admin-enabled true
echo.

echo === Step 3: Build Docker image in Azure (no local Docker needed) ===
az acr build --registry tcplanneracr --image tc-planner:latest .
echo.

echo === Step 4: Create App Service Plan (first time only) ===
az appservice plan create --name tc-planner-plan --resource-group shopify-automation-rg --is-linux --sku B1 --location canadaeast
echo.

echo === Step 5: Create Web App (first time only) ===
az webapp create --name tc-planner-app --resource-group shopify-automation-rg --plan tc-planner-plan --docker-registry-server-url https://tcplanneracr.azurecr.io --deployment-container-image-name tcplanneracr.azurecr.io/tc-planner:latest
echo.

echo === Step 6: Configure container registry credentials ===
for /f "tokens=*" %%a in ('az acr credential show --name tcplanneracr --query "passwords[0].value" -o tsv') do set ACR_PASSWORD=%%a
az webapp config container set --name tc-planner-app --resource-group shopify-automation-rg --docker-registry-server-url https://tcplanneracr.azurecr.io --docker-registry-server-user tcplanneracr --docker-registry-server-password %ACR_PASSWORD%
echo.

echo === Step 7: Set environment variables ===
az webapp config appsettings set --name tc-planner-app --resource-group shopify-automation-rg --settings ^
  SHOPIFY_STORE=telescopes-canada ^
  SHOPIFY_API_VERSION=2025-01 ^
  SHOPIFY_ACCESS_TOKEN=YOUR_SHOPIFY_TOKEN ^
  AZURE_SQL_SERVER=telcansql.database.windows.net ^
  AZURE_SQL_DATABASE=YOUR_DB_NAME ^
  AZURE_SQL_USER=YOUR_DB_USER ^
  AZURE_SQL_PASSWORD=YOUR_DB_PASSWORD ^
  TC_PLANNER_AUTH_TOKEN=YOUR_APP_PASSWORD ^
  ACS_CONNECTION_STRING="endpoint=https://tc-communications.canada.communication.azure.com/;accesskey=YOUR_KEY" ^
  PO_EMAIL_FROM=support@telescopescanada.ca ^
  PO_EMAIL_TO=steve@telescopescanada.ca ^
  DEFAULT_LEAD_TIME_DAYS=14 ^
  PLANNING_HORIZON_DAYS=30 ^
  SAFETY_STOCK_DAYS=7 ^
  SALES_HISTORY_MONTHS=12 ^
  WEBSITES_PORT=8000
echo.

echo === Done! ===
echo Your app will be available at: https://tc-planner-app.azurewebsites.net
echo.
echo To redeploy after code changes, run steps 1 and 3 only:
echo   cd frontend ^&^& npm run build ^&^& cd ..
echo   az acr build --registry tcplanneracr --image tc-planner:latest .
echo   az webapp restart --name tc-planner-app --resource-group shopify-automation-rg
pause
