"""Expose Xiaomi as hardware for the standard Infrared devices panel."""

import math

from homeassistant.components.infrared import InfraredEmitterEntity
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from miio import ChuangmiIr, DeviceException

from .const import DOMAIN, MODEL
from .protocol import check_ack

PARALLEL_UPDATES = 1
PRONTO_TICK_US = 0.241246


def timings_to_pronto(timings: list[int], modulation: int) -> str:
    """Encode carrier cycles without adding or reapplying command repeats.

    Xiaomi's native encoding has a sixteen-entry duration table. Reject a
    signal that cannot be represented, rather than changing its waveform.
    python-miio handles the final Pronto-to-Xiaomi encoding.
    """
    if (
        not isinstance(modulation, int)
        or isinstance(modulation, bool)
        or modulation <= 0
    ):
        raise ValueError("The Xiaomi emitter requires a positive carrier frequency")
    frequency_word = round(1_000_000 / (modulation * PRONTO_TICK_US))
    if not 1 <= frequency_word <= 0xFFFF:
        raise ValueError("Carrier frequency cannot be represented as Pronto")
    values = list(timings)
    if not values or len(values) > 65536:
        raise ValueError("Empty or oversized IR signal")
    for index, value in enumerate(values):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value == 0
            or (value > 0) != (index % 2 == 0)
        ):
            raise ValueError(
                "IR timings must alternate positive pulses and negative spaces"
            )
    # A final pulse needs a space to form a Pronto pair. Xiaomi's native
    # edge_count excludes that last space, so this adds no transmitted pulse.
    if len(values) % 2:
        values.append(-1000)
    period = frequency_word * PRONTO_TICK_US
    cycles = [max(1, math.floor(abs(value) / period + 0.5)) for value in values]
    if any(value > 0xFFFF for value in cycles):
        raise ValueError("IR duration is too long for Pronto encoding")
    if len(set(cycles)) > 16:
        raise ValueError("This signal exceeds Xiaomi's sixteen distinct duration limit")
    words = [0, frequency_word, len(values) // 2, 0, *cycles]
    return " ".join(f"{word:04X}" for word in words)


async def async_setup_entry(hass, entry, async_add_entities):
    """Register one emitter on the existing physical Xiaomi device."""
    async_add_entities([XiaomiInfraredEmitter(entry)])


class XiaomiInfraredEmitter(CoordinatorEntity, InfraredEmitterEntity):
    """Hardware emitter selected by standard infrared consumer integrations."""

    _attr_has_entity_name = True
    _attr_translation_key = "infrared_emitter"

    def __init__(self, entry):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.unique_id}_emitter"
        info = self.coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="Xiaomi",
            model=MODEL,
            sw_version=info.firmware_version,
            hw_version=info.hardware_version,
        )

    async def async_send_command(self, command):
        """Send the command's complete waveform exactly once through miIO."""
        try:
            pronto = timings_to_pronto(command.get_raw_timings(), command.modulation)
            code, frequency = ChuangmiIr.pronto_to_raw(pronto, repeats=0)
        except (ValueError, DeviceException) as err:
            raise ServiceValidationError(str(err)) from err
        async with self.coordinator.lock:
            try:
                response = await self.coordinator.async_io(
                    self.coordinator.device.play_raw, code, frequency
                )
                check_ack(response)
            except (DeviceException, OSError, ValueError) as err:
                raise HomeAssistantError(
                    "IR transmission failed or was not acknowledged. "
                    "It may have been sent; it was not retried."
                ) from err
