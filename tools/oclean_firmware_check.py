"""
Oclean Firmware Update Checker

Checks whether a firmware update is available for one or more Oclean devices
by authenticating against the Oclean cloud API and calling the
SafetyGetNewHardware endpoint.

The current firmware version is read from the BLE DIS SoftwareRevision
characteristic by the HA integration and is shown in the device info panel
(e.g. "1.0.0.41" for OCLEANY3P).

Usage:
    python3 oclean_firmware_check.py --email user@example.com --password secret \\
        --devices AA:BB:CC:DD:EE:FF=1.0.0.41 70:28:45:75:9F:D5=1.0.0.41

    python3 oclean_firmware_check.py --email user@example.com --password secret \\
        --devices AA:BB:CC:DD:EE:FF=1.0.0.41 \\
        --region eu

Region / base URL mapping (matches RootUrlUtil.kt from APK):
    eu   (default)  https://hwapicore.oclean.com/
    us              https://bmapicore.oclean.com/
    tw              https://care.oclean.com.tw/

Exit codes:
    0  all devices up-to-date (or already on unknown version)
    1  at least one update available
    2  authentication or network error
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Region → base URL (from APK RootUrlUtil.kt)
# ---------------------------------------------------------------------------

_BASE_URLS: dict[str, str] = {
    "eu": "https://hwapicore.oclean.com/",
    "us": "https://bmapicore.oclean.com/",
    "tw": "https://care.oclean.com.tw/",
}

_LOGIN_PATH = "Romap/v1/User/SignInByPassword"
_FIRMWARE_PATH = "Romap/v1/UserDeviceContoller/SafetyGetNewHardware"

_APP_HEADERS = {
    "Content-Type": "application/json",
    "AppVersion": "4.0.3",
    "AppAgent": "hw",
    "AppLanguage": "-1",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FirmwareVersion:
    hard_ver: str
    up_info: str
    path: str


@dataclass
class FirmwareResult:
    mac: str
    current_ver: str
    new_hardware: FirmwareVersion | None
    current_hardware: FirmwareVersion | None


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no extra dependencies)
# ---------------------------------------------------------------------------


def _post(url: str, body: dict, headers: dict | None = None) -> dict:
    payload = json.dumps(body).encode()
    req_headers = {**_APP_HEADERS, **(headers or {})}
    request = urllib.request.Request(url, data=payload, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}: {exc.read().decode()[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error contacting {url}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def login(base_url: str, email: str, password: str) -> str:
    """Return a Bearer accessToken or raise RuntimeError."""
    url = base_url + _LOGIN_PATH
    resp = _post(url, {"account": email, "password": password})
    code = resp.get("code")
    if code != 200:
        msg = resp.get("msg", "unknown error")
        raise RuntimeError(f"Login failed (code={code}): {msg}")
    token = resp.get("data", {}).get("tokenBean", {}).get("accessToken")
    if not token:
        raise RuntimeError(f"Login response missing accessToken: {resp}")
    return token


# ---------------------------------------------------------------------------
# Firmware check
# ---------------------------------------------------------------------------


def check_firmware(base_url: str, token: str, mac: str, current_ver: str) -> FirmwareResult:
    """Query SafetyGetNewHardware for one device."""
    url = base_url + _FIRMWARE_PATH
    headers = {"Authorization": f"Bearer {token}"}
    resp = _post(url, {"deviceMac": mac, "hardVer": current_ver}, headers=headers)

    code = resp.get("code")
    if code != 200:
        msg = resp.get("msg", "unknown error")
        raise RuntimeError(f"Firmware check failed for {mac} (code={code}): {msg}")

    data = resp.get("data", {})

    def _parse(obj: dict | None) -> FirmwareVersion | None:
        if not obj or not obj.get("hardVer"):
            return None
        return FirmwareVersion(
            hard_ver=obj.get("hardVer", ""),
            up_info=obj.get("upInfo", ""),
            path=obj.get("path", ""),
        )

    return FirmwareResult(
        mac=mac,
        current_ver=current_ver,
        new_hardware=_parse(data.get("newHardWare")),
        current_hardware=_parse(data.get("currentHardWare")),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_device(value: str) -> tuple[str, str]:
    """Parse 'AA:BB:CC:DD:EE:FF=1.0.0.41' into (mac, version)."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid device spec '{value}'. Expected format: MAC=version  (e.g. AA:BB:CC:DD:EE:FF=1.0.0.41)"
        )
    mac, ver = value.split("=", 1)
    return mac.upper().strip(), ver.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Oclean firmware updates via the cloud API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--email", required=True, help="Oclean account e-mail")
    parser.add_argument("--password", required=True, help="Oclean account password")
    parser.add_argument(
        "--devices",
        required=True,
        nargs="+",
        metavar="MAC=VERSION",
        type=_parse_device,
        help="One or more devices in MAC=version format",
    )
    parser.add_argument(
        "--region",
        choices=list(_BASE_URLS),
        default="eu",
        help="API region (default: eu)",
    )
    args = parser.parse_args()

    base_url = _BASE_URLS[args.region]
    print(f"Region: {args.region}  ({base_url})")

    # --- Login ---
    print(f"Logging in as {args.email} ...", end=" ", flush=True)
    try:
        token = login(base_url, args.email, args.password)
    except RuntimeError as exc:
        print(f"FAILED\n{exc}", file=sys.stderr)
        return 2
    print("OK")

    # --- Check each device ---
    any_update = False
    for mac, current_ver in args.devices:
        print(f"\n{'─' * 50}")
        print(f"Device : {mac}")
        print(f"Current: {current_ver}")
        try:
            result = check_firmware(base_url, token, mac, current_ver)
        except RuntimeError as exc:
            print(f"ERROR  : {exc}", file=sys.stderr)
            continue

        if result.current_hardware:
            print(f"Server : {result.current_hardware.hard_ver}")

        if result.new_hardware and result.new_hardware.hard_ver:
            print(f"Update : {result.new_hardware.hard_ver}  ← UPDATE AVAILABLE")
            if result.new_hardware.up_info:
                print(f"Notes  : {result.new_hardware.up_info}")
            if result.new_hardware.path:
                print(f"URL    : {result.new_hardware.path}")
            any_update = True
        else:
            print("Status : up-to-date")

    print(f"\n{'─' * 50}")
    return 1 if any_update else 0


if __name__ == "__main__":
    sys.exit(main())
