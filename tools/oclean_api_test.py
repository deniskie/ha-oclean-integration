#!/usr/bin/env python3
"""Oclean Cloud API tool – fetch session data for reverse-engineering.

Logs in once, then automatically retrieves the last brushing sessions
with all available fields. Useful for comparing cloud data with BLE raw
values to understand how scores, coverage, and pressure maps are computed.

Usage:
    python tools/oclean_api_test.py                 # interactive login + fetch
    python tools/oclean_api_test.py --last 5        # show last 5 sessions
    python tools/oclean_api_test.py --all            # dump all fields as JSON
    python tools/oclean_api_test.py --relogin        # force new login
"""

import argparse
import getpass
import json
import os
import sys

import requests

BASE_URL = "https://hwapicore.oclean.com"
HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "AppLanguage": "-1",
    "AppVersion": "4.0.3",
    "AppAgent": "hw",
}

TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".oclean_token.json")

# Fields to display in the summary table
SUMMARY_FIELDS = [
    ("date", "Date"),
    ("score", "Score"),
    ("timeLong", "Duration (s)"),
    ("pressure", "Pressure"),
    ("pressureRatio", "PressureRatio"),
    ("pressureDistribution", "PressureDistribution"),
    ("gesture", "Gesture"),
    ("gestureArray", "GestureArray"),
    ("powerArray", "PowerArray"),
    ("clean", "Clean"),
    ("speckle", "Speckle"),
    ("point", "Point"),
    ("schemeType", "SchemeType"),
    ("schemeId", "SchemeID"),
    ("deviceMac", "MAC"),
    ("typeName", "DeviceType"),
]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _save_token(token_data: dict) -> None:
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def _load_token() -> dict | None:
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _auth_headers(access_token: str) -> dict:
    return {**HEADERS, "Authorization": f"Bearer {access_token}"}


def login(email: str, password: str) -> dict:
    """Login and return {accessToken, refreshToken, email}."""
    url = f"{BASE_URL}/Romap/v1/User/SignInByPassword"
    body = {
        "account": email,
        "passWord": password,
        "cellPhone": email,
        "client": 3,
        "language": "-1",
    }
    resp = requests.post(url, json=body, headers=HEADERS, timeout=15)
    data = resp.json()

    if not data.get("state") or not data.get("data"):
        print(f"Login failed: {data.get('msg', 'Unknown error')}")
        sys.exit(1)

    jwt = data["data"]["jwt"]
    token_data = {
        "accessToken": jwt["accessToken"],
        "refreshToken": jwt["refreshToken"],
        "email": email,
    }
    _save_token(token_data)
    print(f"Logged in as {email}")
    return token_data


def ensure_login(force: bool = False) -> str:
    """Return a valid access token, prompting for credentials if needed."""
    token_data = _load_token()
    if token_data and not force:
        print(f"Using saved session for {token_data.get('email', '?')}")
        return token_data["accessToken"]

    print("=== Oclean Cloud Login ===")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    return login(email, password)["accessToken"]


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def get_group_id(access_token: str) -> str:
    """Discover the user's groupId via GetFamilySpace."""
    url = f"{BASE_URL}/Romap/v1/UserDeviceContoller/GetFamilySpace"
    resp = requests.post(url, json={}, headers=_auth_headers(access_token), timeout=15)
    data = resp.json()

    if not data.get("state") or not data.get("data"):
        print(f"Could not fetch family space: {data.get('msg', 'Unknown error')}")
        return ""

    family = data["data"].get("family", [])
    for member in family:
        for fd in member.get("familyData", []):
            gid = fd.get("groupId", "")
            if gid:
                return gid
    return ""


def get_brush_data(access_token: str, group_id: str, limit: int = 10) -> list:
    """Fetch brush records from the cloud."""
    url = f"{BASE_URL}/Romap/v1/UserDeviceContoller/GetBrushData"
    body = {
        "groupId": group_id,
        "beginTime": "2020-01-01",
        "endTime": "2030-12-31",
    }
    resp = requests.post(url, json=body, headers=_auth_headers(access_token), timeout=15)
    data = resp.json()

    if not data.get("state") or not data.get("data"):
        print(f"Could not fetch brush data: {data.get('msg', 'Unknown error')}")
        return []

    records = data["data"].get("dateArr", [])
    if limit > 0:
        records = records[:limit]
    return records


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_summary(records: list) -> None:
    """Print a readable summary of session records."""
    if not records:
        print("No sessions found.")
        return

    print(f"\n{'='*80}")
    print(f" Found {len(records)} session(s)")
    print(f"{'='*80}\n")

    for i, rec in enumerate(records):
        print(f"--- Session {i+1} ---")
        for key, label in SUMMARY_FIELDS:
            val = rec.get(key)
            if val is not None and val != "" and val != 0:
                print(f"  {label:.<25s} {val}")

        # Derived analysis
        pr = rec.get("pressureRatio", "")
        if pr:
            parts = [int(x) for x in pr.split("#") if x]
            pr_sum = sum(parts)
            active = sum(1 for v in parts if v > 0)
            print(f"  {'[ratio sum]':.<25s} {pr_sum} ({active}/{len(parts)} zones active → {round(active/len(parts)*100)}% coverage)")

        pd = rec.get("pressureDistribution", "")
        if pd:
            parts = [int(x) for x in pd.split("#") if x]
            covered = sum(1 for v in parts if v > 100)
            print(f"  {'[dist covered]':.<25s} {covered}/{len(parts)} zones > 100 → {round(covered/len(parts)*100)}% coverage")

        print()


def print_raw_json(records: list) -> None:
    """Dump all fields as JSON for detailed analysis."""
    print(json.dumps(records, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch Oclean brush session data from the cloud API."
    )
    parser.add_argument("--last", type=int, default=10, help="Number of sessions to show (default: 10, 0=all)")
    parser.add_argument("--all", action="store_true", help="Dump all fields as raw JSON")
    parser.add_argument("--relogin", action="store_true", help="Force new login")
    args = parser.parse_args()

    access_token = ensure_login(force=args.relogin)

    print("Discovering device group...")
    group_id = get_group_id(access_token)
    if not group_id:
        print("No device group found. Is a toothbrush paired in the Oclean app?")
        sys.exit(1)
    print(f"Group ID: {group_id}")

    print("Fetching sessions...")
    records = get_brush_data(access_token, group_id, limit=args.last)

    if args.all:
        print_raw_json(records)
    else:
        print_summary(records)


if __name__ == "__main__":
    main()