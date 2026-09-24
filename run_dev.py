"""One-window development launcher. Double-click Start Shipping Planner.bat."""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

import run_local as setup


def check_ports():
    for port in (8000, 3000):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"Port {port} is already in use. Close your previous backend/frontend "
                    "windows (Ctrl+C), then open Start Shipping Planner again."
                ) from exc


def wait_until_ready(processes, timeout=120):
    pending = {"http://127.0.0.1:8000/api/health", "http://127.0.0.1:3000"}
    deadline = time.monotonic() + timeout
    while pending:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("A server stopped during startup. See the error above.")
        if time.monotonic() >= deadline:
            raise RuntimeError("Startup timed out. Check the server errors above and try again.")
        for url in tuple(pending):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        pending.remove(url)
            except (OSError, urllib.error.URLError):
                pass
        if pending:
            time.sleep(0.25)


def stop_servers(processes):
    for process in reversed(processes):
        if process.poll() is not None:
            continue
        if os.name == "nt":
            # Include Uvicorn's reload worker, but only our own process tree.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    processes = []
    try:
        check_ports()
        setup.ensure_venv()
        setup.ensure_env(False)
        npm = setup.ensure_npm()
        if not npm:
            raise RuntimeError("Node.js is required. Install Node.js LTS and try again.")
        # npm ci only when dependencies change; never build the frontend here.
        lock = setup.FRONTEND / "package-lock.json"
        stamp = setup.FRONTEND / "node_modules" / ".launcher-dependencies.sha256"
        fingerprint = setup.hashlib.sha256(
            (setup.FRONTEND / "package.json").read_bytes() + lock.read_bytes()
        ).hexdigest()
        if not stamp.exists() or stamp.read_text() != fingerprint:
            subprocess.check_call([npm, "ci"], cwd=setup.FRONTEND)
            stamp.write_text(fingerprint)
        node = setup.shutil.which("node")
        if not node:
            raise RuntimeError("Node.js could not be found after setup.")
        env = os.environ.copy()
        env.update(setup.LOCAL_OVERRIDES)
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        print("\nStarting Shipping Planner. Keep this window open; press Ctrl+C to stop both servers.", flush=True)
        processes.append(subprocess.Popen(
            [str(setup.VENV_PY), "-m", "uvicorn", "app.main:app", "--reload",
             "--host", "127.0.0.1", "--port", "8000"], cwd=setup.BACKEND, env=env, **options))
        processes.append(subprocess.Popen(
            [node, str(setup.FRONTEND / "node_modules/vite/bin/vite.js"),
             "--host", "127.0.0.1", "--port", "3000", "--strictPort"],
            cwd=setup.FRONTEND, env=env, **options))
        wait_until_ready(processes)
        print("\nReady: http://localhost:3000 - edits reload automatically.\n", flush=True)
        webbrowser.open("http://localhost:3000")
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("A development server stopped. See the error above; restart the launcher.")
    except KeyboardInterrupt:
        print("\nStopping Shipping Planner...", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"\nCould not run Shipping Planner: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_servers(processes)


if __name__ == "__main__":
    sys.exit(main())
