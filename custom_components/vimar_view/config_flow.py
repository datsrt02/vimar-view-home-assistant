"""Config flow for Vimar VIEW."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import aiohttp_client

from .api import (
    VimarAuthSession,
    VimarViewApiError,
    VimarViewAuthError,
    VimarViewConnectionError,
    decode_jwt_claims,
)
from .const import CONF_CALLBACK_URL, CONF_DISCOVERY, CONF_TOKEN, CONF_USER, DOMAIN


class VimarViewConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Vimar VIEW config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._auth: VimarAuthSession | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Start account login."""
        errors: dict[str, str] = {}
        if self._auth is None:
            try:
                session = aiohttp_client.async_get_clientsession(self.hass)
                self._auth = await VimarAuthSession.create(session)
            except VimarViewConnectionError:
                errors["base"] = "cannot_connect"

        if user_input is not None and self._auth is not None:
            try:
                token = await self._auth.exchange_password(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except VimarViewAuthError:
                errors["base"] = "invalid_auth"
            except VimarViewApiError:
                errors["base"] = "invalid_auth"
            except VimarViewConnectionError:
                errors["base"] = "cannot_connect"
            else:
                claims = decode_jwt_claims(token.get("id_token")) or decode_jwt_claims(
                    token.get("access_token")
                )
                user_id = str(
                    claims.get("email")
                    or claims.get("preferred_username")
                    or claims.get("sub")
                    or "vimar_view"
                ).lower()
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_id,
                    data={
                        CONF_DISCOVERY: self._auth.discovery,
                        CONF_TOKEN: token,
                        CONF_USER: claims,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_oauth(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Manual OAuth fallback for accounts that reject password login."""
        errors: dict[str, str] = {}
        if self._auth is None:
            try:
                session = aiohttp_client.async_get_clientsession(self.hass)
                self._auth = await VimarAuthSession.create(session)
            except VimarViewConnectionError:
                errors["base"] = "cannot_connect"

        if user_input is not None and self._auth is not None:
            try:
                token = await self._auth.exchange_callback_url(user_input[CONF_CALLBACK_URL])
            except VimarViewAuthError:
                errors["base"] = "invalid_auth"
            except VimarViewConnectionError:
                errors["base"] = "cannot_connect"
            else:
                claims = decode_jwt_claims(token.get("id_token")) or decode_jwt_claims(
                    token.get("access_token")
                )
                user_id = str(
                    claims.get("email")
                    or claims.get("preferred_username")
                    or claims.get("sub")
                    or "vimar_view"
                ).lower()
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_id,
                    data={
                        CONF_DISCOVERY: self._auth.discovery,
                        CONF_TOKEN: token,
                        CONF_USER: claims,
                    },
                )

        authorization_url = self._auth.authorization_url if self._auth else ""
        fallback_authorization_url = ""
        if self._auth is not None:
            urls = list(self._auth.authorization_urls.values())
            if len(urls) > 1:
                fallback_authorization_url = urls[1]
        return self.async_show_form(
            step_id="oauth",
            data_schema=vol.Schema({vol.Required(CONF_CALLBACK_URL): str}),
            description_placeholders={
                "authorization_url": authorization_url,
                "fallback_authorization_url": fallback_authorization_url,
            },
            errors=errors,
        )
