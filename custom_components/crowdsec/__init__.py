from __future__ import annotations

import importlib
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

# Import your coordinator and API client
from .sensor import CrowdSecCoordinator
from .api import CrowdSecApiClient 

from .const import DOMAIN, CONF_SCHEME

PLATFORMS = ["sensor"]

CARD_URL_BASE = "/crowdsec-card"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and register it as a frontend resource."""
    if hass.data[DOMAIN].get("card_registered"):
        return
    hass.data[DOMAIN]["card_registered"] = True

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "www"), True)]
    )
    # Version query busts the browser cache when the integration is updated.
    integration = await async_get_integration(hass, DOMAIN)
    add_extra_js_url(hass, f"{CARD_URL_BASE}/crowdsec-card.js?v={integration.version}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CrowdSec from a config entry."""
    # Create the device in the registry FIRST.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="CrowdSec",
        model="LAPI",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Create the API client and coordinator.
    session = async_get_clientsession(hass)
    api_client = CrowdSecApiClient(
        host=entry.data["host"],
        port=entry.data["port"],
        api_key=entry.data["api_key"],
        scheme=entry.data[CONF_SCHEME],
        unique_id=entry.unique_id,
        session=session,
    )
    coordinator = CrowdSecCoordinator(hass, api_client, entry)

    # Store the coordinator in hass.data for platforms to access.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Make the bundled Lovelace card available (no manual resource needed).
    await _async_register_card(hass)

    # Pre-load the device_trigger platform to avoid blocking import.
    await hass.async_add_executor_job(
        lambda: importlib.import_module(".device_trigger", package=__package__)
    )

    # Forward the setup to platforms (e.g., sensor).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    # This is called when the user saves the options form.
    # The easiest way to apply the changes is to reload the integration.
    await hass.config_entries.async_reload(entry.entry_id)