"""Serialized local I/O and availability checks."""

import asyncio
import logging
from datetime import timedelta
from functools import partial

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from miio import DeviceException

from .const import DOMAIN, MODEL
from .protocol import InvalidResponse, create_device, mac_id

_LOGGER = logging.getLogger(__name__)


class XiaomiIRCoordinator(DataUpdateCoordinator):
    """Keep all miIO operations, including learning, serialized."""

    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=60),
        )
        self.device = create_device(entry.data["host"], entry.data["token"])
        self.lock = asyncio.Lock()
        self.unique_id = entry.unique_id

    async def async_io(self, function, *args, **kwargs):
        """Run blocking I/O, keeping the lock until the worker really finishes.

        Caller owns self.lock. Cancellation must not leave a socket operation
        running concurrently with the next service or availability update.
        """
        job = self.hass.async_add_executor_job(partial(function, *args, **kwargs))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            try:
                await job
            except Exception:  # Worker errors must not replace cancellation.
                pass
            raise

    async def _async_update_data(self):
        async with self.lock:
            try:
                info = await self.async_io(self.device.info, skip_cache=True)
                if info.model != MODEL or mac_id(info.mac_address) != self.unique_id:
                    raise InvalidResponse("Unexpected device identity")
                return info
            except (DeviceException, OSError, ValueError) as err:
                raise UpdateFailed(
                    "Cannot reach the configured Xiaomi IR device"
                ) from err
