"""
Bring the whole thing up, publicly reachable, with one command.

    python scripts/go_live.py

Starts the tunnel, points the config at whatever URL it hands back, starts the
API and the worker, and prints the guest URL to put behind the QR code.
Ctrl-C stops all three.

WHY THIS IS A SCRIPT AND NOT A CHECKLIST

A quick tunnel gets a new random hostname every time it starts, and three
separate settings have to agree with it:

    GUEST_BASE_URL    what the QR code encodes
    PUBLIC_BASE_URL   what thumbnail and upload URLs are built from
    CORS_ORIGINS_RAW  which origins the API will answer

Miss PUBLIC_BASE_URL and the gallery loads with every image broken, because the
thumbnails point at a machine the guest's phone cannot reach. Miss GUEST_BASE_URL
and the printed QR points at the last session's dead tunnel. Doing this by hand
at a venue, in a hurry, is how an evening gets lost, so it is done here instead.

    --keep-url    reuse the URL already in .env (a named tunnel, or a rerun
                  where QR codes are already printed)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
DIST = ROOT.parent / "web" / "dist"

CLOUDFLARED = shutil.which("cloudflared") or r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

procs: list[subprocess.Popen] = []

# Line-buffered on purpose. The child processes write straight to the same
# terminal, so with the default block buffering their output appears while
# this script's own lines, including the guest links, sit unseen in a buffer
# until exit. That is the same trap that makes a redirected worker look dead.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:  # pragma: no cover - very old interpreters
    pass


def die(msg: str) -> None:
    print(f"\n  {msg}\n")
    stop_all()
    sys.exit(1)


def stop_all() -> None:
    for p in reversed(procs):
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
    for p in reversed(procs):
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()


def set_env_values(values: dict[str, str]) -> None:
    """Rewrite keys in .env in place, leaving everything else untouched."""
    lines = ENV.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        for k, v in values.items():
            if line.startswith(f"{k}="):
                lines[i] = f"{k}={v}"
                seen.add(k)
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def current_url() -> str | None:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("GUEST_BASE_URL="):
            return line.split("=", 1)[1].strip() or None
    return None


def start_tunnel() -> str:
    if not pathlib.Path(CLOUDFLARED).exists():
        die("cloudflared not found. Install it, or use --keep-url with your own URL.")
    print("  starting tunnel ...", end="", flush=True)
    p = subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    procs.append(p)
    deadline = time.time() + 60
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line and p.poll() is not None:
            die("cloudflared exited before printing a URL.")
        m = URL_RE.search(line or "")
        if m:
            print(f" {m.group(0)}")
            return m.group(0)
    die("cloudflared did not print a URL within 60s.")
    raise AssertionError  # unreachable, keeps type checkers quiet


def guest_links(url: str) -> None:
    """Print the QR target for every event currently accepting guests."""
    sys.path.insert(0, str(ROOT))
    try:
        from sqlalchemy import create_engine, text

        from app.config import settings
        eng = create_engine(settings().database_url, connect_args={"connect_timeout": 5})
        with eng.connect() as c:
            rows = c.execute(text(
                "SELECT name, status, qr_token FROM events ORDER BY created_at DESC"
            )).all()
    except Exception as exc:  # noqa: BLE001 - a bad link list must not stop the server
        print(f"  (could not read events: {exc})")
        return

    if not rows:
        print("  no events yet. Create one in the studio app.")
        return
    print("\n  guest links:")
    for name, status, token in rows:
        mark = "  <- live" if status == "active" else f"  ({status})"
        print(f"    {url}/g/{token}   {name}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-url", action="store_true",
                    help="reuse the URL already in .env instead of starting a tunnel")
    args = ap.parse_args()

    os.chdir(ROOT)

    if not DIST.is_dir():
        die("web/dist is missing. Run:  npm run build --workspace web")

    # Fail here rather than after three processes are up.
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import create_engine, text

    from app.config import settings
    try:
        create_engine(settings().database_url,
                      connect_args={"connect_timeout": 5}).connect().execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        die("Postgres is not reachable. Start Docker Desktop, then:  docker compose up -d")

    if args.keep_url:
        url = current_url()
        if not url:
            die("--keep-url given but GUEST_BASE_URL is empty in .env")
        print(f"  reusing {url}")
    else:
        url = start_tunnel()
        set_env_values({
            "GUEST_BASE_URL": url,
            "PUBLIC_BASE_URL": url,
            "CORS_ORIGINS_RAW":
                f"{url},http://localhost:5174,http://localhost:5175,file://,null",
        })
        print("  .env updated")

    # Started after .env is written: settings are read once per process, so a
    # server booted before the rewrite would serve the previous tunnel's URLs.
    print("  starting api ...", flush=True)
    procs.append(subprocess.Popen(
        [sys.executable, "-u", "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8000"]))
    time.sleep(4)
    print("  starting worker ...", flush=True)
    # -u because Python buffers stdout when redirected, and a buffered worker
    # looks dead while it is working perfectly.
    procs.append(subprocess.Popen([sys.executable, "-u", "-m", "app.worker"]))

    time.sleep(3)
    guest_links(url)
    print(f"\n  studio app:  npm run studio        (talks to localhost:8000)")
    print(f"  admin panel: {url}/admin")
    print("\n  ctrl-c to stop everything.\n")

    def on_signal(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    print("\n  a process exited, shutting the rest down")
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  stopping ...")
    finally:
        stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
