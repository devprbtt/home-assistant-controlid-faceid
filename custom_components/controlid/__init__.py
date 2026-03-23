"""Control iD FaceID custom integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientResponse, ClientSession, CookieJar, ContentTypeError, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

DOMAIN = "controlid"

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.BINARY_SENSOR, Platform.SENSOR]

CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_PATH = "webhook_path"
CONF_SECBOX_ID = "secbox_id"
CONF_USER_MAP = "user_map"

DATA_HTTP_VIEW_REGISTERED = "http_view_registered"
DATA_WEBHOOKS = "webhooks"
DATA_RUNTIME = "runtime"

DEFAULT_REQUEST_TIMEOUT_MS = "5000"
DEFAULT_SECBOX_ID = 65793
HEALTHCHECK_INTERVAL = timedelta(seconds=60)
WEBHOOK_WATCHDOG_INTERVAL = timedelta(minutes=15)
WEBHOOK_STALE_AFTER = timedelta(hours=6)
EVENT_MAP = {
    "7": "Authorized",
    "11": "Door Opened",
}

_LOGGER = logging.getLogger(__name__)


class ControlIDError(Exception):
    """Base integration error."""


class ControlIDAuthError(ControlIDError):
    """Authentication failed."""


class ControlIDSessionExpiredError(ControlIDError):
    """Session token expired or rejected."""


def _utc_from_timestamp(value: Any) -> datetime | None:
    """Convert a unix timestamp into UTC datetime."""
    if value in (None, ""):
        return None

    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@dataclass
class ControlIDState:
    """In-memory state mirrored from device webhooks."""

    door_open: bool | None = None
    door_id: int | str | None = None
    device_id: int | str | None = None
    access_event_id: int | str | None = None
    door_updated_at: datetime | None = None
    last_access_user_id: str | None = None
    last_access_event_code: str | None = None
    last_access_event_name: str | None = None
    last_access_type: str | None = None
    last_access_timestamp: datetime | None = None
    last_access_log_id: str | None = None
    last_authorized_user_id: str | None = None
    last_authorized_event_code: str | None = None
    last_authorized_event_name: str | None = None
    last_authorized_timestamp: datetime | None = None
    last_authorized_log_id: str | None = None
    registered_users_count: int | None = None
    available: bool = False
    last_successful_contact: datetime | None = None
    last_failed_contact: datetime | None = None
    last_webhook_received: datetime | None = None
    last_watchdog_refresh: datetime | None = None


@dataclass
class ControlIDRuntime:
    """Runtime data for a config entry."""

    entry: ConfigEntry
    hass: HomeAssistant
    client: "ControlIDClient"
    webhook_id: str
    webhook_path: str
    state: ControlIDState = field(default_factory=ControlIDState)
    _listeners: list[CALLBACK_TYPE] = field(default_factory=list)
    _healthcheck_unsub: CALLBACK_TYPE | None = None
    _watchdog_unsub: CALLBACK_TYPE | None = None
    base_url: str | None = None

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register a listener for push updates."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def async_notify(self) -> None:
        """Push updated state to entities."""
        for listener in tuple(self._listeners):
            listener()

    @callback
    def async_mark_available(self) -> None:
        """Mark the device as available."""
        self.state.available = True
        self.state.last_successful_contact = datetime.now(timezone.utc)

    @callback
    def async_mark_unavailable(self) -> None:
        """Mark the device as unavailable."""
        self.state.available = False
        self.state.last_failed_contact = datetime.now(timezone.utc)

    @callback
    def async_mark_webhook_received(self) -> None:
        """Record the time of the latest webhook."""
        self.state.last_webhook_received = datetime.now(timezone.utc)

    @callback
    def async_handle_door_state(self, payload: dict[str, Any]) -> None:
        """Store secbox or door webhook payload."""
        self.async_mark_webhook_received()
        self.async_mark_available()
        door_state = payload.get("secbox") or payload.get("door") or {}
        if "open" in door_state:
            self.state.door_open = bool(door_state["open"])
        self.state.door_id = door_state.get("id")
        self.state.device_id = payload.get("device_id", self.state.device_id)
        self.state.access_event_id = payload.get("access_event_id")
        self.state.door_updated_at = _utc_from_timestamp(payload.get("time"))
        self.async_notify()

    @callback
    def async_handle_dao(self, payload: dict[str, Any]) -> None:
        """Store DAO webhook payload."""
        self.async_mark_webhook_received()
        self.async_mark_available()
        changes = payload.get("object_changes") or []

        for change in changes:
            if change.get("object") != "access_logs":
                continue

            values = change.get("values") or {}
            event_code = str(values.get("event")) if values.get("event") is not None else None

            self.state.last_access_user_id = (
                str(values.get("user_id")) if values.get("user_id") is not None else None
            )
            self.state.last_access_event_code = event_code
            self.state.last_access_event_name = (
                EVENT_MAP.get(event_code, event_code) if event_code is not None else None
            )
            self.state.last_access_type = change.get("type")
            self.state.last_access_timestamp = _utc_from_timestamp(values.get("time"))
            self.state.last_access_log_id = (
                str(values.get("id")) if values.get("id") is not None else None
            )
            self.state.device_id = payload.get("device_id", self.state.device_id)
            if event_code == "7":
                self.state.last_authorized_user_id = self.state.last_access_user_id
                self.state.last_authorized_event_code = event_code
                self.state.last_authorized_event_name = self.state.last_access_event_name
                self.state.last_authorized_timestamp = self.state.last_access_timestamp
                self.state.last_authorized_log_id = self.state.last_access_log_id
            self.async_notify()
            return

    @property
    def user_map(self) -> dict[str, str]:
        """Return configured user ID to friendly-name mappings."""
        user_map = self.entry.options.get(CONF_USER_MAP, {})
        return user_map if isinstance(user_map, dict) else {}

    @property
    def secbox_id(self) -> int:
        """Return configured secbox ID."""
        return int(self.entry.data.get(CONF_SECBOX_ID, DEFAULT_SECBOX_ID))

    @property
    def device_info(self) -> DeviceInfo:
        """Return device metadata for Home Assistant entity grouping."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.unique_id or self.entry.entry_id)},
            manufacturer="Control iD",
            model="FaceID / Access",
            name=self.entry.title,
            configuration_url=f"http://{self.client.host}",
        )

    async def async_initialize_state(self) -> None:
        """Populate current state from the device on startup."""
        startup_ok = False
        try:
            users = await self.client.async_load_users()
            self.state.registered_users_count = len(users)
            startup_ok = True
        except ControlIDError as err:
            _LOGGER.debug("Unable to load users during startup for %s: %s", self.client.host, err)

        try:
            access_log = await self.client.async_load_latest_access_log()
        except ControlIDError as err:
            _LOGGER.debug(
                "Unable to load latest access log during startup for %s: %s",
                self.client.host,
                err,
            )
        else:
            self.async_handle_dao(
                {
                    "object_changes": [
                        {
                            "object": "access_logs",
                            "type": "startup",
                            "values": access_log,
                        }
                    ]
                }
            )
            startup_ok = True

        try:
            authorized_log = await self.client.async_load_latest_authorized_access_log()
        except ControlIDError as err:
            _LOGGER.debug(
                "Unable to load latest authorized access log during startup for %s: %s",
                self.client.host,
                err,
            )
        else:
            self.state.last_authorized_user_id = (
                str(authorized_log.get("user_id"))
                if authorized_log.get("user_id") is not None
                else None
            )
            self.state.last_authorized_event_code = (
                str(authorized_log.get("event"))
                if authorized_log.get("event") is not None
                else None
            )
            self.state.last_authorized_event_name = (
                EVENT_MAP.get(self.state.last_authorized_event_code, self.state.last_authorized_event_code)
                if self.state.last_authorized_event_code is not None
                else None
            )
            self.state.last_authorized_timestamp = _utc_from_timestamp(authorized_log.get("time"))
            self.state.last_authorized_log_id = (
                str(authorized_log.get("id"))
                if authorized_log.get("id") is not None
                else None
            )
            startup_ok = True

        try:
            door_state = await self.client.async_get_current_door_state(self.secbox_id)
        except ControlIDError as err:
            _LOGGER.debug(
                "Unable to load direct door state during startup for %s: %s",
                self.client.host,
                err,
            )
        else:
            if door_state is not None:
                self.async_handle_door_state(
                    {
                        "secbox": {
                            "id": door_state.get("id"),
                            "open": door_state.get("open"),
                        },
                    }
                )
                self.async_notify()
                self.async_mark_available()
                return

        try:
            door_event = await self.client.async_load_latest_door_event()
        except ControlIDError as err:
            _LOGGER.debug(
                "Unable to load latest door event during startup for %s: %s",
                self.client.host,
                err,
            )
        else:
            if door_event is not None:
                self.async_handle_door_state(
                    {
                        "secbox": {
                            "id": door_event.get("identification"),
                            "open": str(door_event.get("type")).upper() == "OPEN",
                        },
                        "time": door_event.get("timestamp"),
                    }
                )
                startup_ok = True

        if startup_ok:
            self.async_mark_available()
        else:
            self.async_mark_unavailable()
        self.async_notify()

    async def async_sync_users(self) -> int:
        """Import users from the device into the friendly-name map."""
        users = await self.client.async_load_users()
        self.async_mark_available()
        merged_map = dict(self.user_map)
        self.state.registered_users_count = len(users)

        for user in users:
            user_id = user.get("id")
            user_name = user.get("name")
            if user_id in (None, "") or not user_name:
                continue
            merged_map[str(user_id)] = str(user_name)

        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                CONF_USER_MAP: merged_map,
            },
        )
        self.async_notify()
        return len(merged_map)

    async def async_check_connection(self, now: datetime | None = None) -> None:
        """Periodically verify that the device is reachable."""
        try:
            await self.client.async_login()
        except ControlIDError as err:
            _LOGGER.debug("Connectivity check failed for %s: %s", self.client.host, err)
            was_available = self.state.available
            self.async_mark_unavailable()
            if was_available:
                self.async_notify()
            return

        was_available = self.state.available
        self.async_mark_available()
        if not was_available:
            self.async_notify()

    async def async_refresh_core_state(self) -> None:
        """Refresh the most important runtime state from the device."""
        refreshed = False

        try:
            users = await self.client.async_load_users()
        except ControlIDError as err:
            _LOGGER.debug("Unable to refresh users for %s: %s", self.client.host, err)
        else:
            self.state.registered_users_count = len(users)
            refreshed = True

        try:
            access_log = await self.client.async_load_latest_access_log()
        except ControlIDError as err:
            _LOGGER.debug("Unable to refresh latest access log for %s: %s", self.client.host, err)
        else:
            self.async_handle_dao(
                {
                    "object_changes": [
                        {
                            "object": "access_logs",
                            "type": "watchdog",
                            "values": access_log,
                        }
                    ]
                }
            )
            refreshed = True

        try:
            authorized_log = await self.client.async_load_latest_authorized_access_log()
        except ControlIDError as err:
            _LOGGER.debug(
                "Unable to refresh latest authorized access log for %s: %s",
                self.client.host,
                err,
            )
        else:
            self.state.last_authorized_user_id = (
                str(authorized_log.get("user_id"))
                if authorized_log.get("user_id") is not None
                else None
            )
            self.state.last_authorized_event_code = (
                str(authorized_log.get("event"))
                if authorized_log.get("event") is not None
                else None
            )
            self.state.last_authorized_event_name = (
                EVENT_MAP.get(
                    self.state.last_authorized_event_code,
                    self.state.last_authorized_event_code,
                )
                if self.state.last_authorized_event_code is not None
                else None
            )
            self.state.last_authorized_timestamp = _utc_from_timestamp(authorized_log.get("time"))
            self.state.last_authorized_log_id = (
                str(authorized_log.get("id"))
                if authorized_log.get("id") is not None
                else None
            )
            refreshed = True

        try:
            door_state = await self.client.async_get_current_door_state(self.secbox_id)
        except ControlIDError as err:
            _LOGGER.debug("Unable to refresh direct door state for %s: %s", self.client.host, err)
        else:
            if door_state is not None:
                self.async_handle_door_state(
                    {
                        "secbox": {
                            "id": door_state.get("id"),
                            "open": door_state.get("open"),
                        },
                    }
                )
                refreshed = True

        if refreshed:
            self.async_mark_available()
            self.state.last_watchdog_refresh = datetime.now(timezone.utc)
            self.async_notify()

    async def async_watchdog_webhooks(self, now: datetime | None = None) -> None:
        """Detect stale webhook delivery and self-heal when possible."""
        last_webhook = self.state.last_webhook_received
        if last_webhook is not None and datetime.now(timezone.utc) - last_webhook < WEBHOOK_STALE_AFTER:
            return

        _LOGGER.debug(
            "Webhook watchdog refresh triggered for %s; last webhook=%s",
            self.client.host,
            last_webhook,
        )
        await self.async_refresh_core_state()

        if self.base_url is not None:
            try:
                await self.client.async_configure_monitor(self.base_url, self.webhook_path)
            except ControlIDError as err:
                _LOGGER.debug(
                    "Unable to reconfigure monitor during watchdog refresh for %s: %s",
                    self.client.host,
                    err,
                )

    @callback
    def async_start_healthcheck(self) -> None:
        """Start periodic connectivity checks."""
        if self._healthcheck_unsub is not None:
            return

        self._healthcheck_unsub = async_track_time_interval(
            self.hass,
            self.async_check_connection,
            HEALTHCHECK_INTERVAL,
        )

    @callback
    def async_start_watchdog(self) -> None:
        """Start webhook freshness watchdog checks."""
        if self._watchdog_unsub is not None:
            return

        self._watchdog_unsub = async_track_time_interval(
            self.hass,
            self.async_watchdog_webhooks,
            WEBHOOK_WATCHDOG_INTERVAL,
        )

    @callback
    def async_stop_healthcheck(self) -> None:
        """Stop periodic connectivity checks."""
        if self._healthcheck_unsub is not None:
            self._healthcheck_unsub()
            self._healthcheck_unsub = None

    @callback
    def async_stop_watchdog(self) -> None:
        """Stop webhook watchdog checks."""
        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None


