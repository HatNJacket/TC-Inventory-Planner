# TC-Planner — project notes for Claude

## Branches and deploying

- **`development`** - where ALL work lands (Claude sessions, every
  developer). Commit and push here freely; nothing deploys.
- **`main`** - production. **Every push to main deploys automatically**
  via `.github/workflows/deploy.yml`. Only merge `development` into main
  when it's tested and Steve wants it live. Never commit straight to main.

Before starting work: `git checkout development` then
`git pull origin development`. To ship:

```powershell
git checkout main; git pull origin main
git merge development
git push origin main          # this IS the deploy
git checkout development
```

The workflow builds the pushed commit in ACR under tag
`<YYYY-MM-DD>-<sha7>` (+ `latest`), points tc-planner-app at that tag
(a tag change is what forces the pull), then waits until `/api/health`
reports `"build": "<that tag>"` and the page's JS bundle serves 200.
A red run means prod did NOT get that commit. Watch it at
https://github.com/HatNJacket/TC-Inventory-Planner/actions.
Azure sign-in is keyless OIDC: app registration
`github-tc-planner-deploy` trusts only this repo's main branch and holds
Contributor on tcplanneracr + Website Contributor on tc-planner-app.
The repo is PUBLIC: never commit secrets (`backend/.env` and
`setup_env.py` are gitignored).

### Manual deploy (emergency only)

If Actions is down, build from a clean checkout of main that is NOT
behind origin (a stale or dirty checkout silently rolls prod back; it
removed a week of work on 2026-09-14):

```powershell
git checkout main; git pull origin main; git status
az acr build --registry tcplanneracr --image tc-planner:<YYYY-MM-DD><x> --image tc-planner:latest --build-arg BUILD_TAG=<YYYY-MM-DD><x> --no-logs .
az webapp config container set --name tc-planner-app --resource-group shopify-automation-rg --container-image-name tcplanneracr.azurecr.io/tc-planner:<YYYY-MM-DD><x>
```

`--no-logs` avoids a Windows-only crash in the CLI's log streamer
(cp1252 `UnicodeEncodeError`); fetch logs afterwards with
`az acr task logs --registry tcplanneracr --run-id <id>`. The Dockerfile
builds the frontend itself, so no local Node or `npm run build` is needed.

### When deploys are NOT needed

- **Local dev** — `run-local.bat` / `py run_local.py` serves the app at
  http://localhost:8000 with auto-reload; `npm run dev` in `frontend/`
  gives Vite HMR on :3000. Nothing reaches Azure until main is pushed.

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
