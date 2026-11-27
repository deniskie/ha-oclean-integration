"""Tests for tools/oclean_btsnoop.py against a synthetic Android capture."""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "oclean_btsnoop.py"
_spec = importlib.util.spec_from_file_location("oclean_btsnoop", _TOOL)
btsnoop = importlib.util.module_from_spec(_spec)
sys.modules["oclean_btsnoop"] = btsnoop
_spec.loader.exec_module(btsnoop)

_CONN = 0x0040
_OTHER_CONN = 0x0041
_BRUSH_ADDR = "AA:BB:CC:DD:EE:02"
# Characteristic value handles in the synthetic GATT table
_H_WRITE, _H_CMD, _H_RECV = 0x0010, 0x0014, 0x0018
_UUID_BASE = bytes.fromhex("855b673fbb")  # tail shared by the Oclean characteristics


def _uuid_le(uuid: str) -> bytes:
    return bytes.fromhex(uuid.replace("-", ""))[::-1]


def _record(packet: bytes, received: bool, ts_us: int = 0x00DCDDB30F2F8000 + 1_700_000_000_000_000) -> bytes:
    flags = 0x01 if received else 0x00
    if packet[0] == 0x04:
        flags |= 0x02
    return struct.pack(">IIIIq", len(packet), len(packet), flags, 0, ts_us) + packet


def _acl(conn: int, l2cap: bytes, first: bool = True) -> bytes:
    pb = 0x2 if first else 0x1
    return b"\x02" + struct.pack("<HH", conn | (pb << 12), len(l2cap)) + l2cap


def _att(conn: int, pdu: bytes) -> bytes:
    return _acl(conn, struct.pack("<HH", len(pdu), 4) + pdu)


def _conn_complete(conn: int, addr: str) -> bytes:
    raw_addr = bytes.fromhex(addr.replace(":", ""))[::-1]
    body = bytes([0x01, 0x00]) + struct.pack("<H", conn) + bytes([0x00, 0x00]) + raw_addr + bytes(5)
    return bytes([0x04, 0x3E, len(body)]) + body


def _write_capture(path: Path) -> None:
    from custom_components.oclean_ble.const import RECEIVE_BRUSH_UUID, SEND_BRUSH_CMD_UUID, WRITE_CHAR_UUID

    decl = b""
    for handle, uuid in ((_H_WRITE, WRITE_CHAR_UUID), (_H_CMD, SEND_BRUSH_CMD_UUID), (_H_RECV, RECEIVE_BRUSH_UUID)):
        decl += struct.pack("<HBH", handle - 1, 0x1A, handle) + _uuid_le(uuid)
    notify_known = bytes.fromhex("03072a42230000000000641a010f0800000200b4")
    notify_unknown = bytes.fromhex("9901aabbccdd")
    long_unknown = bytes.fromhex("77") * 30  # fragmented over two ACL packets
    long_pdu = b"\x1b" + struct.pack("<H", _H_RECV) + long_unknown
    l2 = struct.pack("<HH", len(long_pdu), 4) + long_pdu
    records = [
        _record(_conn_complete(_CONN, _BRUSH_ADDR), True),
        _record(_att(_CONN, bytes([0x09, len(decl) // 3]) + decl), True),
        _record(_att(_CONN, b"\x12" + struct.pack("<H", _H_CMD) + bytes.fromhex("0307")), False),
        _record(_att(_CONN, b"\x12" + struct.pack("<H", _H_WRITE) + bytes.fromhex("0299aa")), False),
        _record(_att(_CONN, b"\x1b" + struct.pack("<H", _H_RECV) + notify_known), True),
        _record(_att(_CONN, b"\x1b" + struct.pack("<H", _H_RECV) + notify_unknown), True),
        _record(_acl(_CONN, l2[:20]), True),
        _record(_acl(_CONN, l2[20:], first=False), True),
        _record(_att(_OTHER_CONN, b"\x1b" + struct.pack("<H", 0x0030) + b"\x55\x66"), True),
    ]
    path.write_bytes(b"btsnoop\x00" + struct.pack(">II", 1, 1002) + b"".join(records))


def test_decode_classifies(tmp_path):
    log = tmp_path / "btsnoop_hci.log"
    _write_capture(log)
    events = btsnoop.decode(log)
    kinds = [(e.kind, e.char, e.hex, e.verdict) for e in events]
    assert ("write", "fbb89(brush_cmd)", "0307", "known-command") in kinds
    assert ("write", "fbb85(write)", "0299aa", "unknown-command") in kinds
    assert ("notify", "fbb90", "03072a42230000000000641a010f0800000200b4", "known-frame") in kinds
    assert ("notify", "fbb90", "9901aabbccdd", "unknown-frame") in kinds
    assert ("notify", "fbb90", "77" * 30, "unknown-frame") in kinds  # reassembled
    assert ("notify", "h0x0030", "5566", "unknown-frame") in kinds  # no discovery for this handle


def test_address_filter_and_summary(tmp_path):
    log = tmp_path / "btsnoop_hci.log"
    _write_capture(log)
    events = btsnoop.decode(log, address=_BRUSH_ADDR)
    assert all(e.conn == _CONN for e in events)
    summary = btsnoop.summarize(events)
    assert summary["unknown-command"]["fbb85(write) 0299"]["count"] == 1
    assert set(summary["unknown-frame"]) == {"fbb90 9901", "fbb90 7777"}


def test_main_outputs(tmp_path, capsys):
    log = tmp_path / "btsnoop_hci.log"
    _write_capture(log)
    out_json = tmp_path / "out.json"
    assert btsnoop.main([str(log), "--all", "--json", str(out_json)]) == 0
    printed = capsys.readouterr().out
    assert "Frames the parser does not know" in printed
    assert "9901aabbccdd" in printed
    assert out_json.exists()


def test_rejects_non_btsnoop(tmp_path):
    bad = tmp_path / "x.log"
    bad.write_bytes(b"not a capture")
    assert btsnoop.main([str(bad)]) == 1
