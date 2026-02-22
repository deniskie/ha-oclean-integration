#!/usr/bin/env python3
"""
Fetch Oclean brush scheme name mappings from the official API.

Step 1 (no auth): GET /GetAllResources → dataModeGears contains scheme
                  names and pNum values for all device models.
Step 2 (optional, with login): GetUserTags + GetBrushData → cross-check
                  which pNum/schemeId values appear in real session data
                  and what typeName the server returns for them.

Output: a Python dict ready to paste into const.py.

Usage:
    python3 tools/oclean_scheme_map.py                    # Step 1 only
    python3 tools/oclean_scheme_map.py --login            # Step 1 + 2
"""

import argparse
import json
import sys

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

BASE_URL = "https://hwapicore.oclean.com"

HEADERS_BASE = {
    "Content-Type": "application/json; charset=utf-8",
    "AppLanguage": "en",
    "User-Agent": "OcleanSchemeMapper/1.0",
}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _post(path: str, payload: dict, token: str | None = None) -> dict:
    headers = dict(HEADERS_BASE)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(f"{BASE_URL}/{path}", headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def login(email: str, password: str) -> str:
    """Return JWT access token."""
    data = _post(
        "Romap/v1/User/SignInByPassword",
        {"client": 3, "cellPhone": email, "password": password, "phoneCode": ""},
    )
    if not data.get("state"):
        raise RuntimeError(f"Login failed: {data.get('msg')} (code {data.get('code')})")
    return data["data"]["jwt"]["accessToken"]


def get_all_resources() -> dict:
    """Fetch public app configuration including scheme/mode definitions."""
    return _post(
        "Romap/v1/DeviceContoller/GetAllResources",
        {
            "clientId": 3,
            "version": "0|0|0|0|0|0|0|0|",
            "currentAppVer": "4.0.3",
            "platform": "android",
            "brushSchemeAgent": "oclean",
            "language": "-1",
        },
    )


def get_user_tags(token: str) -> list[dict]:
    """Return list of device tag objects (each has groupId, device.mac, etc.)."""
    data = _post("Romap/v1/UserDeviceContoller/GetUserTags", {}, token=token)
    if not data.get("state"):
        raise RuntimeError(f"GetUserTags failed: {data.get('msg')}")
    return data.get("data") or []


def get_brush_data(token: str, group_id: str) -> list[dict]:
    """Fetch recent brush sessions (up to last 30 days) for a device group."""
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=90)
    data = _post(
        "Romap/v1/UserDeviceContoller/GetBrushData",
        {
            "groupId": group_id,
            "beginTime": start.strftime("%Y-%m-%d"),
            "endTime": end.strftime("%Y-%m-%d"),
        },
        token=token,
    )
    if not data.get("state"):
        raise RuntimeError(f"GetBrushData failed: {data.get('msg')}")
    return (data.get("data") or {}).get("dateArr") or []


def get_wash_data(token: str, group_id: str) -> list[dict]:
    """Fetch recent wash records (includes schemeId / schemeType)."""
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=90)
    data = _post(
        "Romap/v1/UserDeviceContoller/GetWashData",
        {
            "groupId": group_id,
            "beginTime": start.strftime("%Y-%m-%d"),
            "endTime": end.strftime("%Y-%m-%d"),
        },
        token=token,
    )
    if not data.get("state"):
        raise RuntimeError(f"GetWashData failed: {data.get('msg')}")
    return (data.get("data") or {}).get("dataList") or []


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_schemes_from_resources(resources_data: dict) -> dict[int, str]:
    """
    Walk dataModeGears looking for pNum → schemeName mappings.
    dataModeGears is typically a list of device model entries, each containing
    a list of available modes/schemes with their pNum and name.
    """
    mapping: dict[int, str] = {}
    raw = resources_data.get("data") or {}

    # Try common key names for scheme data
    for key in ("dataModeGears", "dataBrushScheme", "dataScheme", "dataModes"):
        section = raw.get(key)
        if not section:
            continue
        print(f"\n[GetAllResources] Found key '{key}' – scanning for pNum mappings…")
        _walk_for_pnum(section, mapping)

    return mapping


