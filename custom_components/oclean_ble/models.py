"""Data models for the Oclean BLE integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OcleanDeviceData:
    """Typed value object representing one coordinator data snapshot.

    Field names are intentionally identical to the DATA_* string constants in
    const.py so that the generic ``get()`` accessor can use ``getattr``
    without an extra mapping table.
    """

    battery: int | None = None
    last_brush_score: int | None = None
    last_brush_duration: int | None = None
    last_brush_clean: int | None = None
    last_brush_pressure: float | None = None
    last_brush_time: int | None = None
    brush_head_usage: int | None = None
    last_brush_areas: dict[str, int] | None = None
    last_brush_scheme_type: int | None = None
    last_brush_pnum: int | None = None
    model_id: str | None = None
    hw_revision: str | None = None
    sw_version: str | None = None

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the field value for *key*, or *default* when it is None.

        Allows generic sensor code to look up values by the same string
        constants (DATA_BATTERY = "battery", etc.) used throughout the
        integration, without knowing the concrete field names.
        """
        value = getattr(self, key, None)
        return value if value is not None else default

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OcleanDeviceData:
        """Construct an instance from the raw dict produced by the coordinator."""
        return cls(
            battery=data.get("battery"),
            last_brush_score=data.get("last_brush_score"),
            last_brush_duration=data.get("last_brush_duration"),
            last_brush_clean=data.get("last_brush_clean"),
            last_brush_pressure=data.get("last_brush_pressure"),
            last_brush_time=data.get("last_brush_time"),
            brush_head_usage=data.get("brush_head_usage"),
            last_brush_areas=data.get("last_brush_areas"),
            last_brush_scheme_type=data.get("last_brush_scheme_type"),
            last_brush_pnum=data.get("last_brush_pnum"),
            model_id=data.get("model_id"),
            hw_revision=data.get("hw_revision"),
            sw_version=data.get("sw_version"),
        )
