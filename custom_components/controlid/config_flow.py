"""Config flow for Control iD FaceID."""

from __future__ import annotations

import ast
import json
from typing import Any
import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from . import (
    CONF_SECBOX_ID,
    CONF_USER_MAP,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_PATH,
    DEFAULT_SECBOX_ID,
    ControlIDAuthError,
    ControlIDClient,
)


class ControlIDFaceIDConfigFlow(config_entries.ConfigFlow, domain="controlid"):
    """Handle a config flow for Control iD FaceID."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            session = async_create_clientsession(self.hass)
            client = ControlIDClient(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                session,
            )

            try:
                await client.async_login()
            except ControlIDAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                webhook_id = f"controlid_{uuid.uuid4().hex}"
                data = {
                    **user_input,
                    CONF_WEBHOOK_ID: webhook_id,
                    CONF_WEBHOOK_PATH: f"/api/webhook/{webhook_id}",
                }
                return self.async_create_entry(
                    title=f"Control iD {user_input[CONF_HOST]}",
                    data=data,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME, default="admin"): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_SECBOX_ID, default=DEFAULT_SECBOX_ID): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow handler."""
        return ControlIDFaceIDOptionsFlow()


class ControlIDFaceIDOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Control iD FaceID."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the integration options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_map = _parse_user_map(user_input.get(CONF_USER_MAP, "{}"))
            except ValueError:
                errors[CONF_USER_MAP] = "invalid_user_map"
            else:
                return self.async_create_entry(title="", data={CONF_USER_MAP: user_map})

        current_map = self.config_entry.options.get(CONF_USER_MAP, {})
        if not isinstance(current_map, dict):
            current_map = {}

        try:
            current_map_text = json.dumps(current_map, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            current_map_text = "{}"

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_USER_MAP,
                    default=current_map_text,
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


def _parse_user_map(value: str) -> dict[str, str]:
    """Parse a JSON mapping of user IDs to friendly names."""
    if not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as err:
            raise ValueError("User map must be valid JSON or dict syntax") from err

    if not isinstance(parsed, dict):
        raise ValueError("User map must be a JSON object")

    normalized: dict[str, str] = {}
    for key, item in parsed.items():
        normalized[str(key)] = str(item)

    return normalized
