"""HTTP client for the sharm-seminar bot API.

Telegram handlers use this module instead of reading or writing SQLite/Sheets
directly.  Functions are synchronous by design and are called through
``asyncio.to_thread`` by aiogram handlers.
"""
import logging

import requests

import config

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


def _request(method, path, *, payload=None, params=None, timeout=15):
    if not config.BOT_API_TOKEN:
        raise ApiError("BOT_API_TOKEN is not configured")
    url = f"{config.API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.request(
            method, url, json=payload, params=params,
            headers={"X-Bot-Token": config.BOT_API_TOKEN}, timeout=timeout,
        )
    except requests.RequestException as exc:
        log.exception("Seminar API request failed: %s %s", method, url)
        raise ApiError(f"Seminar API unavailable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok:
        raise ApiError(body.get("error") or f"HTTP {response.status_code}",
                       response.status_code, body)
    return body


def find(*, series=None, dob=None, name=None):
    data = {k: v for k, v in {"series": series, "dob": dob, "name": name}.items() if v}
    return _request("POST", "/api/bot/find", payload=data).get("participants", [])


def link(participant_id, telegram_id, telegram_username=""):
    return _request("POST", "/api/bot/link", payload={
        "id": participant_id, "telegram_id": str(telegram_id),
        "telegram_username": telegram_username or "",
    })["participant"]


def register(participant):
    return _request("POST", "/api/bot/register", payload=participant)["participant"]


def update(patch, *, participant_id=None, series=None):
    return _request("POST", "/api/bot/update", payload={
        "id": participant_id, "series": series, "patch": patch,
    })["participant"]


def roommate(series, roommate_series):
    return _request("POST", "/api/bot/roommate", payload={
        "series": series, "roommate_series": roommate_series,
    })


def recipients():
    return _request("GET", "/api/bot/recipients").get("recipients", [])


def stats():
    return _request("GET", "/api/bot/stats")


def participant(participant_id):
    return _request("GET", f"/api/p/{participant_id}")


def checkin(participant_id, checkpoint, on=True):
    return _request("POST", "/api/checkin", payload={
        "id": participant_id, "checkpoint": checkpoint, "on": bool(on),
    })


# ------------------------------------------------------------ roles & check-in
def whoami(telegram_id):
    """{role: admin|leader|member|guest, id, fio, group, group_name, token}."""
    return _request("GET", "/api/bot/whoami", params={"telegram_id": str(telegram_id)})


def groups():
    return _request("GET", "/api/bot/groups").get("groups", [])


def group_members(group_id):
    return _request("GET", f"/api/bot/group/{int(group_id)}")


def scan_checkin(token, checkpoint, by_telegram_id, on=True):
    """Check somebody in from a QR token; the server enforces the caller's role."""
    return _request("POST", "/api/bot/checkin", payload={
        "token": token, "checkpoint": checkpoint,
        "by_telegram_id": str(by_telegram_id), "on": bool(on),
    })


# ---------------------------------------------------------------- messaging
def message(from_telegram_id, scope, value, text, kind="text"):
    """Register an outgoing message; returns recipients the bot must send to."""
    return _request("POST", "/api/bot/message", payload={
        "from_telegram_id": str(from_telegram_id), "scope": scope,
        "value": "" if value is None else str(value), "text": text, "kind": kind,
    })


def message_sent(message_id, results):
    """Report per-recipient delivery so replies can be routed back later."""
    return _request("POST", "/api/bot/message/sent", payload={
        "message_id": message_id, "results": results,
    })


def reply(from_telegram_id, parent_msg_id, text, kind="text"):
    """Returns the original sender's telegram_id — a reply never fans out."""
    return _request("POST", "/api/bot/reply", payload={
        "from_telegram_id": str(from_telegram_id),
        "parent_msg_id": parent_msg_id, "text": text, "kind": kind,
    })


def scoped_recipients(telegram_id, scope, value=None):
    params = {"telegram_id": str(telegram_id), "scope": scope}
    if value not in (None, ""):
        params["value"] = str(value)
    return _request("GET", "/api/bot/recipients", params=params).get("recipients", [])
