"""Unit tests for protocol.py – DeviceProtocol profiles and model lookup."""

from __future__ import annotations

import pytest

from custom_components.oclean_ble.const import (
    CHANGE_INFO_UUID,
    CMD_QUERY_RUNNING_DATA,
    CMD_QUERY_RUNNING_DATA_T1,
    CMD_QUERY_STATUS,
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    SEND_BRUSH_CMD_UUID,
    WRITE_CHAR_UUID,
)
from custom_components.oclean_ble.protocol import (
    LEGACY,
    TYPE0,
    TYPE1,
    UNKNOWN,
    DeviceProtocol,
    protocol_for_model,
)

# ===========================================================================
# DeviceProtocol dataclass
# ===========================================================================


class TestDeviceProtocolDataclass:
    def test_is_frozen(self):
        """DeviceProtocol must be immutable."""
        with pytest.raises((TypeError, AttributeError)):
            TYPE1.name = "changed"  # type: ignore[misc]

    def test_fields_present(self):
        assert hasattr(TYPE0, "name")
        assert hasattr(TYPE0, "notify_chars")
        assert hasattr(TYPE0, "query_commands")
        assert hasattr(TYPE0, "supports_pagination")

    def test_query_commands_are_pairs(self):
        """Every entry in query_commands must be a (str, bytes) pair."""
        for profile in (TYPE0, TYPE1, LEGACY, UNKNOWN):
            for item in profile.query_commands:
                assert len(item) == 2
                assert isinstance(item[0], str)
                assert isinstance(item[1], bytes)


# ===========================================================================
# TYPE0 – Oclean X Pro / OCLEANY3
# ===========================================================================


class TestType0Profile:
    def test_name(self):
        assert TYPE0.name == "Type-0"

    def test_supports_pagination(self):
        assert TYPE0.supports_pagination is True

    def test_subscribes_read_notify(self):
        assert READ_NOTIFY_CHAR_UUID in TYPE0.notify_chars

    def test_subscribes_change_info(self):
        """CHANGE_INFO_UUID is present on Type-0 devices only."""
        assert CHANGE_INFO_UUID in TYPE0.notify_chars

    def test_does_not_subscribe_receive_brush(self):
        """RECEIVE_BRUSH_UUID is Type-1 only – must not appear in TYPE0."""
        assert RECEIVE_BRUSH_UUID not in TYPE0.notify_chars

    def test_sends_status_command(self):
        cmds = [cmd for _, cmd in TYPE0.query_commands]
        assert CMD_QUERY_STATUS in cmds

    def test_sends_0308_running_data(self):
        cmds = [cmd for _, cmd in TYPE0.query_commands]
        assert CMD_QUERY_RUNNING_DATA in cmds

    def test_does_not_send_0307(self):
        cmds = [cmd for _, cmd in TYPE0.query_commands]
        assert CMD_QUERY_RUNNING_DATA_T1 not in cmds

    def test_status_sent_to_write_char(self):
        char, cmd = next(p for p in TYPE0.query_commands if p[1] == CMD_QUERY_STATUS)
        assert char == WRITE_CHAR_UUID


# ===========================================================================
# TYPE1 – Oclean X / OCLEANY3M  +  Oclean X Pro Elite / OCLEANY3P
# ===========================================================================


class TestType1Profile:
    def test_name(self):
        assert TYPE1.name == "Type-1"

    def test_does_not_support_pagination(self):
        assert TYPE1.supports_pagination is False

    def test_subscribes_read_notify(self):
        assert READ_NOTIFY_CHAR_UUID in TYPE1.notify_chars

    def test_subscribes_receive_brush(self):
        """RECEIVE_BRUSH_UUID is the Type-1 session notification channel."""
        assert RECEIVE_BRUSH_UUID in TYPE1.notify_chars

    def test_does_not_subscribe_change_info(self):
        """CHANGE_INFO_UUID is absent on Type-1 devices (confirmed via logs)."""
        assert CHANGE_INFO_UUID not in TYPE1.notify_chars

    def test_sends_status_command(self):
        cmds = [cmd for _, cmd in TYPE1.query_commands]
        assert CMD_QUERY_STATUS in cmds

    def test_sends_0307_running_data(self):
        cmds = [cmd for _, cmd in TYPE1.query_commands]
        assert CMD_QUERY_RUNNING_DATA_T1 in cmds

    def test_does_not_send_0308(self):
        cmds = [cmd for _, cmd in TYPE1.query_commands]
        assert CMD_QUERY_RUNNING_DATA not in cmds

    def test_0307_sent_to_send_brush_cmd(self):
        char, cmd = next(p for p in TYPE1.query_commands if p[1] == CMD_QUERY_RUNNING_DATA_T1)
        assert char == SEND_BRUSH_CMD_UUID

    def test_standalone_write_char_is_fbb85(self):
        """APK C3376s.java: f12501k = fbb85 for all standalone writes (0206, 0201, 0209, 0217).
        Only 0307 uses f12582C = fbb89. write_char must be WRITE_CHAR_UUID so brush
        scheme and other config commands reach the device."""
        assert TYPE1.write_char == WRITE_CHAR_UUID


