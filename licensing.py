"""Token licensing client.

The list of valid/redeemed tokens lives in a Google Sheet, not in this file — this module only
talks to a small Google Apps Script "web app" endpoint (its URL is read from server_config.json,
see TOKENS.md for how to set that up) that checks/marks tokens in the sheet. Locally we only
cache a device id and a "licensed" flag once a redemption succeeds, so the app never has to ask
for a token again on this machine.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_CONFIG_FILE = os.path.join(_APP_DIR, "server_config.json")

_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "7amanys-color-bot")
_LICENSE_FILE = os.path.join(_DATA_DIR, "license.json")


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_license(data):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _apps_script_url():
    return (_load_json(_SERVER_CONFIG_FILE).get("apps_script_url") or "").strip()


def get_device_id():
    data = _load_json(_LICENSE_FILE)
    if data.get("device_id"):
        return data["device_id"]
    data["device_id"] = uuid.uuid4().hex
    _save_license(data)
    return data["device_id"]


def is_licensed():
    return bool(_load_json(_LICENSE_FILE).get("licensed"))


def redeem_token(token, timeout=6):
    """Attempt to redeem `token` against the cloud sheet. Returns (ok: bool, message: str)."""
    token = (token or "").strip()
    if not token:
        return False, "No token entered."

    url = _apps_script_url()
    if not url:
        return False, "Token server isn't configured yet (server_config.json is empty)."

    device_id = get_device_id()
    query = urllib.parse.urlencode({"action": "redeem", "token": token, "device": device_id})
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return False, f"Couldn't reach the token server ({exc.reason})."
    except Exception as exc:
        return False, f"Token check failed ({exc})."

    if result.get("ok"):
        data = _load_json(_LICENSE_FILE)
        data["licensed"] = True
        data["token"] = token
        _save_license(data)
        return True, "Licensed."
    return False, result.get("error", "Invalid or already-used token.")
