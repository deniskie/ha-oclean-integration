"""HA long-term statistics import for Oclean brush sessions."""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from .const import (
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Metrics exported to HA long-term statistics
# (data_key, statistic_name_suffix, unit_of_measurement)
_STAT_METRICS: tuple[tuple[str, str, str | None], ...] = (
    (DATA_LAST_BRUSH_SCORE, "brush_score", "%"),
    (DATA_LAST_BRUSH_DURATION, "brush_duration", "s"),
    (DATA_LAST_BRUSH_PRESSURE, "brush_pressure", None),
)


def _load_recorder_api():
    """Load recorder statistics API lazily (absent on some HA setups).

    Returns (StatisticData, StatisticMetaData, async_add_external_statistics)
    or None if the recorder component is unavailable.
    """
    try:
        from homeassistant.components.recorder.statistics import (
            StatisticData,
            StatisticMetaData,
            async_add_external_statistics,
        )

        return StatisticData, StatisticMetaData, async_add_external_statistics
    except ImportError:
        pass
    try:
        from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
        from homeassistant.components.recorder.statistics import async_add_external_statistics

        return StatisticData, StatisticMetaData, async_add_external_statistics
    except ImportError:
        return None


async def import_new_sessions(
    hass: HomeAssistant,
    mac_slug: str,
    device_name: str,
    sessions: list[dict[str, Any]],
    last_session_ts: int,
) -> int:
    """Import brush sessions newer than *last_session_ts* into HA long-term statistics.

    Uses recorder.statistics.async_add_external_statistics so that historical
    sessions (e.g. recorded while HA was offline) appear with their actual
    timestamps in HA energy/statistics graphs.

    Returns the updated last_session_ts (unchanged when no new sessions exist).
    """
    new_sessions = [s for s in sessions if s.get("last_brush_time", 0) > last_session_ts]
    if not new_sessions:
        _LOGGER.debug("Oclean no new sessions to import into statistics")
        return last_session_ts

    _LOGGER.debug("Oclean importing %d new session(s) into HA statistics:", len(new_sessions))
    for s in new_sessions:
        ts = s.get("last_brush_time", 0)
        _LOGGER.debug(
            "Oclean  → import ts=%d (%s)",
            ts,
            datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "n/a",
        )

    recorder_api = _load_recorder_api()
    if recorder_api is None:
        _LOGGER.debug("Oclean recorder statistics API not available; skipping history import")
        return last_session_ts
    StatisticData, StatisticMetaData, async_add_external_statistics = recorder_api

    from homeassistant.util import dt as dt_util

    for data_key, stat_suffix, unit in _STAT_METRICS:
        stat_rows: list[Any] = []
        for session in new_sessions:
            value = session.get(data_key)
            if value is None:
                continue
            ts = session["last_brush_time"]
            start_dt = datetime.datetime.fromtimestamp(ts, tz=dt_util.UTC).replace(minute=0, second=0, microsecond=0)
            stat_rows.append(StatisticData(start=start_dt, mean=float(value), state=float(value)))

        if not stat_rows:
            continue

        metadata = StatisticMetaData(
            has_mean=True,
            has_sum=False,
            name=f"Oclean {device_name} {stat_suffix.replace('_', ' ').title()}",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:{mac_slug}_{stat_suffix}",
            unit_of_measurement=unit,
        )
        try:
            async_add_external_statistics(hass, metadata, stat_rows)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Oclean statistics import failed for '%s_%s': %s – skipping",
                mac_slug,
                stat_suffix,
                err,
            )
            continue
        _LOGGER.debug(
            "Oclean imported %d row(s) for statistic '%s:%s_%s'",
            len(stat_rows),
            DOMAIN,
            mac_slug,
            stat_suffix,
        )

    # Per-zone area pressures as individual statistics
    area_stats_by_zone: dict[str, list[Any]] = {}
    for session in new_sessions:
        areas = session.get(DATA_LAST_BRUSH_AREAS)
        if not isinstance(areas, dict):
            continue
        ts = session["last_brush_time"]
        start_dt = datetime.datetime.fromtimestamp(ts, tz=dt_util.UTC).replace(minute=0, second=0, microsecond=0)
        for zone_name, pressure in areas.items():
            area_stats_by_zone.setdefault(zone_name, []).append(
                StatisticData(start=start_dt, mean=float(pressure), state=float(pressure))
            )

    for zone_name, stat_rows in area_stats_by_zone.items():
        metadata = StatisticMetaData(
            has_mean=True,
            has_sum=False,
            name=f"Oclean {device_name} Area {zone_name.replace('_', ' ').title()}",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:{mac_slug}_area_{zone_name}",
            unit_of_measurement=None,
        )
        try:
            async_add_external_statistics(hass, metadata, stat_rows)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Oclean statistics import failed for area '%s': %s – skipping",
                zone_name,
                err,
            )
            continue
        _LOGGER.debug(
            "Oclean imported %d row(s) for area statistic '%s:%s_area_%s'",
            len(stat_rows),
            DOMAIN,
            mac_slug,
            zone_name,
        )

    max_ts = max(s.get("last_brush_time", 0) for s in new_sessions)
    return max_ts if max_ts > last_session_ts else last_session_ts
