#!/usr/bin/env python3
"""Decode an Android btsnoop_hci.log and list the Oclean frames the integration does not know.

Capture the log on the phone while using the official Oclean app (see
tools/README.md), copy it to the computer and run:

    python tools/oclean_btsnoop.py btsnoop_hci.log
    python tools/oclean_btsnoop.py btsnoop_hci.log --all          # every ATT write/notification
    python tools/oclean_btsnoop.py btsnoop_hci.log --json out.json

Only the Python standard library is needed.  The integration's command table
and parser are loaded straight from custom_components/ (Home Assistant is not
imported), so the "known" verdicts always match the code in this checkout.

What it does:
  * reads the btsnoop container (H4 / datalink 1002, or unencapsulated 1001),
  * reassembles fragmented ACL packets and extracts ATT PDUs (L2CAP CID 4),
  * maps attribute handles to characteristic UUIDs from the GATT discovery in
    the capture (Read By Type responses); when Android used its GATT cache and
    no discovery was captured, handles are shown raw (h0x002a),
  * classifies every write as a known command or not, and every notification
    as a frame the parser understands or not,
  * prints the unknown ones grouped by their first two bytes.
"""

# ruff: noqa: T201  (command-line tool: printing is its output)
from __future__ import annotations

import argparse
import importlib
import json
import struct
import sys
import types
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "oclean_ble"


def _load_protocol_modules():
    """Import commands.py and parser.py without running the integration's __init__."""
    pkg = types.ModuleType("_oclean_proto")
    pkg.__path__ = [str(_COMPONENT)]
    sys.modules["_oclean_proto"] = pkg
    return importlib.import_module("_oclean_proto.commands"), importlib.import_module("_oclean_proto.parser")


commands, parser = _load_protocol_modules()

_BTSNOOP_MAGIC = b"btsnoop\x00"
_DATALINK_H4 = 1002
_DATALINK_HCI = 1001
_H4_ACL = 0x02
_H4_EVENT = 0x04
_ATT_CID = 0x0004
# btsnoop timestamps are microseconds since 0000-01-01; this is the offset to the Unix epoch.
_BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000

# ATT opcodes
_ATT_READ_BY_TYPE_RSP = 0x09
_ATT_READ_RSP = 0x0B
_ATT_WRITE_REQ = 0x12
_ATT_NOTIFY = 0x1B
_ATT_INDICATE = 0x1D
_ATT_WRITE_CMD = 0x52
_ATT_NAMES = {
    _ATT_READ_RSP: "read",
    _ATT_WRITE_REQ: "write",
    _ATT_WRITE_CMD: "write-cmd",
    _ATT_NOTIFY: "notify",
    _ATT_INDICATE: "indicate",
}


@dataclass
class AttEvent:
    """One ATT PDU of interest."""

    index: int
    time: float
    conn: int
    kind: str  # write / write-cmd / notify / indicate / read
    handle: int
    char: str
    hex: str
    verdict: str  # known-command / unknown-command / known-frame / unknown-frame / other


def read_btsnoop(path: Path):
    """Yield (timestamp_s, received, is_data, packet) records from a btsnoop file."""
    data = path.read_bytes()
    if data[:8] != _BTSNOOP_MAGIC:
        raise ValueError("not a btsnoop file (bad magic)")
    _version, datalink = struct.unpack(">II", data[8:16])
    if datalink not in (_DATALINK_H4, _DATALINK_HCI):
        raise ValueError(f"unsupported btsnoop datalink {datalink}")
    pos = 16
    while pos + 24 <= len(data):
        _orig, incl, flags, _drops, ts = struct.unpack(">IIIIq", data[pos : pos + 24])
        pos += 24
        packet = data[pos : pos + incl]
        pos += incl
        if len(packet) < incl:
            break  # truncated last record (capture still running)
        timestamp = (ts - _BTSNOOP_EPOCH_DELTA_US) / 1e6
        received = bool(flags & 0x01)
        if datalink == _DATALINK_H4:
            if not packet:
                continue
            ptype, packet = packet[0], packet[1:]
            is_acl = ptype == _H4_ACL
            is_event = ptype == _H4_EVENT
        else:
            is_acl = not flags & 0x02
            is_event = bool(flags & 0x02) and received
        yield timestamp, received, is_acl, is_event, packet


def _connection_addresses(event: bytes) -> tuple[int, str] | None:
    """Return (conn_handle, peer address) from an LE (Enhanced) Connection Complete event."""
    if len(event) < 3 or event[0] != 0x3E:
        return None
    sub = event[2]
    if sub in (0x01, 0x0A) and len(event) >= 12 and event[3] == 0x00:
        handle = struct.unpack("<H", event[4:6])[0] & 0x0FFF
        addr = event[8:14]
        return handle, ":".join(f"{b:02X}" for b in reversed(addr))
    return None


