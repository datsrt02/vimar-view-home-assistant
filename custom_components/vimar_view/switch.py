"""Switch platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity
from .ipconnector import SFE_CMD_ONOFF, SFE_STATE_ONOFF

STATE_ON = "On"
STATE_OFF = "Off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar switches."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarSwitch(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "switch"
    )


class VimarSwitch(VimarDataPointEntity, SwitchEntity):
    """A Vimar switch."""

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        return _state_bool(self._value_for(SFE_STATE_ONOFF, SFE_CMD_ONOFF))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self._send_onoff(STATE_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self._send_onoff(STATE_OFF)

    async def _send_onoff(self, value: str) -> None:
        if SFE_CMD_ONOFF not in (self.vimar_entity.features.get("commands") or {}):
            raise HomeAssistantError("This Vimar switch does not expose an on/off command")
        await self.coordinator.api.async_execute_entity_action(
            self.vimar_entity,
            SFE_CMD_ONOFF,
            value,
        )
        await self.coordinator.async_request_refresh()

    def _value_for(self, *keys: str) -> Any:
        values = self.vimar_entity.features.get("values") or {}
        for key in keys:
            value = values.get(key)
            if value not in (None, ""):
                return value
        return None


def _state_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "active", "on", "true", "yes"}:
        return True
    if normalized in {"0", "inactive", "off", "false", "no"}:
        return False
    return None
