#!/usr/bin/env python3
"""Generate simulator-based regression tests from oclean_ble.log.

Parses the integration's debug log and emits a pytest file with one test
per unique poll pattern.  Each generated test replays the exact notification
bytes seen on the real device.

Expected field values are computed by running the CURRENT parser against
the captured raw bytes – not taken from the log's "parsed" output.  This
ensures generated tests always reflect the current parser logic, even when
the log was captured with an older version.

Usage::

    python tools/generate_tests_from_log.py [logfile] [--output FILE]

    # defaults: reads oclean_ble.log, writes tests/test_generated_real_data.py
    python tools/generate_tests_from_log.py

    # custom paths
    python tools/generate_tests_from_log.py /ha/config/oclean_ble.log \\
        --output /tmp/out.py

Requirements:
    - logging level DEBUG for custom_components.oclean_ble
    - "Oclean notification raw:" lines must be present (always emitted at DEBUG)
    - "Oclean poll start:" lines (added in coordinator.py) mark boundaries in
      new logs; old logs are supported via timestamp-based fallback grouping
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make parse_notification() available at generation time so we
# compute expected values from current parser, not from stale log output.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# Install lightweight HA stubs (same ones used by the test suite)
try:
    from tests.conftest import _install_ha_stubs  # type: ignore[import-untyped]
    _install_ha_stubs()
except Exception:
    pass  # already installed or not needed

from custom_components.oclean_ble.parser import parse_notification  # noqa: E402


# ---------------------------------------------------------------------------
# Log line regexes
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"   # timestamp
    r"\s+\w+"                                       # log level
    r"\s+\[([^\]]+)\]"                              # logger name
    r"\s+(.+)$"                                     # message
)
_RAW_RE       = re.compile(r"Oclean notification raw: ([0-9a-fA-F]+)$")
_PARSED_RE    = re.compile(r"Oclean notification parsed: (\{.*\})$")
_BATTERY_RE   = re.compile(r"Oclean battery raw: ([0-9a-fA-F]+)$")
_POLL_START_RE = re.compile(r"Oclean poll start: mac=([^\s]+) ts=(\d+)")
_COLLECTED_RE  = re.compile(r"Oclean poll collected so far: (\{.*\})$")
_FETCHED_RE    = re.compile(r"Oclean fetched (\d+) session\(s\)")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _Notification:
    raw_hex: str   # full notification bytes including 2-byte prefix
    parsed: dict   # result of parse_notification() run NOW (current parser)


@dataclass
class _Poll:
    timestamp: str       # "YYYY-MM-DD HH:MM:SS"
    mac: str             # device MAC or "unknown"
    battery: int         # 0-100, -1 = not read
    notifications: list[_Notification] = field(default_factory=list)
    session_count: int = 0


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------

def _parse_log(path: Path) -> list[_Poll]:
    """Extract all poll cycles from *path*."""
    polls: list[_Poll] = []
    current: _Poll | None = None
    pending_raw: str | None = None
    mac_from_start: str = "unknown"

    def _flush() -> None:
        nonlocal current
        if current and current.notifications:
            polls.append(current)
        current = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        ts, msg = m.group(1), m.group(3).strip()

        # "Oclean poll start:" – explicit boundary (new logs)
        pm = _POLL_START_RE.search(msg)
        if pm:
            _flush()
            mac_from_start = pm.group(1)
            current = _Poll(timestamp=ts, mac=mac_from_start, battery=-1)
            pending_raw = None
            continue

        # "Oclean notification raw:" – start of a notification pair
        rm = _RAW_RE.search(msg)
        if rm:
            hex_val = rm.group(1)
            if len(hex_val) < 4:   # < 2 bytes → useless fragment
                pending_raw = None
                continue
            if current is None:
                # Old log: infer poll start from first notification seen
                _flush()
                current = _Poll(timestamp=ts, mac=mac_from_start, battery=-1)
            # If a raw was pending but never matched a "parsed" line,
            # resolve it now with the current parser before overwriting.
            if pending_raw is not None:
                _resolve_pending(current, pending_raw)
            pending_raw = hex_val
            continue

        # "Oclean notification parsed:" – completes the pending pair
        # Note: parser-level log lines may appear between "raw" and "parsed"
        # (e.g. "Oclean STATE parsed:", "Oclean 0307 parsed:").
        # We do NOT reset pending_raw on unrelated lines.
        parsed_m = _PARSED_RE.search(msg)
        if parsed_m and pending_raw is not None:
            _resolve_pending(current, pending_raw)
            pending_raw = None
            continue

        # "Oclean battery raw: XX" (XX = 1 hex byte)
        bm = _BATTERY_RE.search(msg)
        if bm and current is not None:
            try:
                current.battery = int(bm.group(1), 16)
            except ValueError:
                pass
            continue

        # "Oclean fetched N session(s)" – end of poll
        fm = _FETCHED_RE.search(msg)
        if fm and current is not None:
            current.session_count = int(fm.group(1))
            if pending_raw is not None:
                _resolve_pending(current, pending_raw)
                pending_raw = None
            _flush()
            continue

    if pending_raw is not None and current is not None:
        _resolve_pending(current, pending_raw)
    _flush()
    return polls


def _resolve_pending(poll: _Poll | None, raw_hex: str) -> None:
    """Parse *raw_hex* with the CURRENT parser and append to *poll*."""
    if poll is None:
        return
    try:
        parsed = parse_notification(bytes.fromhex(raw_hex))
    except Exception:
        parsed = {}
    poll.notifications.append(_Notification(raw_hex, parsed))


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _poll_signature(poll: _Poll) -> str:
    key = "|".join(n.raw_hex for n in poll.notifications)
    return hashlib.md5(key.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

_SENSOR_KEYS = (
    "battery",
    "last_brush_time",
    "last_brush_score",
    "last_brush_duration",
    "last_brush_pressure",
    "last_brush_pnum",
    "last_brush_areas",
)

_FILE_HEADER = '''\
"""Regression tests generated from real device logs.

