import io

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import config

SCOPES = ["https://www.googleapis.com/auth/drive"]

_service = None


def _creds():
    creds = Credentials.from_authorized_user_file(config.DRIVE_TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(config.DRIVE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def _svc():
    global _service
    if _service is None:
        _service = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    return _service


def upload_bytes(data: bytes, filename: str, mime: str) -> str:
    """Faylni Drive papkasiga yuklaydi va ko'rish havolasini qaytaradi."""
    svc = _svc()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    meta = {"name": filename, "parents": [config.DRIVE_FOLDER_ID]}
    f = (
        svc.files()
        .create(body=meta, media_body=media, fields="id,webViewLink", supportsAllDrives=True)
        .execute()
    )
    return f.get("webViewLink", "")
