"""
TC Inventory Planner - run a local copy on Windows (or anywhere).

    py run_local.py            # http://localhost:8000 (this PC only)
    py run_local.py --lan      # also reachable from other PCs on the network
    py run_local.py --port 8001
    py run_local.py --refresh-env          # re-pull backend/.env from Azure
    py run_local.py --make-setup-script    # write setup_env.py to hand out

What it does, every run:
  1. Creates backend/.venv (Python 3.11+) and installs requirements when
     requirements.txt changes.
  2. Makes sure backend/.env holds real settings. In order it tries:
       a. setup_env.py next to this file (gitignored, has the secrets
          built in - someone with Azure access makes it with
          --make-setup-script and hands it over privately), then
       b. the live tc-planner-app settings, if the Azure CLI is signed in.
  3. Installs the SQL Server ODBC driver and Node.js with winget when
     they're missing (Windows asks for permission once).
  4. Checks the database connects; if the Azure SQL firewall blocks this
     PC's IP it offers to add a rule (needs the Azure CLI).
  5. Builds the web page (frontend -> backend/static) when it changed.
  6. Starts the API with auto-reload.

Running this accepts the Microsoft ODBC driver and Node.js licence terms
when it installs them.

NOTE: backend/.env points at the REAL Azure SQL database and Shopify
store, so anything you save locally changes live data. The RFID label
bridge is always left OFF locally (RFID_STATION_KEY blank) so a dev copy
can never queue label prints in the warehouse.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQS = BACKEND / "requirements.txt"
REQ_STAMP = VENV / ".requirements.sha256"
ENV_FILE = BACKEND / ".env"
SETUP_SCRIPT = ROOT / "setup_env.py"
STATIC_INDEX = BACKEND / "static" / "index.html"

AZURE_APP = "tc-planner-app"
AZURE_RG = "shopify-automation-rg"
ODBC_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")

# App Service plumbing that means nothing to a local run.
SKIP_PREFIXES = ("WEBSITE_", "WEBSITES_", "DOCKER_", "APPINSIGHTS", "APPLICATIONINSIGHTS")
# Forced values for local copies: the RFID bridge stays OFF so a dev
# machine never relays label jobs to the live warehouse printer.
LOCAL_OVERRIDES = {"RFID_STATION_KEY": ""}
LOCAL_DROP = {"RFID_APP_URL"}
# Strings only the unfilled template contains.
TEMPLATE_MARKERS = ("shpat_xxxxxxxx", "your-password", "your-secret-token-here")


def step(msg):
    print(f"\n==> {msg}", flush=True)


def warn(msg):
    print(f"    ! {msg}", flush=True)


def is_windows() -> bool:
    return os.name == "nt"


# ─── Python environment ─────────────────────────────────────────

def venv_ok() -> bool:
    if not VENV_PY.exists():
        return False
    r = subprocess.run([str(VENV_PY), "-c", "import sys; print(sys.version_info >= (3, 11))"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "True"


def ensure_venv():
    step("Python environment")
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11 or newer is required (this is {sys.version.split()[0]}).\n"
                 "Install it from https://www.python.org/downloads/ and run: py run_local.py")
    if not venv_ok():
        if VENV.exists():
            print("    Existing backend/.venv is broken or too old - rebuilding it.")
            shutil.rmtree(VENV)
        print(f"    Creating backend/.venv with Python {sys.version.split()[0]}")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])

    want = hashlib.sha256(REQS.read_bytes()).hexdigest()
    have = REQ_STAMP.read_text().strip() if REQ_STAMP.exists() else ""
    if want != have:
        print("    Installing requirements (first run takes a minute)...")
        subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "-q",
                               "--disable-pip-version-check", "-r", str(REQS)])
        REQ_STAMP.write_text(want)
    else:
        print("    Requirements already installed.")


# ─── Settings (backend/.env) ────────────────────────────────────

def az_exe():
    return shutil.which("az")


def fetch_azure_settings():
    """The live app's settings as {name: value}, or None when the Azure
    CLI is missing / signed out / lacks access."""
    az = az_exe()
    if not az:
        return None
    r = subprocess.run([az, "webapp", "config", "appsettings", "list",
                        "-n", AZURE_APP, "-g", AZURE_RG, "-o", "json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        rows = json.loads(r.stdout)
    except ValueError:
        return None
    return {row["name"]: row.get("value") or "" for row in rows}


def local_settings(live: dict) -> dict:
    out = {}
    for name in sorted(live):
        if name.startswith(SKIP_PREFIXES) or name in LOCAL_DROP:
            continue
        out[name] = live[name]
    out.update(LOCAL_OVERRIDES)
    return out


def env_quote(value: str) -> str:
    # Single quotes are literal in python-dotenv; fall back to escaped
    # double quotes for the rare value that contains one.
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_env(settings: dict, source: str) -> str:
    lines = [
        "# TC Inventory Planner - local settings. NEVER commit this file.",
        f"# Generated {datetime.date.today()} from {source}.",
        "# Points at the LIVE database and Shopify store. RFID bridge is OFF.",
        "",
    ]
    lines += [f"{k}={env_quote(v)}" for k, v in settings.items()]
    return "\n".join(lines) + "\n"


def env_ready() -> bool:
    if not ENV_FILE.exists():
        return False
    text = ENV_FILE.read_text(encoding="utf-8", errors="replace")
    return not any(m in text for m in TEMPLATE_MARKERS)


def write_env_from_azure(live: dict):
    ENV_FILE.write_text(render_env(local_settings(live), f"Azure app {AZURE_APP}"),
                        encoding="utf-8")


# setup_env.py is this template with the live settings pasted in. It
# repeats env_quote/render_env so it runs with no other files around.
SETUP_TEMPLATE = r'''"""
TC Inventory Planner - local settings installer. CONTAINS LIVE SECRETS.

