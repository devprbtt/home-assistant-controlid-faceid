"""Control iD FaceID custom integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientResponse, ClientSession, CookieJar, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

DOMAIN = "controlid_faceid"

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


@dataclass
class ControlIDRuntime:
    """Runtime data for a config entry."""

    entry: ConfigEntry
    client: "ControlIDClient"
    webhook_id: str
    webhook_path: str
    state: ControlIDState = field(default_factory=ControlIDState)
    _listeners: list[CALLBACK_TYPE] = field(default_factory=list)

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
    def async_handle_secbox(self, payload: dict[str, Any]) -> None:
        """Store secbox webhook payload."""
        secbox = payload.get("secbox") or {}
        if "open" in secbox:
            self.state.door_open = bool(secbox["open"])
        self.state.door_id = secbox.get("id")
        self.state.device_id = payload.get("device_id", self.state.device_id)
        self.state.access_event_id = payload.get("access_event_id")
        self.state.door_updated_at = _utc_from_timestamp(payload.get("time"))
        self.async_notify()

    @callback
    def async_handle_dao(self, payload: dict[str, Any]) -> None:
        """Store DAO webhook payload."""
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

    async def _async_raise_for_session_error(self, response: ClientResponse) -> None:
        """Inspect responses for session expiry errors."""
        if response.status == 401:
            raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")

        if response.status >= 400:
            body = await response.text()
            if "session" in body.lower():
                raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")
            raise ControlIDError(f"Device {self._host} returned HTTP {response.status}: {body}")

        try:
            data = await response.json(content_type=None)
        except ValueError:
            return

        if isinstance(data, dict):
            message = str(data.get("error") or data.get("message") or "")
            if "session" in message.lower():
                raise ControlIDSessionExpiredError(f"Session expired for device {self._host}")

    async def _async_post_with_relogin(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> None:
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
                await self._async_raise_for_session_error(response)
                return
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

    async def async_configure_monitor(self, base_url: str, webhook_path: str) -> None:
        """Configure the device monitor to point to Home Assistant."""
        parsed = urlparse(base_url)
        if not parsed.hostname:
            raise ControlIDError(f"Invalid Home Assistant URL: {base_url}")

        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80

        payload = {
            "monitor": {
                "request_timeout": DEFAULT_REQUEST_TIMEOUT_MS,
                "hostname": parsed.hostname,
                "port": str(port),
                "path": webhook_path.lstrip("/"),
                "inform_access_event_id": 1,
            }
        }

        try:
            await self._async_post_with_relogin("set_configuration.fcgi", json=payload)
        except ControlIDError as err:
            raise ControlIDError(f"Unable to configure monitor for device {self._host}") from err


class ControlIDWebhookView(HomeAssistantView):
    """Accept Control iD webhooks with suffixes."""

    url = "/api/webhook/{webhook_id}"
    extra_urls = ["/api/webhook/{webhook_id}/{suffix}"]
    name = "api:controlid_faceid:webhook"
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
            elif "object_changes" in payload:
                route_key = "dao"

        if route_key == "secbox":
            runtime.async_handle_secbox(payload)
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
        client=client,
        webhook_id=webhook_id,
        webhook_path=webhook_path,
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: ControlIDRuntime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
        hass.data[DOMAIN][DATA_WEBHOOKS].pop(runtime.webhook_id, None)
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
