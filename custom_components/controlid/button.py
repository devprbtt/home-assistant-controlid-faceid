"""Button platform for Control iD."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_RUNTIME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    async_add_entities([ControlIDOpenGateButton(runtime), ControlIDSyncUsersButton(runtime)])


class ControlIDOpenGateButton(ButtonEntity):
    """Button that opens the gate through SecBox."""

    _attr_has_entity_name = True
    _attr_name = "Open Gate"
    _attr_icon = "mdi:gate-open"

    def __init__(self, runtime) -> None:
        """Initialize the entity."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_open_gate"
        self._attr_device_info = runtime.device_info

    @property
    def available(self) -> bool:
        """Return whether the integration currently has device connectivity."""
        return self._runtime.state.available

    async def async_press(self) -> None:
        """Open the gate."""
        await self._runtime.client.async_open_gate(self._runtime.secbox_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self._remove_listener = self._runtime.async_add_listener(self._handle_runtime_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from runtime updates."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _handle_runtime_update(self) -> None:
        self.async_write_ha_state()


class ControlIDSyncUsersButton(ButtonEntity):
    """Button that imports users from the device."""

    _attr_has_entity_name = True
    _attr_name = "Sync Users"
    _attr_icon = "mdi:account-sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime) -> None:
        """Initialize the entity."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_sync_users"
        self._attr_device_info = runtime.device_info

    @property
    def available(self) -> bool:
        """Return whether the integration currently has device connectivity."""
        return self._runtime.state.available

    async def async_press(self) -> None:
        """Import users from the device into the user map."""
        await self._runtime.async_sync_users()

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self._remove_listener = self._runtime.async_add_listener(self._handle_runtime_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from runtime updates."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _handle_runtime_update(self) -> None:
        self.async_write_ha_state()