Generated by:  python tools/generate_tests_from_log.py
DO NOT EDIT – re-run the generator to refresh.

Each test replays the exact BLE notification bytes captured from a physical
device and asserts the field values produced by the CURRENT parser.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.oclean_ble.coordinator import OcleanCoordinator
from tests.simulator import OcleanDeviceSimulator


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_simulator.py)
# ---------------------------------------------------------------------------

def _make_hass():
    hass = MagicMock()
    hass.data = {}
    return hass


def _make_coordinator(mac: str = "AA:BB:CC:DD:EE:FF") -> OcleanCoordinator:
    coord = OcleanCoordinator(_make_hass(), mac, "Oclean", 300)
    coord._store_loaded = True
    return coord


def _make_service_info(mac: str = "AA:BB:CC:DD:EE:FF"):
    device = MagicMock()
    device.address = mac
    si = MagicMock()
    si.device = device
    return si


async def _run_poll(coordinator: OcleanCoordinator, client: AsyncMock) -> dict:
    with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \\
         patch(
             "custom_components.oclean_ble.coordinator.establish_connection",
             new_callable=AsyncMock,
             return_value=client,
         ), \\
         patch(
             "custom_components.oclean_ble.coordinator.asyncio.sleep",
             new_callable=AsyncMock,
         ), \\
         patch.object(coordinator, "_paginate_sessions", new_callable=AsyncMock), \\
         patch.object(coordinator, "_import_new_sessions", new_callable=AsyncMock):
        bt_mock.async_last_service_info.return_value = _make_service_info(coordinator._mac)
        return await coordinator._poll_device()

