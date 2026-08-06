"""TC-Planner barcode label print agent.

Runs on the receiving PC. Polls TC-Planner for pending label jobs,
generates ZPL, and prints them via Zebra Browser Print on localhost.

Why this exists: Chrome's Local Network Access enforcement blocks
direct browser → localhost calls from public HTTPS sites, regardless
of cert trust or address-space hints. A native Python process isn't
subject to that restriction, so it can talk to Browser Print freely.

Install (one-time):
    pip install requests
    Copy this file to the receiving PC.

Run:
    set TC_PLANNER_URL=https://tc-planner-app.azurewebsites.net
    set TC_PLANNER_TOKEN=<your TC-Planner API token>
    python tc_label_agent.py

It will poll every POLL_SECONDS, print any queued labels, and log to
both stdout and ``tc_label_agent.log`` in the working directory.

To run on Windows boot, schedule via Task Scheduler:
    Action: python.exe
    Arguments: C:\\path\\to\\tc_label_agent.py
    Trigger: At log on (or At startup)
    Run whether user is logged on or not (with the receiving user account)

Browser Print configuration:
    1. Install Zebra Browser Print (zebra.com/browserprint)
    2. Run it; in its system tray UI, set your Zebra printer as default.
    3. The agent talks to Browser Print on http://localhost:9101 (HTTPS)
       or http://localhost:9100 (HTTP). It auto-detects which is alive.

Label format:
    2.25" × 1.25" direct-thermal label
    Layout: TELESCOPES CANADA centered top, Code-128 barcode middle,
    human-readable SKU centered bottom. DPI auto-detected from
    Browser Print's reported device name.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import time
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    sys.stderr.write(
        "Missing dependency `requests`. Install with: pip install requests\n"
    )
    sys.exit(1)

# ─── CONFIG ──────────────────────────────────────────────────────

TC_PLANNER_URL = os.environ.get(
    "TC_PLANNER_URL", "https://tc-planner-app.azurewebsites.net"
).rstrip("/")
TC_PLANNER_TOKEN = os.environ.get("TC_PLANNER_TOKEN", "")
AGENT_ID = os.environ.get("TC_LABEL_AGENT_ID", socket.gethostname())
POLL_SECONDS = float(os.environ.get("TC_LABEL_POLL_SECONDS", "3"))
BATCH_LIMIT = int(os.environ.get("TC_LABEL_BATCH_LIMIT", "25"))
LOG_PATH = os.environ.get("TC_LABEL_LOG_PATH", "tc_label_agent.log")

# Browser Print endpoints (try HTTPS first; HTTP is the older fallback).
# These run on the agent's own host, so the connection is purely local
# and immune to Chrome's LNA restrictions.
BROWSER_PRINT_BASES = [
    "https://localhost:9101",
    "http://localhost:9100",
]

# ─── LOGGING ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("tc-label-agent")


# ─── BROWSER PRINT BRIDGE ───────────────────────────────────────

class BrowserPrintError(Exception):
    pass


def _bp_get(path: str) -> Tuple[str, requests.Response]:
    """GET against the first Browser Print base that answers. Returns
    (base, response) so callers can cache the working base."""
    last_err = None
    for base in BROWSER_PRINT_BASES:
        try:
            r = requests.get(base + path, timeout=5, verify=False)
            if r.ok:
                return base, r
            last_err = "%s → HTTP %d" % (base, r.status_code)
        except Exception as e:
            last_err = "%s → %s" % (base, e)
    raise BrowserPrintError("Browser Print not reachable: %s" % last_err)


def _bp_post(path: str, body: str, base: Optional[str] = None) -> Tuple[str, requests.Response]:
    """POST to Browser Print. Tries the cached base first; falls back."""
    bases = [base] + [b for b in BROWSER_PRINT_BASES if b != base] if base else list(BROWSER_PRINT_BASES)
    last_err = None
    for b in bases:
        if not b:
            continue
        try:
            r = requests.post(
                b + path,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=15,
                verify=False,  # Browser Print uses a Zebra-signed cert
            )
            if r.ok:
                return b, r
            last_err = "%s → HTTP %d: %s" % (b, r.status_code, r.text[:200])
        except Exception as e:
            last_err = "%s → %s" % (b, e)
    raise BrowserPrintError("Browser Print write failed: %s" % last_err)


_cached_dpi: Optional[int] = None
_cached_base: Optional[str] = None


def detect_printer_dpi() -> int:
    """Probe Browser Print for the default printer's DPI. Looks for
    a `300dpi` / `203dpi` / `600dpi` marker in the device name. Falls
    back to 203 if no marker (most Zebra desktop printers including
    the ZP505 are 203 dpi natively)."""
    global _cached_dpi, _cached_base
    if _cached_dpi is not None:
        return _cached_dpi
    try:
        base, r = _bp_get("/default")
        _cached_base = base
        data = r.json()
        blob = " ".join(
            str(data.get(k) or "") for k in ("name", "uid", "connection", "manufacturer")
        ).lower()
        if re.search(r"300\s*dpi", blob):
            _cached_dpi = 300
        elif re.search(r"600\s*dpi", blob):
            _cached_dpi = 600
        elif re.search(r"203\s*dpi", blob):
            _cached_dpi = 203
        else:
            _cached_dpi = 203
        log.info("Detected printer dpi=%d (device=%r)", _cached_dpi, data.get("name"))
        return _cached_dpi
    except Exception as e:
        log.warning("DPI detect failed (%s); defaulting to 203", e)
        _cached_dpi = 203
        return _cached_dpi


# ─── ZPL GENERATION ─────────────────────────────────────────────

_ZPL_UNSAFE = re.compile(r"[\^~]|[^\x20-\x7E]")


def _zpl_safe(s: str) -> str:
    """Strip ZPL control chars (^ and ~) and any non-printable-ASCII
    so embedded user content can't break the format."""
    if not s:
        return ""
    return _ZPL_UNSAFE.sub(lambda m: "" if m.group(0) in "^~" else "?", str(s))


