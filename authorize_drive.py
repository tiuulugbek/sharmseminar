"""Bir martalik Google Drive OAuth ruxsati (acousticzakaz@gmail.com bilan).

Foydalanish:
  1) URL olish:   ./venv/bin/python authorize_drive.py url
  2) Kodni almashtirish (brauzerdan ko'chirilgan to'liq localhost havola):
     ./venv/bin/python authorize_drive.py code "<havola>"
"""
import os
import sys

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from google_auth_oauthlib.flow import Flow

import config

SCOPES = ["https://www.googleapis.com/auth/drive"]
REDIRECT = "http://localhost"
VERIFIER_FILE = os.path.join(os.path.dirname(__file__), ".verifier")


def make_flow():
    flow = Flow.from_client_secrets_file(config.DRIVE_CLIENT_FILE, scopes=SCOPES)
    flow.redirect_uri = REDIRECT
    return flow


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("url", "code"):
        print(__doc__)
        return

    if sys.argv[1] == "url":
        flow = make_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        with open(VERIFIER_FILE, "w") as f:
            f.write(flow.code_verifier or "")
        print("\nQuyidagi havolani brauzerda oching (acousticzakaz@gmail.com bilan kiring va ruxsat bering):\n")
        print(auth_url)
        print("\nRuxsat bergach 'localhost' sahifasiga o'tadi (ochilmaydi — normal).")
        print("Manzil satridagi TO'LIQ havolani menga yuboring.\n")
        return

    # code step
    flow = make_flow()
    with open(VERIFIER_FILE) as f:
        flow.code_verifier = f.read().strip() or None
    flow.fetch_token(authorization_response=sys.argv[2].strip())
    with open(config.DRIVE_TOKEN_FILE, "w") as f:
        f.write(flow.credentials.to_json())
    print("✅ token.json saqlandi:", config.DRIVE_TOKEN_FILE)


if __name__ == "__main__":
    main()
