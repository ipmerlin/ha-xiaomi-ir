"""UI-configured, local Xiaomi IR integration."""

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import XiaomiIRCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.REMOTE]


async def async_setup_entry(hass, entry):
    """Set up the physical device and its standard remote entity."""
    coordinator = XiaomiIRCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    """Unload the entity; CoordinatorEntity removes its polling subscription."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