Generated __DATE__ from Azure app __APP__ by
`py run_local.py --make-setup-script`. It is gitignored: share it
privately (never commit it, never post it in a group chat). Drop it next
to run_local.py; run-local.bat uses it automatically, or run
`py setup_env.py` to (re)write backend/.env yourself.
"""
from pathlib import Path

SETTINGS = __SETTINGS__


def quote(value):
    if "'" not in value:
        return "'" + value + "'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    env = Path(__file__).resolve().parent / "backend" / ".env"
    lines = [
        "# TC Inventory Planner - local settings. NEVER commit this file.",
        "# Written by setup_env.py (settings from __DATE__).",
        "# Points at the LIVE database and Shopify store. RFID bridge is OFF.",
        "",
    ]
    lines += [k + "=" + quote(v) for k, v in SETTINGS.items()]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("    Wrote " + str(env) + " (" + str(len(SETTINGS)) + " settings)")


if __name__ == "__main__":
    main()
'''


def make_setup_script():
    """Write setup_env.py: a gitignored, self-contained script with the
    live settings built in, for someone without Azure access."""
    step("Writing setup_env.py from Azure")
    live = fetch_azure_settings()
    if live is None:
        sys.exit("    Couldn't read the Azure settings. Run `az login` first "
                 "(needs access to tc-planner-app).")
    settings = local_settings(live)
    body = (SETUP_TEMPLATE
            .replace("__DATE__", str(datetime.date.today()))
            .replace("__APP__", AZURE_APP)
            .replace("__SETTINGS__", json.dumps(settings, indent=4)))
    SETUP_SCRIPT.write_text(body, encoding="utf-8")
    print(f"    Wrote {SETUP_SCRIPT.name} ({len(settings)} settings). It is gitignored -")
    print("    hand it over privately; it goes next to run_local.py.")


def ensure_env(refresh: bool):
    step("Settings (backend/.env)")
    if refresh:
        live = fetch_azure_settings()
        if live is None:
            sys.exit("    --refresh-env needs the Azure CLI signed in: run `az login` first.")
        write_env_from_azure(live)
        print(f"    Re-pulled backend/.env from Azure ({AZURE_APP}).")
        return
    if env_ready():
        print("    Found backend/.env")
        return
    if SETUP_SCRIPT.exists():
        print(f"    Writing backend/.env from {SETUP_SCRIPT.name}...")
        subprocess.check_call([sys.executable, str(SETUP_SCRIPT)])
        if env_ready():
            return
        warn(f"{SETUP_SCRIPT.name} ran but backend/.env still looks unfilled.")
    live = fetch_azure_settings()
    if live is not None:
        write_env_from_azure(live)
        print(f"    Pulled backend/.env from Azure ({AZURE_APP}).")
        return
    sys.exit("    backend/.env is missing and there's no way to fill it in automatically.\n"
             "    Either:\n"
             "      - put setup_env.py (ask whoever manages the planner) next to\n"
             "        run_local.py and run this again, or\n"
             "      - install the Azure CLI, run `az login` with an account that can\n"
             "        see tc-planner-app, and run this again.")


# ─── Prerequisites (winget) ─────────────────────────────────────

def winget_install(package_id: str, label: str) -> bool:
    winget = shutil.which("winget") if is_windows() else None
    if not winget:
        return False
    print(f"    Installing {label} with winget (Windows may ask for permission)...", flush=True)
    r = subprocess.run([winget, "install", "--id", package_id, "-e", "--silent",
                        "--accept-package-agreements", "--accept-source-agreements"])
    return r.returncode == 0


def installed_odbc_driver():
    r = subprocess.run([str(VENV_PY), "-c", "import pyodbc; print('|'.join(pyodbc.drivers()))"],
                       capture_output=True, text=True)
    drivers = r.stdout.strip().split("|") if r.returncode == 0 else []
    return next((d for d in ODBC_DRIVERS if d in drivers), None)


def ensure_odbc() -> bool:
    step("SQL Server ODBC driver")
    driver = installed_odbc_driver()
    if not driver:
        winget_install("Microsoft.msodbcsql.18", "ODBC Driver 18 for SQL Server")
        driver = installed_odbc_driver()
    if driver:
        print(f"    Using {driver}")
        return True
    warn("ODBC Driver 18 for SQL Server is missing and couldn't be installed\n"
         "      automatically. The app will start, but database pages will fail.\n"
         "      Install it by hand:  winget install --id Microsoft.msodbcsql.18 -e")
    return False


def find_npm():
    npm = shutil.which("npm")
    if npm:
        return npm
    if is_windows():
        # A fresh winget install isn't on this window's PATH yet.
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                for cand in (Path(base) / "nodejs" / "npm.cmd",
                             Path(base) / "Programs" / "nodejs" / "npm.cmd"):
                    if cand.exists():
                        return str(cand)
    return None


def ensure_npm():
    npm = find_npm()
    if not npm and winget_install("OpenJS.NodeJS.LTS", "Node.js LTS"):
        npm = find_npm()
    if npm:
        # npm.cmd shells out to node.exe, which must be on PATH too.
        node_dir = str(Path(npm).parent)
        if node_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
    return npm


# ─── Database reachability ──────────────────────────────────────

DB_PROBE = """
import pyodbc
from app.config import config
try:
    pyodbc.connect(config.azure_sql_connection_string, timeout=20).close()
    print("OK")
