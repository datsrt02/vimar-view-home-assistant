"""Base entities for Vimar VIEW."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VimarViewCoordinator
from .models import VimarDataEntity, VimarDevice, VimarPlant, VimarRoutine


class VimarCoordinatorEntity(CoordinatorEntity[VimarViewCoordinator]):
    """Base coordinator entity."""

    _attr_has_entity_name = True


class VimarDataPointEntity(VimarCoordinatorEntity):
    """Base entity for extracted Vimar datapoints."""

    def __init__(self, coordinator: VimarViewCoordinator, entity_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self._vimar_entity_id = entity_id
        self._attr_unique_id = entity_id

    @property
    def vimar_entity(self) -> VimarDataEntity:
        """Return the latest entity data."""
        return self.coordinator.data.entities[self._vimar_entity_id]

    @property
    def name(self) -> str:
        """Return entity name."""
        return self.vimar_entity.name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        entity = self.vimar_entity
        if entity.device_id and entity.device_id in self.coordinator.data.devices:
            device = self.coordinator.data.devices[entity.device_id]
            return _device_info(device, self.coordinator.data.plants.get(device.plant_id or ""))
        if entity.plant_id and entity.plant_id in self.coordinator.data.plants:
            plant = self.coordinator.data.plants[entity.plant_id]
            return _plant_device_info(plant)
        return DeviceInfo(identifiers={(DOMAIN, "account")}, manufacturer="Vimar", name="Vimar VIEW")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return useful diagnostic attributes."""
        entity = self.vimar_entity
        return {
            "vimar_id": entity.id,
            "plant_id": entity.plant_id,
            "device_id": entity.device_id,
            "category": entity.category,
            "path": entity.path,
            "idsf": entity.features.get("idsf"),
            "sftype": entity.features.get("sftype"),
            "sstype": entity.features.get("sstype"),
            "room": entity.features.get("room"),
        }


class VimarRoutineEntity(VimarCoordinatorEntity):
    """Base entity for routines."""

    def __init__(self, coordinator: VimarViewCoordinator, routine_key: str) -> None:
        """Initialize routine entity."""
        super().__init__(coordinator)
        self._routine_key = routine_key
        self._attr_unique_id = f"routine:{routine_key}"

    @property
    def routine(self) -> VimarRoutine:
        """Return the latest routine."""
        return self.coordinator.data.routines[self._routine_key]

    @property
    def name(self) -> str:
        """Return routine name."""
        return self.routine.name

    @property
    def device_info(self) -> DeviceInfo:
        """Return plant device info."""
        routine = self.routine
        plant = self.coordinator.data.plants.get(routine.plant_id)
        if plant:
            return _plant_device_info(plant)
        return DeviceInfo(identifiers={(DOMAIN, f"plant:{routine.plant_id}")}, manufacturer="Vimar")


def _plant_device_info(plant: VimarPlant) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"plant:{plant.id}")},
        manufacturer="Vimar",
        name=plant.name,
    )


def _device_info(device: VimarDevice, plant: VimarPlant | None = None) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"device:{device.id}")},
        manufacturer=device.manufacturer,
        name=device.name,
        model=device.model,
    )
    if plant is not None:
        info["via_device"] = (DOMAIN, f"plant:{plant.id}")
    return info