def build_label_zpl(barcode: str, sku: str, dpi: int = 203) -> str:
    """Build a single-label ZPL payload for a 2.25" × 1.25" label.

    Layout: TELESCOPES CANADA centered at top, Code-128 barcode in
    the middle, human-readable SKU centered at bottom. All sizes are
    declared in inches and converted to dots so the layout looks
    consistent across 203 dpi and 300 dpi printers.
    """
    safe_barcode = _zpl_safe(barcode)
    safe_sku = _zpl_safe(sku)

    width_dots = round(2.25 * dpi)
    height_dots = round(1.25 * dpi)

    def in2dots(in_):
        return int(round(in_ * dpi))

    header_y = in2dots(0.06)
    header_font = in2dots(0.11)

    barcode_y = in2dots(0.27)
    barcode_height = in2dots(0.45)
    module_width = 3 if dpi >= 300 else 2
    barcode_x = in2dots(0.18)

    sku_y = in2dots(0.86)
    sku_font = in2dots(0.16)

    return "".join([
        "^XA",
        "^PW%d" % width_dots,
        "^LL%d" % height_dots,
        "^LH0,0",
        "^FO0,%d^FB%d,1,0,C^A0N,%d,%d^FDTELESCOPES CANADA^FS"
        % (header_y, width_dots, header_font, header_font),
        "^FO%d,%d^BY%d,2,%d^BCN,%d,N,N,N^FD%s^FS"
        % (barcode_x, barcode_y, module_width, barcode_height, barcode_height, safe_barcode),
        "^FO0,%d^FB%d,1,0,C^A0N,%d,%d^FD%s^FS"
        % (sku_y, width_dots, sku_font, sku_font, safe_sku),
        "^XZ",
    ])


def build_label_batch_zpl(barcode: str, sku: str, qty: int, dpi: int = 203) -> str:
    """Concatenate ``qty`` copies of a single-label ZPL so Browser
    Print writes one stream per job."""
    one = build_label_zpl(barcode, sku, dpi)
    return one * max(1, int(qty))


def print_label(barcode: str, sku: str, qty: int = 1) -> None:
    """Build + dispatch a label batch. Raises BrowserPrintError on any
    transport / printer failure."""
    global _cached_base
    dpi = detect_printer_dpi()
    zpl = build_label_batch_zpl(barcode, sku, qty, dpi)
    base, _r = _bp_post("/default", zpl, base=_cached_base)
    _cached_base = base
    log.info("Printed %dx label  sku=%s  barcode=%s", qty, sku, barcode)


# ─── TC-PLANNER API ─────────────────────────────────────────────

class TcPlannerError(Exception):
    pass


