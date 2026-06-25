"""Button platform for Vimar VIEW routines."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import VimarRoutineEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vimar routine buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        VimarRoutineButton(coordinator, routine_key)
        for routine_key in coordinator.data.routines
    )


class VimarRoutineButton(VimarRoutineEntity, ButtonEntity):
    """Button that executes a Vimar routine."""

    async def async_press(self) -> None:
        """Execute the routine."""
        routine = self.routine
        await self.coordinator.api.async_execute_routine(routine.plant_id, routine.id)
        await self.coordinator.async_request_refresh()
