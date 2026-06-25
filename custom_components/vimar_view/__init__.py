"""Vimar VIEW integration for Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.typing import ConfigType

from .api import VimarViewApi
from .const import (
    CONF_DISCOVERY,
    CONF_TOKEN,
    DATA_API,
    DATA_COORDINATOR,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import VimarViewCoordinator

SERVICE_REFRESH = "refresh"
SERVICE_EXECUTE_ROUTINE = "execute_routine"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration services."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_refresh(call: ServiceCall) -> None:
        for item in hass.data[DOMAIN].values():
            coordinator = item.get(DATA_COORDINATOR)
            if coordinator is not None:
                await coordinator.async_request_refresh()

    async def handle_execute_routine(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        plant_id = call.data["plant_id"]
        routine_id = call.data["routine_id"]
        candidates = (
            [hass.data[DOMAIN][entry_id]]
            if entry_id in hass.data[DOMAIN]
            else list(hass.data[DOMAIN].values())
        )
        if not candidates:
            return
        api: VimarViewApi = candidates[0][DATA_API]
        await api.async_execute_routine(plant_id, routine_id)
        coordinator = candidates[0].get(DATA_COORDINATOR)
        if coordinator is not None:
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXECUTE_ROUTINE,
        handle_execute_routine,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("plant_id"): str,
                vol.Required("routine_id"): str,
            }
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Vimar VIEW config entry."""
    session = aiohttp_client.async_get_clientsession(hass)

    async def token_update_callback(token: dict) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TOKEN: token},
        )

    api = VimarViewApi(
        session,
        token=dict(entry.data[CONF_TOKEN]),
        discovery=dict(entry.data[CONF_DISCOVERY]),
        token_update_callback=token_update_callback,
    )
    coordinator = VimarViewCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_API: api,
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Vimar VIEW config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