'''


def _gen_test_name(poll: _Poll, index: int) -> str:
    ts = poll.timestamp.replace("-", "").replace(" ", "_").replace(":", "")[:13]
    batt = f"batt{poll.battery}" if poll.battery >= 0 else "nobatt"
    return f"test_{ts}_{batt}_{poll.session_count}sess_{index:03d}"


def _gen_test(poll: _Poll, index: int, mac: str) -> str:
    name = _gen_test_name(poll, index)

    # Merge parse results from all notifications (current parser)
    expected: dict = {}
    for n in poll.notifications:
        # "newer timestamp wins" (mirrors coordinator logic)
        incoming_ts = n.parsed.get("last_brush_time")
        if incoming_ts is not None:
            current_ts = expected.get("last_brush_time", 0)
            if incoming_ts < current_ts:
                # drop time-dependent fields but keep enrichment fields
                filtered = {k: v for k, v in n.parsed.items()
                            if k not in ("last_brush_time", "last_brush_duration")}
                expected.update(filtered)
                continue
        expected.update(n.parsed)
    if poll.battery >= 0:
        expected["battery"] = poll.battery

    # Simulator chain
    sim_lines = ["client = (", "    OcleanDeviceSimulator()"]
    if poll.battery >= 0:
        sim_lines.append(f"    .with_battery({poll.battery})")
    for n in poll.notifications:
        sim_lines.append(f"    .add_notification(bytes.fromhex({n.raw_hex!r}))")
    sim_lines.append("    .build_client()")
    sim_lines.append(")")

    # Assertions derived from current-parser output
    asserts: list[str] = []
    for key in _SENSOR_KEYS:
        val = expected.get(key)
        if val is None:
            continue
        if key == "last_brush_time":
            asserts.append('assert result.get("last_brush_time") is not None')
        elif key == "last_brush_areas":
            asserts.append('assert isinstance(result.get("last_brush_areas"), dict)')
        elif isinstance(val, float):
            asserts.append(f'assert abs((result.get("{key}") or 0) - {val}) < 1')
        else:
            asserts.append(f'assert result.get("{key}") == {val!r}')

    mac_arg = f'"{mac}"' if mac != "unknown" else '"AA:BB:CC:DD:EE:FF"'
    indent = "        "

    body = [
        f"    @pytest.mark.asyncio",
        f"    async def {name}(self):",
        f'        """Poll {poll.timestamp} – '
        f'battery={poll.battery}, {poll.session_count} session(s) '
        f'[{len(poll.notifications)} notification(s)]"""',
    ]
    for sl in sim_lines:
        body.append(indent + sl)
    body.append(f"        result = await _run_poll(_make_coordinator({mac_arg}), client)")
    if asserts:
        for a in asserts:
            body.append(indent + a)
    else:
        body.append(indent + "assert result is not None")

    return "\n".join(body)


def generate(polls: list[_Poll]) -> str:
    by_mac: dict[str, list[_Poll]] = {}
    for p in polls:
        by_mac.setdefault(p.mac, []).append(p)

    sections: list[str] = [_FILE_HEADER]

    for mac, mac_polls in by_mac.items():
        # Deduplicate
        seen: set[str] = set()
        unique: list[_Poll] = []
        for p in mac_polls:
            sig = _poll_signature(p)
            if sig not in seen:
                seen.add(sig)
                unique.append(p)

        # Only keep polls where at least one notification produced non-battery data
        interesting = [
            p for p in unique
            if any(
                set(n.parsed) - {"battery"}
                for n in p.notifications
            )
        ]
        if not interesting:
            continue

        # Detect model from collected info in any poll (best-effort)
        model = next(
            (p.notifications[0].raw_hex[:4] for p in interesting),
            ""
        )

        class_name = "TestGenerated_" + mac.replace(":", "_")
        sections.append(f"class {class_name}:")
        sections.append(
            f'    """Auto-generated regression tests for {mac}.\n\n'
            f"    {len(interesting)} unique poll pattern(s) captured from real device.\n"
            f'    """\n'
        )

        for idx, poll in enumerate(interesting):
            sections.append(_gen_test(poll, idx, mac))
            sections.append("")

        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "logfile", nargs="?", default="oclean_ble.log",
        help="Path to oclean_ble.log (default: oclean_ble.log)",
    )
    p.add_argument(
        "--output", "-o", default="tests/test_generated_real_data.py",
        help="Output file (default: tests/test_generated_real_data.py)",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Print to stdout without writing")
    p.add_argument("--stats", action="store_true",
                   help="Print statistics only")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    log_path = Path(args.logfile)
    if not log_path.exists():
        print(f"ERROR: log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {log_path} …", file=sys.stderr)
    polls = _parse_log(log_path)
    print(f"  Found {len(polls)} poll cycles total", file=sys.stderr)

    by_mac: dict[str, list[_Poll]] = {}
    for p in polls:
        by_mac.setdefault(p.mac, []).append(p)

    for mac, mp in by_mac.items():
        seen: set[str] = set()
        unique = sum(
            1 for p in mp
            if _poll_signature(p) not in seen and not seen.add(_poll_signature(p))  # type: ignore
        )
        with_sess = sum(1 for p in mp if p.session_count > 0)
        print(
            f"  {mac}: {len(mp)} polls, {unique} unique patterns, "
            f"{with_sess} with session data",
            file=sys.stderr,
        )

    if args.stats:
        return

    code = generate(polls)

    if args.dry_run:
        print(code)
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code, encoding="utf-8")
    test_count = code.count("    async def test_")
    print(f"  → wrote {test_count} test(s) to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
