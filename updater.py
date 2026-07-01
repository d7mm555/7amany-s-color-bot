"""Self-update client.

The GitHub repo is the single source of truth for the app's version: a plain-text `VERSION`
file at the repo root. On startup the app compares its local VERSION to the one on the `main`
branch; if the repo's is newer, the app offers an Update button that re-downloads the tracked
files below and restarts.

`server_config.json` is deliberately never touched by an update -- it holds the user's own
Apps Script URL and must survive updates untouched.
"""

import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO_RAW_BASE = "https://raw.githubusercontent.com/d7mm555/7amany-s-color-bot/main/"

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_VERSION_FILE = os.path.join(_APP_DIR, "VERSION")

TRACKED_FILES = [
    "VERSION",
    "app.py",
    "worker.py",
    "overlays.py",
    "licensing.py",
    "updater.py",
    "requirements.txt",
    "run.bat",
]


def local_version():
    try:
        with open(_VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _parse(v):
    try:
        return tuple(int(p) for p in v.strip().split("."))
    except Exception:
        return None


def check_for_update(timeout=6):
    """Returns (update_available, latest_version, error). Network/parse failures report no
    update available rather than raising, so a flaky connection never blocks the app."""
    try:
        with urllib.request.urlopen(REPO_RAW_BASE + "VERSION", timeout=timeout) as resp:
            remote = resp.read().decode("utf-8").strip()
    except urllib.error.URLError as exc:
        return False, None, f"Couldn't check for updates ({exc.reason})."
    except Exception as exc:
        return False, None, f"Update check failed ({exc})."

    local_t, remote_t = _parse(local_version()), _parse(remote)
    if local_t is not None and remote_t is not None:
        newer = remote_t > local_t
    else:
        newer = remote != local_version()
    return newer, remote, None


def download_update(timeout=15):
    """Fetch every tracked file from the repo and overwrite the local copy.
    Returns (ok, error)."""
    fetched = {}
    for name in TRACKED_FILES:
        try:
            with urllib.request.urlopen(REPO_RAW_BASE + name, timeout=timeout) as resp:
                fetched[name] = resp.read()
        except Exception as exc:
            return False, f"Failed to download {name} ({exc})"

    for name, data in fetched.items():
        path = os.path.join(_APP_DIR, name)
        with open(path, "wb") as f:
            f.write(data)
    return True, None


def restart_app():
    """Relaunch the app from the (now-updated) source and end the current process."""
    subprocess.Popen([sys.executable, os.path.join(_APP_DIR, "app.py")], cwd=_APP_DIR)
    os._exit(0)