except Exception as e:
    print("ERR " + str(e))
"""


def check_database():
    step("Database connection")
    r = subprocess.run([str(VENV_PY), "-c", DB_PROBE], cwd=BACKEND,
                       capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip()
    if out.startswith("OK"):
        print("    Connected to Azure SQL.")
        return
    ip = re.search(r"IP address '([0-9a-fA-F.:]+)'", out)
    if not ip:
        warn("Couldn't connect to the database (the app will still start):\n      "
             + out[-400:])
        return
    ip = ip.group(1)
    warn(f"The Azure SQL firewall is blocking this PC's internet address ({ip}).")
    if not offer_firewall_rule(ip):
        print("      Ask someone with Azure access to allow it: Azure portal -> SQL servers ->\n"
              "      (the planner's server) -> Networking -> Add a firewall rule for "
              f"{ip}.")


def offer_firewall_rule(ip: str) -> bool:
    az = az_exe()
    if not az or not sys.stdin.isatty():
        return False
    server_host = ""
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("AZURE_SQL_SERVER="):
            server_host = line.split("=", 1)[1].strip().strip("'\"")
    server = server_host.split(".")[0]
    if not server:
        return False
    rg = subprocess.run([az, "sql", "server", "list", "--query",
                         f"[?name=='{server}'].resourceGroup | [0]", "-o", "tsv"],
                        capture_output=True, text=True).stdout.strip()
    if not rg:
        return False  # not signed in, or no access to the SQL server
    answer = input(f"      Add a firewall rule on {server} for {ip}? [y/N] ").strip().lower()
    if answer != "y":
        return False
    rule = "local-dev-" + re.sub(r"[^A-Za-z0-9-]", "-", os.environ.get("COMPUTERNAME", "pc"))
    r = subprocess.run([az, "sql", "server", "firewall-rule", "create", "-g", rg, "-s", server,
                        "-n", rule, "--start-ip-address", ip, "--end-ip-address", ip],
                       capture_output=True, text=True)
    if r.returncode != 0:
        warn("Adding the rule failed: " + (r.stderr or r.stdout).strip()[-300:])
        return False
    print(f"    Added firewall rule '{rule}'. It can take a few minutes to apply;\n"
          "    if pages still fail, stop (Ctrl+C) and run this again shortly.")
    return True


# ─── Frontend ───────────────────────────────────────────────────

def newest_mtime(*paths: Path) -> float:
    newest = 0.0
    for p in paths:
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    newest = max(newest, f.stat().st_mtime)
    return newest


def build_frontend():
    step("Web page (frontend)")
    src_changed = newest_mtime(FRONTEND / "src", FRONTEND / "index.html",
                               FRONTEND / "package.json", FRONTEND / "vite.config.js")
    built = STATIC_INDEX.exists()
    if built and STATIC_INDEX.stat().st_mtime >= src_changed:
        print("    Web page is up to date.")
        return
    npm = ensure_npm()
    if not npm:
        if built:
            warn("Node.js not found - using the existing (older) build in backend/static.")
        else:
            warn("Node.js is missing and couldn't be installed automatically, so the web\n"
                 "      page can't be built. The API still works (see /docs). Install it by\n"
                 "      hand:  winget install --id OpenJS.NodeJS.LTS -e")
        return
    if not (FRONTEND / "node_modules").exists():
        print("    Installing frontend packages (first run only)...")
        subprocess.check_call([npm, "ci"], cwd=FRONTEND)
    print("    Building the web page...")
    subprocess.check_call([npm, "run", "build"], cwd=FRONTEND)


# ─── Main ───────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Run TC Inventory Planner locally.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true",
                    help="listen on all network interfaces so other PCs can connect")
    ap.add_argument("--skip-frontend", action="store_true",
                    help="don't rebuild the web page (use `npm run dev` for live editing)")
    ap.add_argument("--refresh-env", action="store_true",
                    help="re-pull backend/.env from Azure (after secrets change)")
    ap.add_argument("--make-setup-script", action="store_true",
                    help="write gitignored setup_env.py with the live settings, then exit")
    args = ap.parse_args()

    if args.make_setup_script:
        make_setup_script()
        return

    ensure_venv()
    ensure_env(args.refresh_env)
    if ensure_odbc():
        check_database()
    if not args.skip_frontend:
        build_frontend()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    step("Starting TC Inventory Planner")
    print(f"    App:      http://localhost:{args.port}")
    print(f"    API docs: http://localhost:{args.port}/docs")
    if args.lan:
        print(f"    Other PCs: http://<this PC's IP>:{args.port} (Windows may ask to allow it through the firewall)")
    print("    Press Ctrl+C to stop.\n", flush=True)
    try:
        subprocess.call([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--reload",
                         "--host", host, "--port", str(args.port)], cwd=BACKEND)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
