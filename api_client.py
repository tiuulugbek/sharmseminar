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


def _request(method, path, *, payload=None, timeout=15):
    if not config.BOT_API_TOKEN:
        raise ApiError("BOT_API_TOKEN is not configured")
    url = f"{config.API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.request(
            method, url, json=payload,
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
