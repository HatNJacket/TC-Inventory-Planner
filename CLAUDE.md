# TC-Planner — project notes for Claude

## Deploying to Azure

TC-Planner is a containerized FastAPI app on Azure App Service. The frontend
bundle is served by the FastAPI backend out of `backend/static/`, so any
frontend change must be **rebuilt** before the image is rebuilt.

The full deploy sequence (run from project root):

```powershell
cd C:\tc-planner\frontend; npm run build; cd ..
az acr build --registry tcplanneracr --image tc-planner:latest --no-logs .
az webapp restart --name tc-planner-app --resource-group shopify-automation-rg
```

> **Why `--no-logs`?** On Windows, the Azure CLI's log streamer crashes
> with `UnicodeEncodeError: 'charmap' codec` when the build output
> contains characters outside cp1252 (e.g. anything pip downloads with
> non-ASCII metadata). The build itself runs fine on Azure — only the
> local log tailing fails. Passing `--no-logs` skips the tailing and
> just reports `status: Succeeded` / `Failed` when the run finishes.
> If you need to see what went wrong on a failure, fetch logs after
> the fact with `az acr task logs --registry tcplanneracr --run-id <id>`.

What each step does:

1. `npm run build` — Vite build that writes the bundle into
   `C:\tc-planner\backend\static\`. The Dockerfile copies this directory
   into the image, so skipping this step ships stale frontend code.
2. `az acr build` — Builds the Docker image in Azure Container Registry
   (`tcplanneracr`) and tags it `tc-planner:latest`.
3. `az webapp restart` — Restarts the App Service so it pulls the new
   `tc-planner:latest` image.

### When deploys are NOT needed

- **Backend-only Python changes during local dev** — if you're running
  uvicorn locally, just restart the local process. Push only when you
  want the change live on Azure.
- **Frontend changes during local dev** — `npm run dev` with Vite HMR
  picks up changes instantly. The full build/push is only for production.

## Related Azure resources

- **App Service**: `tc-planner-app` (resource group `shopify-automation-rg`)
- **Container Registry**: `tcplanneracr`
- **Image**: `tc-planner:latest`

## Sibling project: shopify-jobs

`C:\Shopify-Azure\shopify-jobs` is a separate Azure Functions app
(`shopify-automation-func`) that runs the Shopify automation timers
(More on the Way tagger, scan_fulfillable, webhooks, etc.). It is
**unrelated** to TC-Planner deploys — pushing one does not push the
other. Deploy that app with:

```powershell
cd C:\Shopify-Azure\shopify-jobs; func azure functionapp publish shopify-automation-func --python
```
