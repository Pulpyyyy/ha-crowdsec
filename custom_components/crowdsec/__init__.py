from __future__ import annotations

import importlib
import logging
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

_LOGGER = logging.getLogger(__name__)

CARD_URL_BASE = "/crowdsec-card"
CARD_URL = f"{CARD_URL_BASE}/crowdsec-card.js"


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
    versioned_url = f"{CARD_URL}?v={integration.version}"

    lovelace = hass.data.get("lovelace")
    if isinstance(lovelace, dict):  # pre-2024 shape, kept for safety
        mode = lovelace.get("mode")
        resources = lovelace.get("resources")
    else:
        mode = getattr(lovelace, "mode", None)
        resources = getattr(lovelace, "resources", None)

    if mode == "storage" and resources is not None:
        # Storage mode: create (or refresh) the entry in the Lovelace
        # resources so the card shows up in Settings > Dashboards > Resources.
        try:
            await resources.async_get_info()  # ensure the store is loaded
            existing = next(
                (r for r in resources.async_items() if r["url"].startswith(CARD_URL)),
                None,
            )
            if existing is None:
                await resources.async_create_item(
                    {"res_type": "module", "url": versioned_url}
                )
            elif existing["url"] != versioned_url:
                await resources.async_update_item(
                    existing["id"], {"url": versioned_url}
                )
            return
        except Exception:  # noqa: BLE001 - never break setup over the card
            _LOGGER.exception(
                "Could not register the card in the Lovelace resources; "
                "falling back to direct injection"
            )

    # YAML-mode dashboards (or failure above): inject the module directly.
    add_extra_js_url(hass, versioned_url)


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