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


# ------------------------------------------------- Telegram guruh nazorati
def group_seen(chat_id, telegram_id, *, username="", full_name="", status="member", source="?"):
    """Guruhda ko'rilgan odamni yozib qo'yadi."""
    return _request("POST", "/api/bot/group/seen", payload={
        "chat_id": str(chat_id), "telegram_id": str(telegram_id),
        "username": username, "full_name": full_name,
        "status": status, "source": source,
    })


def group_audit(chat_id):
    """Bot ko'rgan a'zolar: ro'yxatda borlar va yo'qlar."""
    return _request("GET", "/api/bot/group/audit", params={"chat_id": str(chat_id)})


def get_setting(key):
    return _request("GET", f"/api/bot/setting/{key}").get("value")


def set_setting(key, value):
    return _request("POST", f"/api/bot/setting/{key}", payload={"value": value})


# ------------------------------------------------------ hujjatlar (voucher, chipta)
def docs_for(*, telegram_id=None, participant_id=None):
    """Bir odamning tarqatishga tayyor hujjatlari va vaqti kelmaganlari."""
    params = {}
    if telegram_id:
        params["telegram_id"] = str(telegram_id)
    if participant_id:
        params["id"] = participant_id
    return _request("GET", "/api/bot/docs", params=params)


def docs_pending():
    """Hujjati bor, lekin hali yuborilmagan odamlar."""
    return _request("GET", "/api/bot/docs/pending")


def docs_file(doc_id):
    """Fayl baytlari — bot uni Telegramga yuboradi."""
    url = f"{config.API_BASE.rstrip('/')}/api/bot/docs/file/{int(doc_id)}"
    try:
        response = requests.get(url, headers={"X-Bot-Token": config.BOT_API_TOKEN}, timeout=60)
    except requests.RequestException as exc:
        raise ApiError(f"Faylni olib bo'lmadi: {exc}") from exc
    if not response.ok:
        raise ApiError(f"HTTP {response.status_code}", response.status_code)
    return response.content


def docs_mark_sent(ids):
    return _request("POST", "/api/bot/docs/sent", payload={"ids": list(ids)})


def docs_upload(files, *, pids=None, hint="", kind="", uploader=""):
    """Fayllarni yuklaydi. `files` — [(nom, baytlar, mime), ...].

    `pids` berilmasa egalari fayl nomidan va `hint` matnidan topiladi.
    """
    if not config.BOT_API_TOKEN:
        raise ApiError("BOT_API_TOKEN is not configured")
    url = f"{config.API_BASE.rstrip('/')}/api/bot/docs/upload"
    data = {"hint": hint, "kind": kind, "uploader": str(uploader or "")}
    if pids:
        data["pids"] = ",".join(pids)
    payload = [("files", (name, blob, mime or "application/octet-stream"))
               for name, blob, mime in files]
    try:
        response = requests.post(url, data=data, files=payload,
                                 headers={"X-Bot-Token": config.BOT_API_TOKEN}, timeout=120)
    except requests.RequestException as exc:
        raise ApiError(f"Faylni yuklab bo'lmadi: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok:
        raise ApiError(body.get("error") or f"HTTP {response.status_code}",
                       response.status_code, body)
    return body


def docs_share(doc_id, pids):
    return _request("POST", "/api/bot/docs/share",
                    payload={"id": doc_id, "pids": list(pids)})


def docs_delete(doc_id):
    return _request("POST", "/api/bot/docs/delete", payload={"id": doc_id})


def docs_state():
    """Hujjatlar bo'yicha umumiy holat."""
    return _request("GET", "/api/bot/docs/state")


def docs_hold(on):
    """Tarqatishni ushlab turish yoki ochish."""
    return _request("POST", "/api/bot/docs/hold", payload={"hold": bool(on)})
