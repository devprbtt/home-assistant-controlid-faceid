"""Sensors for Control iD FaceID."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_RUNTIME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    async_add_entities(
        [
            ControlIDLastAccessSensor(runtime),
            ControlIDLastAccessUserSensor(runtime),
            ControlIDRegisteredUsersCountSensor(runtime),
        ]
    )


class ControlIDLastAccessSensor(SensorEntity):
    """Last access sensor built from DAO webhook data."""

    _attr_has_entity_name = True
    _attr_name = "Last Access"
    _attr_icon = "mdi:account-key"

    def __init__(self, runtime) -> None:
        """Initialize the sensor."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_access"

    @property
    def native_value(self) -> str | None:
        """Return the mapped event name."""
        return self._runtime.state.last_access_event_name

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose parsed DAO data."""
        user_id = self._runtime.state.last_access_user_id
        friendly_name = self._runtime.user_map.get(user_id) if user_id else None
        user_display = friendly_name or user_id
        return {
            "user_id": user_id,
            "user_name": friendly_name,
            "user_display": user_display,
            "event_code": self._runtime.state.last_access_event_code,
            "event": self._runtime.state.last_access_event_name,
            "change_type": self._runtime.state.last_access_type,
            "log_id": self._runtime.state.last_access_log_id,
            "device_id": self._runtime.state.device_id,
            "timestamp": self._runtime.state.last_access_timestamp,
            "configured_user_map": self._runtime.user_map,
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


class ControlIDLastAccessUserSensor(SensorEntity):
    """User sensor built from DAO webhook data."""

    _attr_has_entity_name = True
    _attr_name = "Last Access User"
    _attr_icon = "mdi:account"

    def __init__(self, runtime) -> None:
        """Initialize the sensor."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_access_user"

    @property
    def native_value(self) -> str | None:
        """Return the last authorized friendly user name or raw user ID."""
        user_id = self._runtime.state.last_authorized_user_id
        if user_id is None:
            return None
        return self._runtime.user_map.get(user_id, user_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the underlying user data."""
        user_id = self._runtime.state.last_authorized_user_id
        return {
            "user_id": user_id,
            "user_name": self._runtime.user_map.get(user_id) if user_id else None,
            "event": self._runtime.state.last_authorized_event_name,
            "event_code": self._runtime.state.last_authorized_event_code,
            "timestamp": self._runtime.state.last_authorized_timestamp,
            "log_id": self._runtime.state.last_authorized_log_id,
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


class ControlIDRegisteredUsersCountSensor(SensorEntity):
    """Sensor for the total number of registered users on the device."""

    _attr_has_entity_name = True
    _attr_name = "Registered Users"
    _attr_icon = "mdi:account-group"

    def __init__(self, runtime) -> None:
        """Initialize the sensor."""
        self._runtime = runtime
        self._remove_listener = None
        self._attr_unique_id = f"{runtime.entry.entry_id}_registered_users"

    @property
    def native_value(self) -> int | None:
        """Return the number of users loaded from the device."""
        return self._runtime.state.registered_users_count

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose sync metadata."""
        return {
            "mapped_users": len(self._runtime.user_map),
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
