"""Climate platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ATTR_TEMPERATURE, ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity
from .ipconnector import (
    SFE_CMD_AMBIENT_SETPOINT,
    SFE_CMD_CHANGEOVER,
    SFE_CMD_HVAC_MODE,
    SFE_CMD_HVAC_MODE_DEAD_ZONE,
    SFE_STATE_AMBIENT_SETPOINT,
    SFE_STATE_AMBIENT_TEMP,
    SFE_STATE_CHANGEOVER,
    SFE_STATE_CLIMA_TEMP,
    SFE_STATE_HVAC_MODE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar climates."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarClimate(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "climate"
    )


class VimarClimate(VimarDataPointEntity, ClimateEntity):
    """A Vimar climate zone."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
        HVACMode.AUTO,
    ]

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported climate features."""
        features = ClimateEntityFeature(0)
        if self._has_command(SFE_CMD_AMBIENT_SETPOINT):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return _number(self._value_for(SFE_STATE_AMBIENT_TEMP, SFE_STATE_CLIMA_TEMP))

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        return _number(self._value_for(SFE_STATE_AMBIENT_SETPOINT, SFE_CMD_AMBIENT_SETPOINT))

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode."""
        return _hvac_mode(
            self._value_for(
                SFE_STATE_HVAC_MODE,
                SFE_STATE_CHANGEOVER,
                SFE_CMD_HVAC_MODE,
                SFE_CMD_CHANGEOVER,
            )
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return
        if not self._has_command(SFE_CMD_AMBIENT_SETPOINT):
            raise HomeAssistantError("This Vimar climate entity does not expose a setpoint command")
        await self.coordinator.api.async_execute_entity_action(
            self.vimar_entity,
            SFE_CMD_AMBIENT_SETPOINT,
            str(kwargs[ATTR_TEMPERATURE]),
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode if the gateway exposes a command."""
        command = self._hvac_command
        if command is None:
            raise HomeAssistantError("This Vimar climate entity does not expose a HVAC mode command")
        await self.coordinator.api.async_execute_entity_action(
            self.vimar_entity,
            command,
            _hvac_command_value(hvac_mode),
        )
        await self.coordinator.async_request_refresh()

    @property
    def _hvac_command(self) -> str | None:
        for command in (SFE_CMD_HVAC_MODE, SFE_CMD_HVAC_MODE_DEAD_ZONE, SFE_CMD_CHANGEOVER):
            if self._has_command(command):
                return command
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


def _hvac_mode(value: Any) -> HVACMode | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if "off" in normalized:
        return HVACMode.OFF
    if "auto" in normalized or "automatic" in normalized:
        return HVACMode.AUTO
    if "cool" in normalized:
        return HVACMode.COOL
    if "heat" in normalized:
        return HVACMode.HEAT
    if normalized in {"comfort", "economy", "manual", "reduction", "protection"}:
        return HVACMode.HEAT_COOL
    return HVACMode.HEAT_COOL


def _hvac_command_value(hvac_mode: HVACMode) -> str:
    if hvac_mode == HVACMode.OFF:
        return "Off"
    if hvac_mode == HVACMode.COOL:
        return "Cooling"
    if hvac_mode == HVACMode.HEAT:
        return "Heating"
    return "Auto"
