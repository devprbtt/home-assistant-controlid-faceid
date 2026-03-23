"""Binary sensors for Control iD."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_RUNTIME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    async_add_entities([ControlIDDoorStateBinarySensor(runtime), ControlIDDeviceOnlineBinarySensor(runtime)])


class ControlIDDoorStateBinarySensor(BinarySensorEntity):
    """Door state pushed by the secbox webhook."""

    _attr_has_entity_name = True
    _attr_name = "Door State"
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, runtime) -> None:
        """Initialize the sensor."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_door_state"
        self._attr_device_info = runtime.device_info

    @property
    def is_on(self) -> bool | None:
        """Return true when the door is open."""
        return self._runtime.state.door_open

    @property
    def available(self) -> bool:
        """Return whether the integration currently has device connectivity."""
        return self._runtime.state.available

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the last secbox payload details."""
        return {
            "door_id": self._runtime.state.door_id,
            "device_id": self._runtime.state.device_id,
            "access_event_id": self._runtime.state.access_event_id,
            "updated_at": self._runtime.state.door_updated_at,
        }

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


class ControlIDDeviceOnlineBinarySensor(BinarySensorEntity):
    """Connectivity sensor for the device."""

    _attr_has_entity_name = True
    _attr_name = "Device Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime) -> None:
        """Initialize the sensor."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_device_online"
        self._attr_device_info = runtime.device_info

    @property
    def is_on(self) -> bool:
        """Return true when the device is online."""
        return self._runtime.state.available

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose contact timestamps."""
        return {
            "last_successful_contact": self._runtime.state.last_successful_contact,
            "last_failed_contact": self._runtime.state.last_failed_contact,
            "last_webhook_received": self._runtime.state.last_webhook_received,
            "last_watchdog_refresh": self._runtime.state.last_watchdog_refresh,
        }

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
