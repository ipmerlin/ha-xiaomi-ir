"""Standard remote actions backed by a local Xiaomi IR controller."""

import asyncio
import secrets
from copy import deepcopy

from homeassistant.components import persistent_notification
from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from miio import DeviceException

from .commands import (
    command_names,
    delete_commands,
    device_name,
    resolve_commands,
    validate_library,
)
from .const import DOMAIN, MODEL, STORAGE_VERSION
from .protocol import check_ack, read_code, transmission_error_message


async def async_setup_entry(hass, entry, async_add_entities):
    """Load the command library before exposing the entity."""
    remote = XiaomiIRRemote(entry)
    remote.library = validate_library(await remote.store.async_load())
    async_add_entities([remote])


class XiaomiIRRemote(CoordinatorEntity, RemoteEntity, RestoreEntity):
    """A named-command remote, with software enable/disable like Broadlink."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND
    )

    def __init__(self, entry):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = entry.unique_id
        self._attr_is_on = True
        self.library = {}
        self.store = Store(
            self.coordinator.hass,
            STORAGE_VERSION,
            f"{DOMAIN}_{entry.unique_id}_commands",
        )
        info = self.coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="Xiaomi",
            model=MODEL,
            sw_version=info.firmware_version,
            hw_version=info.hardware_version,
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        self._attr_is_on = state is None or state.state != "off"

    async def async_turn_on(self, **kwargs):
        """Enable sending; this is not the power state of a controlled appliance."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable sending without claiming to power off the physical IR hub."""
        self._attr_is_on = False
        self.async_write_ha_state()

    def _require_enabled(self):
        if not self._attr_is_on:
            raise ServiceValidationError("The remote is disabled; turn it on first")

    async def _save(self, updated):
        await self.store.async_save(updated)
        self.library = updated

    async def async_send_command(self, command, **kwargs):
        """Send names or Xiaomi raw/Pronto codes with explicit repeats."""
        if kwargs.get("hold_secs", 0):
            raise ServiceValidationError("hold_secs is not supported; use num_repeats")
        async with self.coordinator.lock:
            self._require_enabled()
            try:
                codes = resolve_commands(self.library, command, kwargs.get("device"))
                repeats = int(kwargs.get("num_repeats", 1))
                delay = float(kwargs.get("delay_secs", 0.4))
                if repeats < 1 or delay < 0:
                    raise ValueError("Invalid repeats or delay")
                # Validate the entire sequence, including Pronto conversion, first.
                for code in codes:
                    self._validate_code(code)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            sent = False
            for _ in range(repeats):
                for code in codes:
                    if sent:
                        await asyncio.sleep(delay)
                    try:
                        response = await self.coordinator.async_io(
                            self.coordinator.device.play, code
                        )
                        check_ack(response, allow_zero=True)
                    except (DeviceException, OSError, ValueError) as err:
                        raise HomeAssistantError(
                            transmission_error_message(err)
                        ) from err
                    sent = True

    def _validate_code(self, code):
        import base64
        import binascii

        kind, payload, *args = code.split(":")
        if not payload or len(args) > 2:
            raise ValueError("Invalid IR code")
        try:
            numbers = [int(arg) for arg in args]
            if kind == "raw":
                if not base64.b64decode(payload, validate=True):
                    raise ValueError("Empty IR code")
                if numbers and numbers[0] <= 0:
                    raise ValueError("Invalid IR frequency")
            else:
                self.coordinator.device.pronto_to_raw(
                    payload, numbers[0] if numbers else 1
                )
        except (ValueError, binascii.Error, DeviceException) as err:
            raise ValueError("Invalid IR code or parameters") from err

    async def async_learn_command(self, **kwargs):
        """Capture codes and save each successfully learned name immediately."""
        try:
            subdevice = device_name(kwargs.get("device"))
            names = command_names(kwargs.get("command"))
            if any(name.startswith(("raw:", "pronto:")) for name in names):
                raise ValueError("Command names cannot start with raw: or pronto:")
            if kwargs.get("command_type", "ir") != "ir":
                raise ValueError("This device supports IR learning only")
            if kwargs.get("alternative", False):
                raise ValueError("Alternative codes are not supported")
            timeout = float(kwargs.get("timeout", 30))
            if not 1 <= timeout <= 300:
                raise ValueError("Learning timeout must be between 1 and 300 seconds")
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        async with self.coordinator.lock:
            self._require_enabled()
            for name in names:
                code = await self._learn_one(subdevice, name, timeout)
                updated = deepcopy(self.library)
                updated.setdefault(subdevice, {})[name] = code
                await self._save(updated)

    async def _learn_one(self, subdevice, name, timeout):
        device = self.coordinator.device
        notification_id = f"{DOMAIN}_learn_{self.unique_id}"
        try:
            # Verify an unused slot, so a previous capture can never be mistaken
            # for a newly pressed button after a restart or failed learning call.
            for _ in range(5):
                slot = secrets.randbelow(1000000) + 1
                if await self.coordinator.async_io(read_code, device, slot) is None:
                    break
            else:
                raise ValueError("Cannot find an empty learning slot")
            check_ack(
                await self.coordinator.async_io(device.learn, slot), allow_zero=True
            )
            persistent_notification.async_create(
                self.hass,
                f"Нажмите кнопку «{name}» на пульте «{subdevice}», направив его на Xiaomi IR.",
                title="Обучение Xiaomi IR",
                notification_id=notification_id,
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1)
                code = await self.coordinator.async_io(read_code, device, slot)
                if code:
                    return code
            raise TimeoutError("No IR code received before the learning timeout")
        except (DeviceException, OSError, ValueError) as err:
            raise HomeAssistantError(
                "Не удалось обучить команду: проверьте связь и повторите обучение."
            ) from err
        finally:
            persistent_notification.async_dismiss(self.hass, notification_id)

    async def async_delete_command(self, **kwargs):
        """Delete learned names from Home Assistant storage."""
        async with self.coordinator.lock:
            try:
                updated = delete_commands(
                    self.library, kwargs.get("command"), kwargs.get("device")
                )
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            await self._save(updated)
