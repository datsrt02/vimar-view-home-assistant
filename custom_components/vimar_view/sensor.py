"""Sensor platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarSensor(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "sensor"
    )


class VimarSensor(VimarDataPointEntity, SensorEntity):
    """A Vimar datapoint sensor."""

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.vimar_entity.value
