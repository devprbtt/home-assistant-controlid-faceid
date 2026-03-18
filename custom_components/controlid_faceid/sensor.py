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
    async_add_entities([ControlIDLastAccessSensor(runtime)])


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
        return {
            "user_id": user_id,
            "user_name": friendly_name,
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