def iter_att(path: Path, address: str | None = None):
    """Yield (timestamp, received, conn_handle, att_pdu) with ACL reassembly."""
    pending: dict[tuple[int, bool], tuple[int, bytearray, float]] = {}
    wanted: set[int] | None = None if address is None else set()
    for ts, received, is_acl, is_event, packet in read_btsnoop(path):
        if is_event:
            conn = _connection_addresses(packet)
            if conn and wanted is not None and conn[1].upper() == address.upper():
                wanted.add(conn[0])
            continue
        if not is_acl or len(packet) < 4:
            continue
        hdr, _length = struct.unpack("<HH", packet[:4])
        conn_handle, pb = hdr & 0x0FFF, (hdr >> 12) & 0x3
        if wanted is not None and conn_handle not in wanted:
            continue
        payload = packet[4:]
        key = (conn_handle, received)
        if pb == 0x01:  # continuation fragment
            if key not in pending:
                continue
            total, buf, first_ts = pending[key]
            buf.extend(payload)
        else:
            if len(payload) < 4:
                continue
            total = struct.unpack("<H", payload[:2])[0] + 4
            buf, first_ts = bytearray(payload), ts
        if len(buf) < total:
            pending[key] = (total, buf, first_ts)
            continue
        pending.pop(key, None)
        _l2len, cid = struct.unpack("<HH", buf[:4])
        if cid == _ATT_CID:
            yield first_ts, received, conn_handle, bytes(buf[4:total])


def _uuid_str(raw: bytes) -> str:
    if len(raw) == 2:
        return f"0000{struct.unpack('<H', raw)[0]:04x}-0000-1000-8000-00805f9b34fb"
    b = raw[::-1].hex()
    return f"{b[:8]}-{b[8:12]}-{b[12:16]}-{b[16:20]}-{b[20:]}"


def _char_label(uuid: str | None, handle: int) -> str:
    if uuid is None:
        return f"h0x{handle:04x}"
    alias = next((name for name, full in commands.CHAR_ALIASES.items() if full == uuid), None)
    short = commands.char_short(uuid)
    return f"{short}({alias})" if alias else short


def _known_command(value: bytes) -> bool:
    return any(value[: len(cmd.payload)] == cmd.payload for cmd in commands.KNOWN_COMMANDS)


def decode(path: Path, address: str | None = None) -> list[AttEvent]:
    """Decode a capture into the list of ATT writes/notifications/reads."""
    handle_uuid: dict[tuple[int, int], str] = {}
    events: list[AttEvent] = []
    for ts, _received, conn, pdu in iter_att(path, address):
        if not pdu:
            continue
        op = pdu[0]
        if op == _ATT_READ_BY_TYPE_RSP and len(pdu) > 2:
            item_len = pdu[1]
            # characteristic declarations: handle(2) props(1) value_handle(2) uuid(2|16)
            if item_len in (7, 21):
                for i in range(2, len(pdu) - item_len + 1, item_len):
                    item = pdu[i : i + item_len]
                    value_handle = struct.unpack("<H", item[3:5])[0]
                    handle_uuid[(conn, value_handle)] = _uuid_str(item[5:])
            continue
        if op not in _ATT_NAMES:
            continue
        if op == _ATT_READ_RSP:
            handle, value = 0, pdu[1:]
        else:
            if len(pdu) < 3:
                continue
            handle, value = struct.unpack("<H", pdu[1:3])[0], pdu[3:]
        kind = _ATT_NAMES[op]
        if kind.startswith("write"):
            verdict = "known-command" if _known_command(value) else "unknown-command"
        elif kind in ("notify", "indicate"):
            verdict = "known-frame" if parser.is_known_frame(value) else "unknown-frame"
        else:
            verdict = "other"
        events.append(
            AttEvent(
                index=len(events),
                time=round(ts, 3),
                conn=conn,
                kind=kind,
                handle=handle,
                char=_char_label(handle_uuid.get((conn, handle)), handle),
                hex=value.hex(),
                verdict=verdict,
            )
        )
    return events


def summarize(events: list[AttEvent]) -> dict[str, dict[str, dict]]:
    """Group unknown commands and frames by characteristic and 2-byte prefix."""
    groups: dict[str, dict[str, dict]] = {"unknown-command": {}, "unknown-frame": {}}
    for ev in events:
        if ev.verdict not in groups:
            continue
        key = f"{ev.char} {ev.hex[:4]}"
        entry = groups[ev.verdict].setdefault(key, {"count": 0, "examples": []})
        entry["count"] += 1
        if ev.hex not in entry["examples"] and len(entry["examples"]) < 5:
            entry["examples"].append(ev.hex)
    return groups


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", type=Path, help="btsnoop_hci.log from the phone")
    ap.add_argument("--address", help="only the connection to this Bluetooth address")
    ap.add_argument("--all", action="store_true", help="print every write/notification, not just the summary")
    ap.add_argument("--json", type=Path, help="also write every event and the summary to this JSON file")
    args = ap.parse_args(argv)

    try:
        events = decode(args.log, args.address)
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    counts: dict[str, int] = defaultdict(int)
    for ev in events:
        counts[ev.verdict] += 1
    print(f"{len(events)} ATT events: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.all:
        start = events[0].time if events else 0.0
        for ev in events:
            arrow = "->" if ev.kind.startswith("write") else "<-"
            print(f"{ev.time - start:9.3f}s {arrow} {ev.kind:9} {ev.char:18} {ev.hex}  [{ev.verdict}]")

    groups = summarize(events)
    for verdict, title in (
        ("unknown-command", "Writes not in the command table"),
        ("unknown-frame", "Frames the parser does not know"),
    ):
        print(f"\n{title}:")
        if not groups[verdict]:
            print("  (none)")
        for key, entry in sorted(groups[verdict].items(), key=lambda kv: -kv[1]["count"]):
            print(f"  {key}  x{entry['count']}  e.g. {', '.join(entry['examples'])}")

    if args.json:
        args.json.write_text(json.dumps({"events": [asdict(e) for e in events], "summary": groups}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
