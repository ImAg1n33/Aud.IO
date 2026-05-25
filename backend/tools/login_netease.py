"""NetEase Cloud Music QR-code login — unified httpx sync backend."""

import base64
import json
import time
from pathlib import Path

import httpx
from dotenv import set_key


BASE_URL = "http://127.0.0.1:3000"
POLL_INTERVAL_SECONDS = 3
QRCODE_FILE = Path(__file__).resolve().parents[1] / "qrcode.png"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _request_json(path: str, params: dict[str, str] | None = None) -> dict:
    url = f"{BASE_URL}/{path.lstrip('/')}"

    with httpx.Client(timeout=20) as client:
        response = client.get(
            url,
            params=params or {},
            headers={
                "Accept": "application/json",
                "User-Agent": "Aud.IO/0.2 (+https://github.com)",
            },
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected JSON payload from NetEase API.")
    return payload


def _timestamp() -> str:
    return str(int(time.time() * 1000))


def _extract_unikey(payload: dict) -> str:
    data = payload.get("data", {})
    if isinstance(data, dict):
        key = data.get("unikey") or data.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    raise ValueError("Failed to get QR key from /login/qr/key")


def _save_qr_image(payload: dict) -> None:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("Invalid response from /login/qr/create")

    qrimg = data.get("qrimg")
    if not isinstance(qrimg, str) or not qrimg.strip():
        raise ValueError("Missing qrimg field in /login/qr/create response")

    b64 = qrimg.strip()
    prefix = "base64,"
    if prefix in b64:
        b64 = b64.split(prefix, 1)[1]

    image_bytes = base64.b64decode(b64)
    QRCODE_FILE.write_bytes(image_bytes)


def _extract_cookie(payload: dict) -> str:
    direct_cookie = payload.get("cookie")
    if isinstance(direct_cookie, str) and direct_cookie.strip():
        return direct_cookie.strip()

    data = payload.get("data")
    if isinstance(data, dict):
        nested_cookie = data.get("cookie")
        if isinstance(nested_cookie, str) and nested_cookie.strip():
            return nested_cookie.strip()

    raise ValueError("Login success but cookie not found in response")


def login_with_qr() -> str:
    key_payload = _request_json("/login/qr/key", {"timestamp": _timestamp()})
    key = _extract_unikey(key_payload)

    qr_payload = _request_json(
        "/login/qr/create",
        {"key": key, "qrimg": "true", "timestamp": _timestamp()},
    )
    _save_qr_image(qr_payload)

    print(f"QR code saved to: {QRCODE_FILE}")
    print("Open the image and scan with NetEase app.")

    last_code = None
    while True:
        check_payload = _request_json(
            "/login/qr/check",
            {"key": key, "timestamp": _timestamp()},
        )
        code = check_payload.get("code")

        if code != last_code:
            print(f"Current login status code: {code}")
            last_code = code

        if code == 803:
            cookie = _extract_cookie(check_payload)
            if not ENV_FILE.exists():
                ENV_FILE.write_text("", encoding="utf-8")
            set_key(str(ENV_FILE), "NETEASE_COOKIE", cookie)
            if QRCODE_FILE.exists():
                QRCODE_FILE.unlink()
            print("Login authorized. NETEASE_COOKIE has been written to backend/.env")
            return cookie

        if code == 800:
            raise RuntimeError("QR code expired. Please rerun the script.")

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    try:
        login_with_qr()
    except Exception as exc:
        print(f"Login failed: {exc}")
        raise


if __name__ == "__main__":
    main()