def _tc_headers() -> Dict[str, str]:
    if not TC_PLANNER_TOKEN:
        raise TcPlannerError(
            "TC_PLANNER_TOKEN env var is not set. Set it to your TC-Planner API token."
        )
    return {
        "Authorization": "Bearer " + TC_PLANNER_TOKEN,
        "Content-Type": "application/json",
    }


def fetch_pending_jobs() -> List[Dict]:
    """Atomically claim up to BATCH_LIMIT pending label jobs."""
    url = "%s/api/labels/pending?agent_id=%s&limit=%d" % (
        TC_PLANNER_URL,
        requests.utils.quote(AGENT_ID),
        BATCH_LIMIT,
    )
    r = requests.get(url, headers=_tc_headers(), timeout=15)
    if not r.ok:
        raise TcPlannerError("GET /api/labels/pending → HTTP %d: %s" % (r.status_code, r.text[:200]))
    return r.json().get("jobs", [])


def mark_printed(job_id: int) -> None:
    url = "%s/api/labels/%d/printed" % (TC_PLANNER_URL, int(job_id))
    r = requests.post(url, headers=_tc_headers(), timeout=15)
    if not r.ok:
        raise TcPlannerError("POST printed → HTTP %d: %s" % (r.status_code, r.text[:200]))


def mark_failed(job_id: int, error: str) -> None:
    url = "%s/api/labels/%d/failed" % (TC_PLANNER_URL, int(job_id))
    r = requests.post(
        url, headers=_tc_headers(),
        data=json.dumps({"error": (error or "")[:1000]}),
        timeout=15,
    )
    if not r.ok:
        log.warning("POST failed-mark → HTTP %d: %s", r.status_code, r.text[:200])


# ─── MAIN POLL LOOP ─────────────────────────────────────────────

def process_one_job(job: Dict) -> None:
    job_id = int(job["id"])
    sku = str(job.get("sku") or "")
    barcode = str(job.get("barcode") or "")
    qty = int(job.get("qty") or 1)
    if not sku or not barcode or qty < 1:
        mark_failed(job_id, "Missing sku/barcode or invalid qty")
        log.warning("Job %d skipped: missing data", job_id)
        return
    try:
        print_label(barcode, sku, qty)
        mark_printed(job_id)
    except BrowserPrintError as e:
        # Don't mark failed on transient print errors — the unack'd
        # claim will time out (5 min) and become pending again so a
        # later poll retries. Just surface the error and stop the
        # batch; next poll will pick up where we left off.
        log.error("Job %d print failed: %s", job_id, e)
        raise


def poll_once() -> int:
    """Single poll iteration. Returns number of jobs processed."""
    try:
        jobs = fetch_pending_jobs()
    except TcPlannerError as e:
        log.error("Fetch failed: %s", e)
        return 0
    if not jobs:
        return 0
    log.info("Claimed %d label job(s)", len(jobs))
    processed = 0
    for job in jobs:
        try:
            process_one_job(job)
            processed += 1
        except BrowserPrintError:
            # Stop the batch — printer is down, retry on next poll.
            return processed
        except Exception as e:
            log.exception("Job %s unexpected error: %s", job.get("id"), e)
            try:
                mark_failed(int(job["id"]), str(e)[:500])
            except Exception:
                pass
    return processed


def main() -> int:
    log.info(
        "TC-Planner label agent starting. url=%s agent_id=%s poll=%.1fs",
        TC_PLANNER_URL, AGENT_ID, POLL_SECONDS,
    )
    if not TC_PLANNER_TOKEN:
        log.error("TC_PLANNER_TOKEN env var is not set. Exiting.")
        return 2

    # Quick startup probe so the operator sees an immediate error if
    # Browser Print isn't reachable, instead of silent polling.
    try:
        detect_printer_dpi()
    except Exception as e:
        log.warning(
            "Browser Print probe failed at startup (%s). Will retry on next print.",
            e,
        )

    while True:
        try:
            poll_once()
        except KeyboardInterrupt:
            log.info("Interrupted; shutting down.")
            return 0
        except Exception as e:
            log.exception("Poll loop error: %s", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    # Suppress noisy InsecureRequestWarning from urllib3 when verify=False
    try:
        from urllib3.exceptions import InsecureRequestWarning
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    except Exception:
        pass
    sys.exit(main())
