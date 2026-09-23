"""
TC Inventory Planner - run a local copy on Windows (or anywhere).

    py run_local.py            # http://localhost:8000 (this PC only)
    py run_local.py --lan      # also reachable from other PCs on the network
    py run_local.py --port 8001

What it does, every run:
  1. Creates backend/.venv (Python 3.11+) and installs requirements when
     requirements.txt changes.
  2. Makes sure backend/.env exists (copies the template the first time
     and stops so you can fill it in).
  3. Checks for the SQL Server ODBC driver and says how to install it.
  4. Builds the web page (frontend -> backend/static) when Node is
     installed and the source changed.
  5. Starts the API with auto-reload.

NOTE: backend/.env points at the REAL Azure SQL database and Shopify
store, so anything you save locally changes live data.
"""
import argparse
import hashlib
import os
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
ENV_TEMPLATE = BACKEND / ".env.template"
STATIC_INDEX = BACKEND / "static" / "index.html"


def step(msg):
    print(f"\n==> {msg}", flush=True)


def warn(msg):
    print(f"    ! {msg}", flush=True)


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


def ensure_env():
    step("Settings (backend/.env)")
    if ENV_FILE.exists():
        print("    Found backend/.env")
        return
    shutil.copy(ENV_TEMPLATE, ENV_FILE)
    print("    Created backend/.env from the template.\n"
          "    Fill in the real values, then run this again. The live values are in\n"
          "    Azure portal -> App Services -> tc-planner-app -> Settings ->\n"
          "    Environment variables (SHOPIFY_*, AZURE_SQL_*, TC_PLANNER_AUTH_TOKEN).")
    sys.exit(1)


def check_odbc():
    step("SQL Server ODBC driver")
    r = subprocess.run([str(VENV_PY), "-c", "import pyodbc; print('|'.join(pyodbc.drivers()))"],
                       capture_output=True, text=True)
    drivers = r.stdout.strip().split("|") if r.returncode == 0 else []
    for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if name in drivers:
            print(f"    Using {name}")
            return
    warn("ODBC Driver 18 for SQL Server is not installed - the app will start,\n"
         "      but every database page will fail. Install it with:\n"
         "        winget install --id Microsoft.msodbcsql.18 -e\n"
         "      (or download it from Microsoft), then run this again.")


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
    npm = shutil.which("npm")
    built = STATIC_INDEX.exists()
    if not npm:
        if built:
            warn("Node.js not found - using the existing build in backend/static.")
        else:
            warn("Node.js is not installed, so the web page can't be built.\n"
                 "      The API still works (see /docs). To get the page, install Node:\n"
                 "        winget install --id OpenJS.NodeJS.LTS -e\n"
                 "      then close this window, open a new one, and run this again.")
        return
    src_changed = newest_mtime(FRONTEND / "src", FRONTEND / "index.html",
                               FRONTEND / "package.json", FRONTEND / "vite.config.js")
    if built and STATIC_INDEX.stat().st_mtime >= src_changed:
        print("    Web page is up to date.")
        return
    if not (FRONTEND / "node_modules").exists():
        print("    Installing frontend packages (first run only)...")
        subprocess.check_call([npm, "ci"], cwd=FRONTEND)
    print("    Building the web page...")
    subprocess.check_call([npm, "run", "build"], cwd=FRONTEND)


def main():
    ap = argparse.ArgumentParser(description="Run TC Inventory Planner locally.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true",
                    help="listen on all network interfaces so other PCs can connect")
    ap.add_argument("--skip-frontend", action="store_true",
                    help="don't rebuild the web page (use `npm run dev` for live editing)")
    args = ap.parse_args()

    ensure_venv()
    ensure_env()
    check_odbc()
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
