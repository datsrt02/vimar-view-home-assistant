"""Light platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity
from .ipconnector import SFE_CMD_BRIGHTNESS, SFE_CMD_ONOFF, SFE_STATE_BRIGHTNESS, SFE_STATE_ONOFF

STATE_ON = "On"
STATE_OFF = "Off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar lights."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarLight(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "light"
    )


class VimarLight(VimarDataPointEntity, LightEntity):
    """A Vimar light."""

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return supported color modes."""
        if self._brightness_value is not None or self._has_command(SFE_CMD_BRIGHTNESS):
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode:
        """Return current color mode."""
        if ColorMode.BRIGHTNESS in self.supported_color_modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return true if the light is on."""
        return _state_bool(self._value_for(SFE_STATE_ONOFF, SFE_CMD_ONOFF))

    @property
    def brightness(self) -> int | None:
        """Return light brightness on HA's 0-255 scale."""
        value = self._brightness_value
        if value is None:
            return None
        if value <= 100:
            return max(0, min(255, round(value * 255 / 100)))
        return max(0, min(255, round(value)))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        if ATTR_BRIGHTNESS in kwargs and self._has_command(SFE_CMD_BRIGHTNESS):
            brightness = int(kwargs[ATTR_BRIGHTNESS])
            await self._send(SFE_CMD_BRIGHTNESS, str(round(brightness * 100 / 255)))
        if self._has_command(SFE_CMD_ONOFF):
            await self._send(SFE_CMD_ONOFF, STATE_ON)
        elif ATTR_BRIGHTNESS not in kwargs:
            raise HomeAssistantError("This Vimar light does not expose an on/off command")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        if not self._has_command(SFE_CMD_ONOFF):
            raise HomeAssistantError("This Vimar light does not expose an on/off command")
        await self._send(SFE_CMD_ONOFF, STATE_OFF)
        await self.coordinator.async_request_refresh()

    @property
    def _brightness_value(self) -> float | None:
        value = self._value_for(SFE_STATE_BRIGHTNESS, SFE_CMD_BRIGHTNESS)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _value_for(self, *keys: str) -> Any:
        values = self.vimar_entity.features.get("values") or {}
        for key in keys:
            value = values.get(key)
            if value not in (None, ""):
                return value
        return None

    def _has_command(self, sfetype: str) -> bool:
        return sfetype in (self.vimar_entity.features.get("commands") or {})

    async def _send(self, sfetype: str, value: str) -> None:
        await self.coordinator.api.async_execute_entity_action(self.vimar_entity, sfetype, value)


def _state_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "on", "true", "yes"}:
        return True
    if normalized in {"0", "off", "false", "no"}:
        return False
    return None