class ControlIDClient:
    """Minimal Control iD HTTP client."""

    def __init__(self, host: str, username: str, password: str, session: ClientSession) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._session = session
        self._session_token: str | None = None

    @property
    def host(self) -> str:
        """Return configured host."""
        return self._host

    async def async_login(self) -> str:
        """Authenticate and return a session token."""
        url = f"http://{self._host}/login.fcgi"
        payload = {"login": self._username, "password": self._password}

        try:
            response = await self._session.post(url, data=payload)
            response.raise_for_status()
            data = await response.json(content_type=None)
        except (ClientError, ValueError) as err:
            raise ControlIDAuthError(f"Unable to authenticate with device {self._host}") from err

        token = data.get("session")
        if not token:
            raise ControlIDAuthError(f"Device {self._host} did not return a session token")

        self._session_token = str(token)
        return self._session_token

    async def _async_ensure_login(self) -> str:
        """Return a cached session token or log in."""
        if self._session_token is None:
            return await self.async_login()
        return self._session_token

    async def _async_parse_response(self, response: ClientResponse) -> Any:
        """Parse a response body as JSON when possible, otherwise as text."""
        try:
            return await response.json(content_type=None)
        except (ValueError, ContentTypeError):
            return await response.text()

    async def _async_raise_for_session_error(self, response: ClientResponse) -> Any:
        """Inspect responses for session expiry errors and return parsed content."""
        parsed = await self._async_parse_response(response)

        if response.status == 401:
            raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")

        if response.status >= 400:
            body = parsed if isinstance(parsed, str) else str(parsed)
            if "session" in body.lower():
                raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")
            raise ControlIDError(f"Device {self._host} returned HTTP {response.status}: {body}")

        if isinstance(parsed, dict):
            message = str(parsed.get("error") or parsed.get("message") or "")
            if "session" in message.lower():
                raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")
        elif isinstance(parsed, str) and "session" in parsed.lower():
            raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")

        return parsed

    async def _async_post_with_relogin(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """POST to an authenticated endpoint and retry once on session expiry."""
        last_error: Exception | None = None

        for attempt in range(2):
            session_token = await self._async_ensure_login()
            url = f"http://{self._host}/{path}"

            try:
                response = await self._session.post(
                    url,
                    params={"session": session_token},
                    data=data,
                    json=json,
                    headers={"Cookie": f"session={session_token}"},
                )
                return await self._async_raise_for_session_error(response)
            except ControlIDSessionExpiredError as err:
                last_error = err
                self._session_token = None
                if attempt == 0:
                    continue
                raise ControlIDError(f"Session retry failed for device {self._host}") from err
            except ClientError as err:
                last_error = err
                raise ControlIDError(f"Unable to reach device {self._host}") from err

        if last_error is not None:
            raise ControlIDError(f"Unable to complete request for device {self._host}") from last_error

    async def async_open_gate(self, secbox_id: int) -> None:
        """Open the gate using the SecBox action."""
        payload = {
            "actions": [
                {
                    "action": "sec_box",
                    "parameters": f"id={secbox_id}, reason=3",
                }
            ]
        }

        try:
            await self._async_post_with_relogin("execute_actions.fcgi", json=payload)
        except ControlIDError as err:
            raise ControlIDError(f"Unable to trigger gate opening on {self._host}") from err

    async def async_load_users(self) -> list[dict[str, Any]]:
        """Load registered users from the device."""
        payload = {"object": "users"}

        try:
            data = await self._async_post_with_relogin("load_objects.fcgi", json=payload)
        except ControlIDError as err:
            raise ControlIDError(f"Unable to load users from device {self._host}") from err

        if not isinstance(data, dict):
            raise ControlIDError(f"Unexpected user list response from device {self._host}: {data}")

        users = data.get("users")
        if not isinstance(users, list):
            raise ControlIDError(f"Device {self._host} did not return a users list")

        return [user for user in users if isinstance(user, dict)]

    async def async_load_latest_access_log(self) -> dict[str, Any]:
        """Load the most recent access log from the device."""
        payload = {
            "object": "access_logs",
            "limit": 1,
            "order": ["id", "descending"],
        }

        try:
            data = await self._async_post_with_relogin("load_objects.fcgi", json=payload)
        except ControlIDError as err:
            raise ControlIDError(f"Unable to load access logs from device {self._host}") from err

        if not isinstance(data, dict):
            raise ControlIDError(f"Unexpected access log response from device {self._host}: {data}")

        access_logs = data.get("access_logs")
        if not isinstance(access_logs, list):
            raise ControlIDError(f"Device {self._host} did not return an access logs list")
        if not access_logs:
            raise ControlIDError(f"Device {self._host} returned no access logs")

        latest_log = access_logs[0]
        if not isinstance(latest_log, dict):
            raise ControlIDError(f"Device {self._host} returned an invalid access log entry")

        return latest_log

    async def async_load_latest_authorized_access_log(self) -> dict[str, Any]:
        """Load the most recent authorized access log from the device."""
        payload = {
            "object": "access_logs",
            "limit": 1,
            "order": ["id", "descending"],
            "where": {
                "access_logs": {
                    "event": 7,
                }
            },
        }

        try:
            data = await self._async_post_with_relogin("load_objects.fcgi", json=payload)
        except ControlIDError as err:
            raise ControlIDError(
                f"Unable to load authorized access logs from device {self._host}"
            ) from err

        if not isinstance(data, dict):
            raise ControlIDError(
                f"Unexpected authorized access log response from device {self._host}: {data}"
            )

        access_logs = data.get("access_logs")
        if not isinstance(access_logs, list):
            raise ControlIDError(f"Device {self._host} did not return an access logs list")
        if not access_logs:
            raise ControlIDError(f"Device {self._host} returned no authorized access logs")

        latest_log = access_logs[0]
        if not isinstance(latest_log, dict):
            raise ControlIDError(f"Device {self._host} returned an invalid authorized access log")

        return latest_log

    async def async_load_latest_door_event(self) -> dict[str, Any] | None:
        """Load the latest door or secbox event and infer current state."""
        for event_name in ("secbox", "door"):
            payload = {
                "object": "access_events",
                "limit": 1,
                "order": ["id", "descending"],
                "where": {
                    "access_events": {
                        "event": event_name,
                    }
                },
            }

            try:
                data = await self._async_post_with_relogin("load_objects.fcgi", json=payload)
            except ControlIDError as err:
                raise ControlIDError(
                    f"Unable to load door state events from device {self._host}"
                ) from err

            if not isinstance(data, dict):
                continue

            access_events = data.get("access_events")
            if not isinstance(access_events, list) or not access_events:
                continue

            latest_event = access_events[0]
            if isinstance(latest_event, dict):
                return latest_event

        return None

    async def async_get_current_door_state(self, secbox_id: int) -> dict[str, Any] | None:
        """Read the current door state directly from the device."""
        for path in ("door_state.fcgi", "doors_state.fcgi"):
            try:
                data = await self._async_post_with_relogin(path, json={})
            except ControlIDError as err:
                _LOGGER.debug(
                    "Direct door state request %s failed for %s: %s",
                    path,
                    self._host,
                    err,
                )
                continue

            if not isinstance(data, dict):
                continue

            sec_boxes = data.get("sec_boxes")
            if isinstance(sec_boxes, list):
                for sec_box in sec_boxes:
                    if not isinstance(sec_box, dict):
                        continue
                    current_id = sec_box.get("id")
                    if current_id == secbox_id:
                        return sec_box
                if sec_boxes and isinstance(sec_boxes[0], dict):
                    return sec_boxes[0]

            doors = data.get("doors")
            if isinstance(doors, list) and doors and isinstance(doors[0], dict):
                return doors[0]

        return None

    async def async_configure_monitor(self, base_url: str, webhook_path: str) -> None:
        """Configure the device monitor to point to Home Assistant."""
        parsed = urlparse(base_url)
        if not parsed.hostname:
            raise ControlIDError(f"Invalid Home Assistant URL: {base_url}")

        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80

        full_payload = {
            "monitor": {
                "request_timeout": DEFAULT_REQUEST_TIMEOUT_MS,
                "hostname": parsed.hostname,
                "port": str(port),
                "path": webhook_path.lstrip("/"),
                "inform_access_event_id": 1,
            }
        }
        fallback_payload = {
            "monitor": {
                "hostname": parsed.hostname,
                "port": str(port),
                "path": webhook_path.lstrip("/"),
            }
        }

        try:
            await self._async_post_with_relogin("set_configuration.fcgi", json=full_payload)
            return
        except ControlIDError as err:
            _LOGGER.debug(
                "Full monitor configuration failed for %s, retrying with fallback payload: %s",
                self._host,
                err,
            )

        try:
            await self._async_post_with_relogin("set_configuration.fcgi", json=fallback_payload)
        except ControlIDError as err:
            raise ControlIDError(
                f"Unable to configure monitor for device {self._host}: {err}"
            ) from err


class ControlIDWebhookView(HomeAssistantView):
    """Accept Control iD webhooks with suffixes."""

    url = "/api/webhook/{webhook_id}"
    extra_urls = ["/api/webhook/{webhook_id}/{suffix}"]
    name = "api:controlid:webhook"
    requires_auth = False
    cors_allowed = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def post(
        self, request: web.Request, webhook_id: str, suffix: str | None = None
    ) -> web.Response:
        """Handle webhook POSTs."""
        runtime: ControlIDRuntime | None = self.hass.data[DOMAIN][DATA_WEBHOOKS].get(webhook_id)
        if runtime is None:
            return web.Response(status=404)

        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"status": "invalid_json"}, status=400)

        route_key = (suffix or "").lower()
        if not route_key:
            if "secbox" in payload:
                route_key = "secbox"
            elif "door" in payload:
                route_key = "door"
            elif "object_changes" in payload:
                route_key = "dao"

        if route_key in {"secbox", "door"}:
            runtime.async_handle_door_state(payload)
        elif route_key == "dao":
            runtime.async_handle_dao(payload)
        else:
            _LOGGER.debug("Ignoring unsupported Control iD webhook route: %s", route_key)

        return web.json_response({"status": "ok"})


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_WEBHOOKS, {})

    if not hass.data[DOMAIN].get(DATA_HTTP_VIEW_REGISTERED):
        hass.http.register_view(ControlIDWebhookView(hass))
        hass.data[DOMAIN][DATA_HTTP_VIEW_REGISTERED] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Control iD from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    webhook_path = entry.data[CONF_WEBHOOK_PATH]

    session = async_create_clientsession(hass, cookie_jar=CookieJar(unsafe=True))
    client = ControlIDClient(host, username, password, session)
    runtime = ControlIDRuntime(
        entry=entry,
        hass=hass,
        client=client,
        webhook_id=webhook_id,
        webhook_path=webhook_path,
        base_url=base_url,
    )

    hass.data[DOMAIN][entry.entry_id] = {DATA_RUNTIME: runtime}
    hass.data[DOMAIN][DATA_WEBHOOKS][webhook_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    base_url = hass.config.internal_url or hass.config.external_url
    if not base_url:
        raise ConfigEntryNotReady(
            "Set Home Assistant internal or external URL before configuring Control iD"
        )

    try:
        await client.async_configure_monitor(base_url, webhook_path)
    except ControlIDError as err:
        raise ConfigEntryNotReady(str(err)) from err

    await runtime.async_initialize_state()
    runtime.async_start_healthcheck()
    runtime.async_start_watchdog()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: ControlIDRuntime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
        runtime.async_stop_healthcheck()
        runtime.async_stop_watchdog()
        hass.data[DOMAIN][DATA_WEBHOOKS].pop(runtime.webhook_id, None)
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
