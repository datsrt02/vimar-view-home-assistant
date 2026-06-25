"""Data coordinator for Vimar VIEW."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VimarViewApi, VimarViewAuthError, VimarViewError
from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN
from .models import VimarSystemData

_LOGGER = logging.getLogger(__name__)


class VimarViewCoordinator(DataUpdateCoordinator[VimarSystemData]):
    """Coordinate Vimar VIEW cloud updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: VimarViewApi) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
            always_update=False,
        )
        self.api = api

    async def _async_update_data(self) -> VimarSystemData:
        """Fetch a fresh account snapshot."""
        try:
            return await self.api.async_fetch_system()
        except VimarViewAuthError as err:
            raise ConfigEntryAuthFailed from err
        except VimarViewError as err:
            raise UpdateFailed(str(err)) from err
