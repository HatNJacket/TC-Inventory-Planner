# TC-Planner Label Agent

A small Python script that runs on the receiving PC, polls TC-Planner
for pending barcode label jobs, and prints them via Zebra Browser Print.

## Why this exists

Chrome's Local Network Access (LNA) enforcement blocks browser → localhost
calls from public HTTPS sites — even with a trusted certificate. That
makes the "browser-direct-to-printer" pattern unreliable in modern
Chrome. A native Python process isn't subject to LNA, so it can talk to
Browser Print on `localhost:9101` freely.

The agent is the bridge:

```
TC-Planner UI → /api/labels (queue)         (HTTPS, public)
                    ↓
Cloud DB: pending_label_jobs

Local Agent (this script) → polls /api/labels/pending every 3s
                          → generates ZPL
                          → POSTs Browser Print on localhost
                          → POSTs /api/labels/{id}/printed
```

## Prerequisites

1. **Python 3.8+** on the receiving PC.
2. **`requests` package** — install via `pip install requests`.
3. **Zebra Browser Print** installed and running, with your Zebra
   ZP505 (or any ZPL-compatible printer) selected as the default device.
   - Download: https://www.zebra.com/us/en/software/printer-software/browser-print.html
   - After install, open the Browser Print system tray icon → Settings →
     confirm your printer shows under "Default Devices".

## Configuration

Set these environment variables before running:

| Variable | Required | Default | Description |
|---|---|---|---|
| `TC_PLANNER_URL` | yes | `https://tc-planner-app.azurewebsites.net` | Base URL of your TC-Planner deployment |
| `TC_PLANNER_TOKEN` | yes | _(none)_ | Your TC-Planner API token (same one the browser uses) |
| `TC_LABEL_AGENT_ID` | no | `<hostname>` | Identifier surfaced in claim audit; useful if running on multiple PCs |
| `TC_LABEL_POLL_SECONDS` | no | `3` | How often to poll for new jobs |
| `TC_LABEL_BATCH_LIMIT` | no | `25` | Max jobs claimed per poll |
| `TC_LABEL_LOG_PATH` | no | `tc_label_agent.log` | Where to write the log file |

## Running

```cmd
set TC_PLANNER_URL=https://tc-planner-app.azurewebsites.net
set TC_PLANNER_TOKEN=<your-token>
python tc_label_agent.py
```

You'll see startup logs and any printed/skipped jobs as they're processed.

## Running at logon (Windows Task Scheduler)

To have the agent start automatically when you log into Windows:

1. Open **Task Scheduler** → **Create Task...**
2. **General** tab:
   - Name: `TC-Planner Label Agent`
   - Run only when user is logged on (or "whether logged on or not" if you've added the user as a service account)
3. **Triggers** tab:
   - New → At log on (your user account)
4. **Actions** tab:
   - New → Start a program
   - Program: `C:\Path\To\python.exe` (or just `python` if on PATH)
   - Arguments: `C:\Path\To\tc_label_agent.py`
   - Start in: `C:\Path\To\` (so the log file lands somewhere predictable)
5. **Conditions** tab:
   - Untick "Start the task only if the computer is on AC power"
6. Set the env vars in the user's environment (System Properties →
   Environment Variables) so the agent picks them up at logon.

## Verifying it works

1. Start the agent.
2. Open TC-Planner → **Settings** → click **Queue Test Label**.
3. The toast confirms the job was queued.
4. Within `TC_LABEL_POLL_SECONDS` (default 3s), the agent claims it,
   prints, and the label drops out of the printer.

If a label doesn't print:

- **Check `tc_label_agent.log`** in the working directory.
- **Common error:** `Browser Print not reachable` → confirm Browser Print
  is running (system tray icon visible).
- **Common error:** `TC_PLANNER_TOKEN env var is not set` → set it before
  running.
- **Common error:** `HTTP 401` from TC-Planner → token is invalid or expired.

## Failure handling

- **Transient print errors** (printer offline, paper out): the claim
  expires after 5 minutes and the job becomes pending again. Next poll
  retries.
- **Permanent errors** (missing barcode, malformed data): marked as
  `failed` in the database. Operator can investigate in SQL or via a
  future "Failed labels" UI.

## Label format

2.25" × 1.25" direct-thermal label.

```
┌───────────────────────────────────────┐
│         TELESCOPES CANADA             │  header text
│                                       │
│   ████ █ ██  █ ████  █ ██ █ ████      │  Code-128 barcode
│   ████ █ ██  █ ████  █ ██ █ ████      │
│                                       │
│              S20216                   │  human-readable SKU
└───────────────────────────────────────┘
```

DPI is auto-detected from Browser Print's printer info. ZP505 (203 dpi)
and ZD420-300dpi (300 dpi) both produce the same physical layout because
all positions are declared in inches and converted at runtime.

## Tweaking the layout

Edit `build_label_zpl()` in `tc_label_agent.py`. All positions are in
inches. Common changes:

- **Bigger/smaller header text:** change `header_font = in2dots(0.11)`.
- **Replace text header with TC logo image:** convert your PNG to a ZPL
  `^GF` graphic block and emit it instead of the `^FD TELESCOPES CANADA`
  field. Tools like [LabelDesign](https://labelary.com/viewer.html)
  can help generate the `^GF` payload.
- **Move the barcode:** `barcode_x` and `barcode_y` control the origin.
- **Different label size:** edit the `2.25` and `1.25` literals at the
  top of `build_label_zpl()`.
