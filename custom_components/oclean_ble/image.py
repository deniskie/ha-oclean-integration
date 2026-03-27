"""Brush coverage image entity for the Oclean BLE integration.

Generates an SVG tooth diagram showing which zones were brushed and how
much pressure was applied.  Individual tooth circles are placed along an
elliptical arch and split into inner/outer halves via SVG clip paths.

No external assets or dependencies required — pure Python + math.

Colour coding:
    grey   → not brushed (pressure 0)
    green  → light pressure (1-80)
    orange → medium pressure (81-160)
    red    → high pressure (161-255)
"""

from __future__ import annotations

import math
from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DATA_LAST_BRUSH_AREAS,
    DOMAIN,
)
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

# SVG canvas.
_W, _H = 300, 460
_MID_Y = 230  # horizontal divider between upper and lower jaw

# Arch geometry — ellipse parameters for tooth centre positions.
_UPPER = {"cx": _W / 2, "cy": _MID_Y - 8, "rx": 80, "ry": 160, "start": 185, "end": 355}
_LOWER = {"cx": _W / 2, "cy": _MID_Y + 8, "rx": 80, "ry": 160, "start": 5, "end": 175}
_TEETH_PER_JAW = 16
_TOOTH_R = 15

_BG = "#E0E0E0"
_BG_STROKE = "#D0D0D0"


def _pressure_to_color(pressure: int) -> str | None:
    """Return the fill colour for a pressure value, or None if not brushed."""
    if pressure <= 0:
        return None
    if pressure <= 80:
        return "#4CAF50"  # green
    if pressure <= 160:
        return "#FF9800"  # orange
    return "#F44336"  # red


# ------------------------------------------------------------------
# SVG helpers
# ------------------------------------------------------------------


def _tooth_positions(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start_deg: float,
    end_deg: float,
    count: int,
) -> list[tuple[float, float]]:
    """Return (x, y) centres for *count* teeth along an elliptical arc."""
    start_rad = math.radians(start_deg)
    end_rad = math.radians(end_deg)
    return [
        (
            cx + rx * math.cos(start_rad + (end_rad - start_rad) * i / (count - 1)),
            cy + ry * math.sin(start_rad + (end_rad - start_rad) * i / (count - 1)),
        )
        for i in range(count)
    ]


def _ellipse_arc_path(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start_deg: float,
    end_deg: float,
    segments: int = 40,
) -> str:
    """Return SVG path commands for a smooth elliptical arc."""
    start_rad = math.radians(start_deg)
    end_rad = math.radians(end_deg)
    cmds: list[str] = []
    for i in range(segments + 1):
        t = start_rad + (end_rad - start_rad) * i / segments
        x = cx + rx * math.cos(t)
        y = cy + ry * math.sin(t)
        cmds.append(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}")
    return " ".join(cmds)


def _build_clip_paths(
    jaw: str,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start: float,
    end: float,
) -> tuple[str, str]:
    """Return (outer_clip_def, inner_clip_def) SVG <clipPath> elements."""
    # Extend the arc 15° beyond each end so endpoint teeth are fully covered.
    arc = _ellipse_arc_path(cx, cy, rx, ry, start - 15, end + 15)

    first_x = cx + rx * math.cos(math.radians(start - 15))
    first_y = cy + ry * math.sin(math.radians(start - 15))

    # Outer clip: full canvas minus the ellipse interior (even-odd).
    outer_d = f"M 0 0 L {_W} 0 L {_W} {_H} L 0 {_H} Z {arc} L {first_x:.1f} {first_y:.1f} Z"
    outer = f'<clipPath id="clip-{jaw}-outer"><path d="{outer_d}" clip-rule="evenodd"/></clipPath>'

    # Inner clip: ellipse interior closed through the centre.
    inner_d = f"{arc} L {cx:.1f} {cy:.1f} Z"
    inner = f'<clipPath id="clip-{jaw}-inner"><path d="{inner_d}"/></clipPath>'
    return outer, inner


# ------------------------------------------------------------------
# SVG generation
# ------------------------------------------------------------------