# ===========================================================================
# LEGACY – Oclean Air 1 / OCLEANA1
# ===========================================================================


class TestLegacyProfile:
    def test_name(self):
        assert LEGACY.name == "Legacy"

    def test_does_not_support_pagination(self):
        assert LEGACY.supports_pagination is False

    def test_no_notify_chars(self):
        """OCLEANA1 has no working notify characteristics."""
        assert len(LEGACY.notify_chars) == 0

    def test_only_sends_status(self):
        cmds = [cmd for _, cmd in LEGACY.query_commands]
        assert cmds == [CMD_QUERY_STATUS]


# ===========================================================================
# UNKNOWN – fallback profile
# ===========================================================================


class TestUnknownProfile:
    def test_supports_pagination(self):
        """UNKNOWN must support pagination so new devices are fully explored."""
        assert UNKNOWN.supports_pagination is True

    def test_includes_all_notify_chars(self):
        """UNKNOWN must subscribe to all known notify characteristics."""
        for char in (READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID, CHANGE_INFO_UUID, SEND_BRUSH_CMD_UUID):
            assert char in UNKNOWN.notify_chars

    def test_includes_all_commands(self):
        """UNKNOWN must send all known query commands."""
        cmds = [cmd for _, cmd in UNKNOWN.query_commands]
        assert CMD_QUERY_STATUS in cmds
        assert CMD_QUERY_RUNNING_DATA in cmds
        assert CMD_QUERY_RUNNING_DATA_T1 in cmds

    def test_is_superset_of_type0_notify(self):
        for char in TYPE0.notify_chars:
            assert char in UNKNOWN.notify_chars

    def test_is_superset_of_type1_notify(self):
        for char in TYPE1.notify_chars:
            assert char in UNKNOWN.notify_chars


# ===========================================================================
# protocol_for_model() lookup
# ===========================================================================


class TestProtocolForModel:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("OCLEANY3M", TYPE1),  # Oclean X – confirmed
            ("OCLEANY3P", TYPE1),  # Oclean X Pro Elite – confirmed (issue #3)
            ("OCLEANY3", TYPE1),  # Oclean X Pro – reclassified (issue #49)
            ("OCLEANA1", LEGACY),  # Oclean Air 1 – confirmed (issue #7)
        ],
    )
    def test_known_models(self, model_id: str, expected: DeviceProtocol):
        assert protocol_for_model(model_id) is expected

    def test_unknown_model_returns_unknown(self):
        assert protocol_for_model("OCLEANFUTURE99") is UNKNOWN

    def test_none_returns_unknown(self):
        assert protocol_for_model(None) is UNKNOWN

    def test_empty_string_returns_unknown(self):
        assert protocol_for_model("") is UNKNOWN

    def test_case_sensitive(self):
        """Model IDs from DIS are uppercase; lowercase must not match."""
        assert protocol_for_model("ocleany3m") is UNKNOWN

    def test_ocleany3p_is_type1_not_type0(self):
        """OCLEANY3P sends 0307 on RECEIVE_BRUSH_UUID – must NOT map to TYPE0."""
        proto = protocol_for_model("OCLEANY3P")
        assert RECEIVE_BRUSH_UUID in proto.notify_chars
        assert CHANGE_INFO_UUID not in proto.notify_chars
        assert proto.supports_pagination is False

    def test_ocleany3_is_type1_not_type0(self):
        """OCLEANY3 (Oclean X Pro) requires 0307 cmd – must NOT map to TYPE0 (issue #49)."""
        proto = protocol_for_model("OCLEANY3")
        assert RECEIVE_BRUSH_UUID in proto.notify_chars
        assert CHANGE_INFO_UUID not in proto.notify_chars
        assert proto.supports_pagination is False