def _walk_for_pnum(obj, mapping: dict[int, str]) -> None:
    """Recursively walk a JSON structure looking for pNum + name pairs."""
    if isinstance(obj, list):
        for item in obj:
            _walk_for_pnum(item, mapping)
    elif isinstance(obj, dict):
        pnum = obj.get("pNum") or obj.get("pnum") or obj.get("schemeId")
        name = (
            obj.get("schemeName")
            or obj.get("name")
            or obj.get("modeName")
            or obj.get("typeName")
            or obj.get("title")
        )
        if pnum is not None and name:
            try:
                mapping[int(pnum)] = str(name)
            except (ValueError, TypeError):
                pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk_for_pnum(v, mapping)


def extract_schemes_from_sessions(sessions: list[dict]) -> dict[int, str]:
    """Extract schemeId → typeName from BrushData session records."""
    mapping: dict[int, str] = {}
    for s in sessions:
        sid = s.get("schemeId") or s.get("pNum")
        name = s.get("typeName") or s.get("schemeName")
        if sid is not None and name:
            try:
                mapping[int(sid)] = str(name)
            except (ValueError, TypeError):
                pass
    return mapping


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Oclean scheme name mappings")
    parser.add_argument("--login", action="store_true", help="Also authenticate and fetch session data")
    parser.add_argument("--dump-raw", action="store_true", help="Dump raw API responses to JSON files for manual inspection")
    args = parser.parse_args()

    combined: dict[int, str] = {}

    # --- Step 1: public resource endpoint (no auth) ---
    print("Fetching GetAllResources (no auth required)…")
    try:
        res = get_all_resources()
        if args.dump_raw:
            with open("resources_raw.json", "w") as f:
                json.dump(res, f, indent=2, ensure_ascii=False)
            print("  → Raw response saved to resources_raw.json")

        from_resources = extract_schemes_from_resources(res)
        if from_resources:
            print(f"  → Found {len(from_resources)} scheme(s) in GetAllResources")
            combined.update(from_resources)
        else:
            print("  → No pNum mappings found in GetAllResources (see resources_raw.json with --dump-raw)")
    except Exception as e:
        print(f"  ✗ GetAllResources failed: {e}")

    # --- Step 2: authenticated session data ---
    if args.login:
        print("\nLogin required for session data.")
        email = input("Email: ").strip()
        password = input("Password: ").strip()
        try:
            token = login(email, password)
            print("  ✓ Login successful")

            tags = get_user_tags(token)
            print(f"  ✓ Found {len(tags)} device group(s)")

            for tag in tags:
                group_id = tag.get("groupId")
                device_name = (tag.get("device") or {}).get("deviceName") or group_id
                if not group_id:
                    continue
                print(f"\n  Device: {device_name} (groupId={group_id})")

                try:
                    sessions = get_brush_data(token, group_id)
                    print(f"    GetBrushData: {len(sessions)} session(s)")
                    from_sessions = extract_schemes_from_sessions(sessions)
                    if from_sessions:
                        print(f"    → Found scheme mappings: {from_sessions}")
                        combined.update(from_sessions)
                    else:
                        print("    → No typeName in session records")

                    if args.dump_raw:
                        fname = f"brush_data_{group_id}.json"
                        with open(fname, "w") as f:
                            json.dump(sessions, f, indent=2, ensure_ascii=False)
                        print(f"    → Raw sessions saved to {fname}")
                except Exception as e:
                    print(f"    ✗ GetBrushData failed: {e}")

                try:
                    wash = get_wash_data(token, group_id)
                    print(f"    GetWashData: {len(wash)} record(s)")
                    from_wash = extract_schemes_from_sessions(wash)
                    if from_wash:
                        print(f"    → Found scheme mappings: {from_wash}")
                        combined.update(from_wash)
                    if args.dump_raw:
                        fname = f"wash_data_{group_id}.json"
                        with open(fname, "w") as f:
                            json.dump(wash, f, indent=2, ensure_ascii=False)
                        print(f"    → Raw wash data saved to {fname}")
                except Exception as e:
                    print(f"    ✗ GetWashData failed: {e}")

        except Exception as e:
            print(f"  ✗ Auth step failed: {e}")

    # --- Output ---
    print("\n" + "=" * 60)
    if combined:
        print(f"Found {len(combined)} pNum → scheme name mapping(s):\n")
        print("# Paste into custom_components/oclean_ble/const.py:")
        print("SCHEME_NAMES: dict[int, str] = {")
        for pnum in sorted(combined):
            print(f"    {pnum}: {combined[pnum]!r},")
        print("}")
    else:
        print("No scheme name mappings found.")
        print("Try running with --login --dump-raw and inspect the raw JSON files.")
    print("=" * 60)


if __name__ == "__main__":
    main()
