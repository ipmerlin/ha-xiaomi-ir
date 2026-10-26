"""Add or reconfigure a Xiaomi IR using its local IP and token."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_TOKEN
from homeassistant.helpers import selector
from miio import DeviceException

from .const import DEFAULT_NAME, DOMAIN, MODEL
from .protocol import create_device, mac_id, validate_token


class XiaomiIRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validate the local connection before creating an entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle initial setup."""
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Change the IP or token while retaining the device and learned codes."""
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step_id, user_input):
        errors = {}
        entry = self._get_reconfigure_entry() if step_id == "reconfigure" else None
        if user_input is not None:
            data = dict(user_input)
            try:
                data[CONF_TOKEN] = validate_token(data[CONF_TOKEN])
            except ValueError:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                data[CONF_HOST] = data[CONF_HOST].strip()
                data[CONF_NAME] = data[CONF_NAME].strip() or DEFAULT_NAME
                device = create_device(data[CONF_HOST], data[CONF_TOKEN])
                try:
                    info = await self.hass.async_add_executor_job(device.info)
                    unique_id = mac_id(info.mac_address)
                    if info.model != MODEL:
                        errors["base"] = "unsupported_model"
                except (DeviceException, OSError, ValueError):
                    errors["base"] = "cannot_connect"
                if not errors:
                    await self.async_set_unique_id(unique_id)
                    if entry:
                        self._abort_if_unique_id_mismatch()
                        return self.async_update_reload_and_abort(
                            entry, data=data, title=data[CONF_NAME]
                        )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=data[CONF_NAME], data=data)
        defaults = entry.data if entry else {}
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
                    ): str,
                    vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                    vol.Required(CONF_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )
