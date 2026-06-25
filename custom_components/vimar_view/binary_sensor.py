"""Binary sensor platform for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarDataPointEntity

TRUTHY = {"1", "active", "enabled", "on", "online", "open", "true", "yes"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarBinarySensor(coordinator, entity_id)
        for entity_id, entity in coordinator.data.entities.items()
        if entity.platform == "binary_sensor"
    )


class VimarBinarySensor(VimarDataPointEntity, BinarySensorEntity):
    """A Vimar boolean datapoint."""

    @property
    def is_on(self) -> bool | None:
        """Return true if the datapoint is on."""
        value: Any = self.vimar_entity.value
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        return str(value).lower() in TRUTHY

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Guess a binary sensor device class."""
        hint = f"{self.vimar_entity.category or ''} {self.name}".lower()
        if "door" in hint or "window" in hint or "gate" in hint:
            return BinarySensorDeviceClass.OPENING
        if "motion" in hint or "presence" in hint:
            return BinarySensorDeviceClass.MOTION
        if "online" in hint or "connect" in hint:
            return BinarySensorDeviceClass.CONNECTIVITY
        return None
