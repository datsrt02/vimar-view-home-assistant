"""Cover platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity
from .ipconnector import (
    SFE_CMD_SHUTTER,
    SFE_CMD_SHUTTER_WO_POSITION,
    SFE_STATE_SHUTTER,
    SFE_STATE_SHUTTER_WO_POSITION,
)

STATE_UP = "Up"
STATE_DOWN = "Down"
STATE_STOP = "Stop"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar covers."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarCover(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "cover"
    )


class VimarCover(VimarDataPointEntity, CoverEntity):
    """A Vimar cover or curtain."""

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return supported cover features."""
        features = CoverEntityFeature(0)
        if self._movement_command is not None:
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if self._has_command(SFE_CMD_SHUTTER):
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Return cover position."""
        value = _number(self._value_for(SFE_STATE_SHUTTER, SFE_CMD_SHUTTER))
        if value is None:
            return None
        return max(0, min(100, round(value)))

    @property
    def is_closed(self) -> bool | None:
        """Return true if cover is closed."""
        position = self.current_cover_position
        if position is not None:
            return position <= 0
        value = self._value_for(
            SFE_STATE_SHUTTER_WO_POSITION,
            SFE_STATE_SHUTTER,
            SFE_CMD_SHUTTER_WO_POSITION,
            SFE_CMD_SHUTTER,
        )
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in {"down", "close", "closed", "0"}:
            return True
        if normalized in {"up", "open", "opened", "100"}:
            return False
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._send_movement(STATE_UP)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._send_movement(STATE_DOWN)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover movement."""
        await self._send_movement(STATE_STOP)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set cover position."""
        if not self._has_command(SFE_CMD_SHUTTER):
            raise HomeAssistantError("This Vimar cover does not expose a position command")
        position = int(kwargs[ATTR_POSITION])
        await self.coordinator.api.async_execute_entity_action(
            self.vimar_entity,
            SFE_CMD_SHUTTER,
            str(max(0, min(100, position))),
        )
        await self.coordinator.async_request_refresh()

    async def _send_movement(self, value: str) -> None:
        command = self._movement_command
        if command is None:
            raise HomeAssistantError("This Vimar cover does not expose a movement command")
        await self.coordinator.api.async_execute_entity_action(self.vimar_entity, command, value)
        await self.coordinator.async_request_refresh()

    @property
    def _movement_command(self) -> str | None:
        if self._has_command(SFE_CMD_SHUTTER_WO_POSITION):
            return SFE_CMD_SHUTTER_WO_POSITION
        if self._has_command(SFE_CMD_SHUTTER):
            return SFE_CMD_SHUTTER
        return None

    def _has_command(self, sfetype: str) -> bool:
        return sfetype in (self.vimar_entity.features.get("commands") or {})

    def _value_for(self, *keys: str) -> Any:
        values = self.vimar_entity.features.get("values") or {}
        for key in keys:
            value = values.get(key)
            if value not in (None, ""):
                return value
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
