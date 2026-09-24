# TC-Planner — project notes for Claude

## Deploying to Azure

TC-Planner is a containerized FastAPI app on Azure App Service. The frontend
bundle is served by the FastAPI backend out of `backend/static/`, so any
frontend change must be **rebuilt** before the image is rebuilt.

The full deploy sequence (run from project root). Since 2026-08-25 the
Dockerfile is multi-stage and builds the frontend INSIDE `az acr build`
(a Node stage runs `npm ci && vite build`), so no local Node install is
needed and a stale local bundle can never ship:

```powershell
git fetch origin; git status   # must NOT be behind origin/main -- see below
az acr build --registry tcplanneracr --image tc-planner:<YYYY-MM-DD><x> --image tc-planner:latest --no-logs .
az webapp config container set --name tc-planner-app --resource-group shopify-automation-rg --container-image-name tcplanneracr.azurecr.io/tc-planner:<YYYY-MM-DD><x>
az webapp restart --name tc-planner-app --resource-group shopify-automation-rg
```

(`npm run build` locally is now only for Vite dev workflows; the deploy
ignores backend/static entirely.)

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
   (`tcplanneracr`) under a dated tag (e.g. `2026-09-17a`) plus `latest`.
3. `az webapp config container set` — Points the App Service at that dated
   tag. **Since 2026-09-14 the app runs a pinned tag, not `latest`**, so
   building `latest` alone deploys nothing. Check what is live with
   `az webapp config show ... --query linuxFxVersion`.
4. `az webapp restart` — Restarts the App Service onto the new image.

> **Deploys happen from more than one machine.** The image is built from the
> local working tree, so building from a checkout that is behind
> `origin/main` -- or that holds uncommitted work -- silently rolls back
> whatever the other machine shipped. On 2026-09-14 a deploy from GitHub
> removed a week of uncommitted features this way. Before building: pull,
> and commit/push anything you are about to ship.

### When deploys are NOT needed

- **Backend-only Python changes during local dev** — if you're running
  uvicorn locally, just restart the local process. Push only when you
  want the change live on Azure.
- **Frontend changes during local dev** — `npm run dev` with Vite HMR
  picks up changes instantly. The full build/push is only for production.

## Related Azure resources

- **App Service**: `tc-planner-app` (resource group `shopify-automation-rg`)
- **Container Registry**: `tcplanneracr`
- **Image**: `tc-planner:<dated tag>` (pinned; `latest` is also pushed but not what runs)

## Sibling project: shopify-jobs

`C:\Shopify-Azure\shopify-jobs` is a separate Azure Functions app
(`shopify-automation-func`) that runs the Shopify automation timers
(More on the Way tagger, scan_fulfillable, webhooks, etc.). It is
**unrelated** to TC-Planner deploys — pushing one does not push the
other. Deploy that app with:

```powershell
cd C:\Shopify-Azure\shopify-jobs; func azure functionapp publish shopify-automation-func --python
```