def _generate_svg(areas: dict[str, int]) -> str:
    """Generate a complete SVG string for the given brush area pressures."""
    defs: list[str] = []
    elems: list[str] = []

    # Left/right clip paths for clean vertical split at the centre line.
    cx_mid = _W / 2
    defs.append(f'<clipPath id="clip-left"><rect x="0" y="0" width="{cx_mid:.1f}" height="{_H}"/></clipPath>')
    defs.append(
        f'<clipPath id="clip-right"><rect x="{cx_mid:.1f}" y="0" width="{cx_mid:.1f}" height="{_H}"/></clipPath>'
    )

    for jaw_name, jaw in [("upper", _UPPER), ("lower", _LOWER)]:
        cx, cy = jaw["cx"], jaw["cy"]
        rx, ry = jaw["rx"], jaw["ry"]
        start, end = jaw["start"], jaw["end"]

        positions = _tooth_positions(cx, cy, rx, ry, start, end, _TEETH_PER_JAW)
        mid = _TEETH_PER_JAW // 2

        outer_clip, inner_clip = _build_clip_paths(jaw_name, cx, cy, rx, ry, start, end)
        defs.append(outer_clip)
        defs.append(inner_clip)

        # Upper jaw: first half = left side, second half = right side.
        # Lower jaw: first half = right side, second half = left side
        # (the arc goes from right to left for the lower jaw).
        if jaw_name == "upper":
            sides = [("left", range(mid)), ("right", range(mid, _TEETH_PER_JAW))]
        else:
            sides = [("right", range(mid)), ("left", range(mid, _TEETH_PER_JAW))]

        for side, indices in sides:
            zone_out = f"{jaw_name}_{side}_out"
            zone_in = f"{jaw_name}_{side}_in"
            color_out = _pressure_to_color(areas.get(zone_out, 0))
            color_in = _pressure_to_color(areas.get(zone_in, 0))

            # Wrap each side in a left/right clip group.
            elems.append(f'<g clip-path="url(#clip-{side})">')

            for idx in indices:
                tx, ty = positions[idx]
                c = f'cx="{tx:.1f}" cy="{ty:.1f}" r="{_TOOTH_R}"'

                # Grey background tooth.
                elems.append(f'<circle {c} fill="{_BG}" stroke="{_BG_STROKE}" stroke-width="0.3"/>')

                # Coloured outer half (clip to outside of arch ellipse).
                if color_out:
                    elems.append(
                        f'<circle {c} fill="{color_out}" clip-path="url(#clip-{jaw_name}-outer)" opacity="0.9"/>'
                    )

                # Coloured inner half (clip to inside of arch ellipse).
                if color_in:
                    elems.append(
                        f'<circle {c} fill="{color_in}" clip-path="url(#clip-{jaw_name}-inner)" opacity="0.9"/>'
                    )

            elems.append("</g>")

    # Divider line and labels.
    elems.append(
        f'<line x1="20" y1="{_MID_Y}" x2="{_W - 20}" y2="{_MID_Y}" '
        f'stroke="{_BG_STROKE}" stroke-width="1" stroke-dasharray="4,4"/>'
    )
    elems.append(
        f'<text x="12" y="{_MID_Y + 5}" font-family="Arial,sans-serif" '
        f'font-size="13" fill="#BBB" text-anchor="middle">L</text>'
    )
    elems.append(
        f'<text x="{_W - 12}" y="{_MID_Y + 5}" font-family="Arial,sans-serif" '
        f'font-size="13" fill="#BBB" text-anchor="middle">R</text>'
    )

    defs_str = "\n    ".join(defs)
    elems_str = "\n  ".join(elems)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">\n'
        f'  <rect width="{_W}" height="{_H}" fill="white" rx="8"/>\n'
        f"  <defs>\n    {defs_str}\n  </defs>\n"
        f"  {elems_str}\n"
        f"</svg>\n"
    )


# ------------------------------------------------------------------
# Home Assistant entity
# ------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Oclean brush coverage image entity."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    async_add_entities([OcleanBrushCoverageImage(coordinator, mac, device_name)])


class OcleanBrushCoverageImage(OcleanEntity, ImageEntity):
    """Image entity showing a colour-coded brush coverage diagram."""

    _attr_content_type = "image/svg+xml"
    _attr_translation_key = "brush_coverage"

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        OcleanEntity.__init__(self, coordinator, mac, device_name, "brush_coverage")
        ImageEntity.__init__(self, coordinator.hass)
        self._cached_image: bytes | None = None
        self._cached_areas: dict[str, int] | None = None

    @property
    def available(self) -> bool:
        """Available when brush area data exists."""
        areas = self._get_areas()
        return self._session_field_available(areas)

    def _get_areas(self) -> dict[str, int] | None:
        if self.coordinator.data is None:
            return None
        areas = self.coordinator.data.get(DATA_LAST_BRUSH_AREAS)
        return areas if isinstance(areas, dict) else None

    def _handle_coordinator_update(self) -> None:
        """Invalidate cache when area data changes."""
        areas = self._get_areas()
        if areas != self._cached_areas:
            self._cached_areas = areas
            self._cached_image = None
            self._attr_image_last_updated = datetime.now()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the brush coverage SVG as bytes."""
        areas = self._get_areas()
        if areas is None:
            return None

        if self._cached_image is not None:
            return self._cached_image

        self._cached_image = _generate_svg(areas).encode("utf-8")
        return self._cached_image
