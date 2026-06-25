"""Cloud API client for Vimar VIEW."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from html.parser import HTMLParser
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from aiohttp import ClientError, ClientResponse, ClientSession, FormData

from .const import (
    APP_CLIENT_ID,
    APP_REDIRECT_URIS,
    APP_SCOPE,
    APP_USER_AGENT,
    BASE_URL,
    MAX_DEVICE_FETCH_CONCURRENCY,
    OIDC_DISCOVERY_URL,
)
from .models import JsonObject, VimarDataEntity, VimarDevice, VimarPlant, VimarRoutine, VimarSystemData

_LOGGER = logging.getLogger(__name__)

TokenUpdateCallback = Callable[[JsonObject], Awaitable[None] | None]


class _AuthNavigationResult:
    """Result from manually navigating the auth pages."""

    def __init__(
        self,
        url: str,
        *,
        callback_url: str | None = None,
        text: str | None = None,
    ) -> None:
        self.url = url
        self.callback_url = callback_url
        self.text = text


class _LoginForm:
    """HTML login form details."""

    def __init__(self, action: str, inputs: dict[str, str], enctype: str | None = None) -> None:
        self.action = action
        self.inputs = inputs
        self.enctype = enctype or ""


class _LoginFormParser(HTMLParser):
    """Extract forms from Keycloak login HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current_form: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            self._current_form = {
                "id": attr_map.get("id", ""),
                "action": attr_map.get("action", ""),
                "enctype": attr_map.get("enctype", ""),
                "inputs": {},
            }
            return
        if tag.lower() not in {"button", "input"} or self._current_form is None:
            return
        name = attr_map.get("name")
        if name:
            self._current_form["inputs"][name] = attr_map.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None


class VimarViewError(Exception):
    """Base Vimar VIEW error."""


class VimarViewAuthError(VimarViewError):
    """Authentication failed."""


class VimarViewConnectionError(VimarViewError):
    """Connection failed."""


class VimarViewApiError(VimarViewError):
    """The cloud API returned an error."""


class VimarAuthSession:
    """Small AppAuth-compatible authorization code helper."""

    def __init__(self, session: ClientSession, discovery: JsonObject) -> None:
        """Initialize auth session."""
        self.session = session
        self.discovery = discovery
        self.state = secrets.token_urlsafe(24)
        self.nonce = secrets.token_urlsafe(24)
        self.code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(self.code_verifier.encode("ascii")).digest()
        self.code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @classmethod
    async def create(cls, session: ClientSession) -> "VimarAuthSession":
        """Create an authorization helper by loading OpenID discovery."""
        discovery = await request_json(session, "GET", OIDC_DISCOVERY_URL, auth=False)
        return cls(session, discovery)

    @property
    def authorization_url(self) -> str:
        """Return the URL the user must open."""
        return self.authorization_url_for_redirect_uri(APP_REDIRECT_URIS[0])

    @property
    def authorization_urls(self) -> dict[str, str]:
        """Return primary and fallback URLs the user can open."""
        return {
            redirect_uri: self.authorization_url_for_redirect_uri(redirect_uri)
            for redirect_uri in APP_REDIRECT_URIS
        }

    def authorization_url_for_redirect_uri(self, redirect_uri: str) -> str:
        """Return an authorization URL for a redirect URI."""
        authorization_endpoint = self.discovery["authorization_endpoint"]
        query = {
            "client_id": APP_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": APP_SCOPE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{authorization_endpoint}?{urlencode(query)}"

    async def exchange_callback_url(self, callback_url: str) -> JsonObject:
        """Exchange the pasted redirect URL for tokens."""
        cleaned_callback = callback_url.strip()
        parsed = urlparse(cleaned_callback)
        query = parse_qs(parsed.query)
        if not query and parsed.fragment:
            query = parse_qs(parsed.fragment)
        error = _first(query.get("error"))
        if error:
            description = _first(query.get("error_description")) or error
            raise VimarViewAuthError(description)
        code = _first(query.get("code"))
        if not code and "=" not in cleaned_callback and "/" not in cleaned_callback:
            code = cleaned_callback
        state = _first(query.get("state"))
        if not code:
            raise VimarViewAuthError("Redirect URL does not contain an authorization code")
        if state is not None and state != self.state:
            raise VimarViewAuthError("OAuth state mismatch")
        redirect_uri = _redirect_uri_from_callback(parsed) or APP_REDIRECT_URIS[0]

        token_endpoint = self.discovery["token_endpoint"]
        data = {
            "grant_type": "authorization_code",
            "client_id": APP_CLIENT_ID,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": self.code_verifier,
        }
        token = await request_json(self.session, "POST", token_endpoint, data=data, auth=False)
        token["created_at"] = int(time.time())
        return token

    async def exchange_credentials(self, username: str, password: str) -> JsonObject:
        """Exchange account credentials using direct grant or the web login form."""
        username = username.strip()
        try:
            return await self.exchange_password(username, password)
        except (VimarViewApiError, VimarViewAuthError):
            _LOGGER.debug("Vimar direct password grant failed, trying browser form flow")
            return await self.exchange_login_form(username, password)

    async def exchange_password(self, username: str, password: str) -> JsonObject:
        """Exchange account credentials for tokens."""
        token_endpoint = self.discovery["token_endpoint"]
        data = {
            "grant_type": "password",
            "client_id": APP_CLIENT_ID,
            "username": username,
            "password": password,
            "scope": APP_SCOPE,
        }
        token = await request_json(self.session, "POST", token_endpoint, data=data, auth=False)
        token["created_at"] = int(time.time())
        return token

    async def exchange_login_form(self, username: str, password: str) -> JsonObject:
        """Submit the Vimar web login form and exchange the resulting auth code."""
        last_error: VimarViewError | None = None
        for auth_url in self.authorization_urls.values():
            try:
                result = await self._request_without_redirects("GET", auth_url)
                if result.callback_url is not None:
                    return await self.exchange_callback_url(result.callback_url)
                if result.text is None:
                    raise VimarViewAuthError("Vimar login page did not return a form")

                login_form = _extract_login_form(result.text)
                form_action = urljoin(result.url, login_form.action)
                form_data = dict(login_form.inputs)
                if "email" in form_data or "passw" in form_data:
                    form_data["email"] = username
                    form_data["passw"] = password
                else:
                    form_data["username"] = username
                    form_data["password"] = password

                result = await self._request_without_redirects(
                    "POST",
                    form_action,
                    data=_build_form_payload(form_data, login_form.enctype),
                )
                if result.callback_url is None:
                    raise VimarViewAuthError("Vimar login did not return an authorization code")
                return await self.exchange_callback_url(result.callback_url)
            except VimarViewError as err:
                last_error = err
                _LOGGER.debug("Vimar web login form flow failed: %s", err)
        raise last_error or VimarViewAuthError("Vimar login did not return an authorization code")

    async def _request_without_redirects(
        self,
        method: str,
        url: str,
        data: Any | None = None,
    ) -> "_AuthNavigationResult":
        """Navigate auth pages while preserving custom-scheme redirects."""
        current_method = method
        current_url = url
        current_data = data
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": APP_USER_AGENT,
        }
        try:
            for _ in range(10):
                async with self.session.request(
                    current_method,
                    current_url,
                    headers=headers,
                    data=current_data,
                    allow_redirects=False,
                    timeout=30,
                ) as response:
                    location = response.headers.get("Location")
                    if location and response.status in (301, 302, 303, 307, 308):
                        if location.startswith("com.prova.app:"):
                            return _AuthNavigationResult(url=location, callback_url=location)
                        current_url = urljoin(str(response.url), location)
                        if response.status in (301, 302, 303):
                            current_method = "GET"
                            current_data = None
                        continue
                    if response.status >= 400:
                        text = await response.text()
                        raise VimarViewAuthError(text or response.reason)
                    text = await response.text()
                    return _AuthNavigationResult(url=str(response.url), text=text)
        except ClientError as err:
            raise VimarViewConnectionError(str(err)) from err
        raise VimarViewConnectionError("Too many Vimar login redirects")


class VimarViewApi:
    """Vimar VIEW cloud client."""

    def __init__(
        self,
        session: ClientSession,
        token: JsonObject,
        discovery: JsonObject,
        token_update_callback: TokenUpdateCallback | None = None,
    ) -> None:
        """Initialize the API client."""
        self.session = session
        self.token = dict(token)
        self.discovery = discovery
        self._token_update_callback = token_update_callback

    async def async_refresh_access_token(self) -> None:
        """Refresh the access token."""
        refresh_token = self.token.get("refresh_token")
        if not refresh_token:
            raise VimarViewAuthError("No refresh token available")
        data = {
            "grant_type": "refresh_token",
            "client_id": APP_CLIENT_ID,
            "refresh_token": refresh_token,
        }
        token = await request_json(
            self.session,
            "POST",
            self.discovery["token_endpoint"],
            data=data,
            auth=False,
        )
        token["created_at"] = int(time.time())
        if "refresh_token" not in token:
            token["refresh_token"] = refresh_token
        self.token.update(token)
        if self._token_update_callback is not None:
            result = self._token_update_callback(dict(self.token))
            if asyncio.iscoroutine(result):
                await result

    async def async_fetch_system(self) -> VimarSystemData:
        """Fetch account plants, devices, extracted datapoints, and routines."""
        associations_task = asyncio.create_task(self.get_user_associations())
        plants_task = asyncio.create_task(self.get_iot_plants())
        associations = await associations_task
        raw_plants = await plants_task

        plants = self._normalize_plants(raw_plants, associations)
        device_ids = sorted(_extract_ids(associations, ("duid", "deviceUid", "deviceuid", "deviceId")))
        device_ids.extend(
            device_id
            for device_id in _extract_ids(raw_plants, ("duid", "deviceUid", "deviceuid", "deviceId"))
            if device_id not in device_ids
        )

        devices = await self._fetch_devices(device_ids, plants)
        routines = await self._fetch_routines(plants)
        entities = self._extract_entities(plants, devices)

        return VimarSystemData(
            plants=plants,
            devices=devices,
            entities=entities,
            routines=routines,
            raw={"associations": associations, "plants": raw_plants},
        )

    async def get_user_associations(self) -> Any:
        """Return user associations."""
        return await self._request("GET", "/ngvcloud/user/associations")

    async def get_iot_plants(self) -> Any:
        """Return IOT plants."""
        return await self._request("GET", "/ngvcloud/inst/plants/v2?plantTypes=IOT")

    async def get_device(self, device_id: str) -> Any:
        """Return a cloud device document."""
        return await self._request("GET", f"/ngvcloud/user/devices/{device_id}")

    async def get_capabilities(self, plant_id: str) -> Any:
        """Return plant capabilities."""
        return await self._request("GET", f"api/v1/plants/{plant_id}/capabilities")

    async def get_routines(self, plant_id: str) -> Any:
        """Return plant routines."""
        return await self._request("GET", f"api/v1/plants/{plant_id}/routines")

    async def async_execute_routine(self, plant_id: str, routine_id: str) -> Any:
        """Execute a routine."""
        return await self._request(
            "POST",
            f"api/v1/plants/{plant_id}/routines/{routine_id}/execute",
            json_data={},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Any | None = None,
        data: Any | None = None,
        base_url: str = BASE_URL,
        retry_auth: bool = True,
    ) -> Any:
        """Run an authorized cloud request."""
        if self._token_expiring:
            await self.async_refresh_access_token()
        url = _join_url(base_url, path)
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en",
            "Authorization": f"Bearer {self.token.get('access_token', '')}",
            "User-Agent": APP_USER_AGENT,
        }
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                json=json_data,
                data=data,
                timeout=30,
            ) as response:
                if response.status == 401 and retry_auth:
                    response.release()
                    await self.async_refresh_access_token()
                    return await self._request(
                        method,
                        path,
                        json_data=json_data,
                        data=data,
                        base_url=base_url,
                        retry_auth=False,
                    )
                return await _read_response(response)
        except ClientError as err:
            raise VimarViewConnectionError(str(err)) from err

    @property
    def _token_expiring(self) -> bool:
        expires_in = int(self.token.get("expires_in") or 0)
        created_at = int(self.token.get("created_at") or 0)
        return bool(expires_in and created_at and time.time() > created_at + expires_in - 60)

    async def _fetch_devices(
        self,
        device_ids: Iterable[str],
        plants: dict[str, VimarPlant],
    ) -> dict[str, VimarDevice]:
        semaphore = asyncio.Semaphore(MAX_DEVICE_FETCH_CONCURRENCY)

        async def fetch_one(device_id: str) -> tuple[str, Any | None]:
            async with semaphore:
                try:
                    return device_id, await self.get_device(device_id)
                except VimarViewError as err:
                    _LOGGER.debug("Failed to fetch Vimar device %s: %s", device_id, err)
                    return device_id, None

        results = await asyncio.gather(*(fetch_one(device_id) for device_id in device_ids))
        devices: dict[str, VimarDevice] = {}
        for device_id, raw in results:
            if raw is None:
                devices[device_id] = VimarDevice(id=device_id, name=f"Vimar device {device_id}")
                continue
            doc = _as_dict(raw)
            plant_id = _first_string(doc, ("plantId", "plantuid", "puid", "plant_id"))
            name = _first_string(doc, ("name", "deviceName", "label", "description")) or device_id
            model = _first_string(doc, ("model", "gatewayModel", "productCode", "type"))
            devices[device_id] = VimarDevice(
                id=device_id,
                name=name,
                plant_id=plant_id if plant_id in plants else plant_id,
                model=model,
                raw=doc,
            )
        return devices

    async def _fetch_routines(self, plants: dict[str, VimarPlant]) -> dict[str, VimarRoutine]:
        routines: dict[str, VimarRoutine] = {}
        for plant_id, plant in plants.items():
            try:
                raw_routines = await self.get_routines(plant_id)
            except VimarViewError as err:
                _LOGGER.debug("Failed to fetch routines for plant %s: %s", plant_id, err)
                continue
            for item in _iter_items(raw_routines):
                routine_id = _first_string(item, ("routineId", "id", "uid", "uuid"))
                if not routine_id:
                    continue
                name = _first_string(item, ("name", "title", "label")) or f"Routine {routine_id}"
                key = f"{plant_id}:{routine_id}"
                routines[key] = VimarRoutine(
                    id=routine_id,
                    plant_id=plant.id,
                    name=name,
                    raw=item,
                )
        return routines

    def _normalize_plants(self, raw_plants: Any, associations: Any) -> dict[str, VimarPlant]:
        plants: dict[str, VimarPlant] = {}
        for item in _iter_items(raw_plants):
            plant_id = _first_string(item, ("plantId", "plantuid", "puid", "uid", "id"))
            if not plant_id:
                continue
            name = _first_string(item, ("name", "plantName", "label", "description")) or plant_id
            plants[plant_id] = VimarPlant(id=plant_id, name=name, raw=item)

        for item in _iter_items(associations):
            plant_id = _first_string(item, ("plantId", "plantuid", "puid", "uid"))
            if plant_id and plant_id not in plants:
                name = _first_string(item, ("plantName", "name", "label")) or plant_id
                plants[plant_id] = VimarPlant(id=plant_id, name=name, raw=item)
        return plants

    def _extract_entities(
        self,
        plants: dict[str, VimarPlant],
        devices: dict[str, VimarDevice],
    ) -> dict[str, VimarDataEntity]:
        entities: dict[str, VimarDataEntity] = {}
        for plant in plants.values():
            value = _first_existing(plant.raw, ("status", "state", "online", "enabled"))
            if value is not None:
                key = f"plant:{plant.id}:status"
                entities[key] = VimarDataEntity(
                    id=key,
                    name=f"{plant.name} status",
                    platform=_platform_for_value(value, "status"),
                    value=value,
                    plant_id=plant.id,
                    category="plant",
                    path="plant.status",
                    raw=plant.raw,
                )

        for device in devices.values():
            device_doc = device.raw or {}
            device_value = _first_existing(device_doc, ("status", "state", "online", "enabled"))
            if device_value is not None:
                key = f"device:{device.id}:status"
                entities[key] = VimarDataEntity(
                    id=key,
                    name=f"{device.name} status",
                    platform=_platform_for_value(device_value, "status"),
                    value=device_value,
                    plant_id=device.plant_id,
                    device_id=device.id,
                    category="device",
                    path="device.status",
                    raw=device_doc,
                )

            for path, node in _walk_dicts(device_doc):
                entity_id = _first_string(node, ("idsf", "idSf", "sfId", "sfid", "uid", "uuid", "id"))
                value = _first_existing(
                    node,
                    ("value", "state", "status", "active", "enabled", "online", "measure", "measuredValue"),
                )
                name = _first_string(
                    node,
                    ("name", "label", "title", "description", "displayName", "functionName"),
                )
                category = _first_string(node, ("category", "type", "subtype", "function", "deviceType"))
                if not _looks_like_data_entity(path, node, entity_id, name, category, value):
                    continue
                unique = f"device:{device.id}:{'.'.join(path)}:{entity_id or name}"
                if unique in entities:
                    continue
                safe_value = _coerce_value(value)
                label = name or category or entity_id or path[-1]
                entities[unique] = VimarDataEntity(
                    id=unique,
                    name=f"{device.name} {label}",
                    platform=_platform_for_value(safe_value, category or label),
                    value=safe_value,
                    plant_id=device.plant_id,
                    device_id=device.id,
                    category=category,
                    path=".".join(path),
                    raw=node,
                )
        return entities


async def request_json(
    session: ClientSession,
    method: str,
    url: str,
    *,
    auth: bool = False,
    data: Any | None = None,
    json_data: Any | None = None,
) -> JsonObject:
    """Run an unauthenticated JSON request used during auth."""
    headers = {
        "Accept": "application/json",
        "User-Agent": APP_USER_AGENT,
    }
    if json_data is not None:
        headers["Content-Type"] = "application/json"
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            data=data,
            json=json_data,
            timeout=30,
        ) as response:
            result = await _read_response(response)
            if isinstance(result, dict):
                return result
            raise VimarViewApiError("Expected JSON object")
    except ClientError as err:
        raise VimarViewConnectionError(str(err)) from err


async def _read_response(response: ClientResponse) -> Any:
    if response.status >= 400:
        text = await response.text()
        if response.status in (401, 403):
            raise VimarViewAuthError(text or response.reason)
        raise VimarViewApiError(f"{response.status}: {text or response.reason}")
    if response.status == 204:
        return None
    content_type = response.headers.get("Content-Type", "")
    if "json" in content_type:
        return await response.json(content_type=None)
    text = await response.text()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def decode_jwt_claims(token: str | None) -> JsonObject:
    """Decode JWT claims without verification for a stable config title."""
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}


def _join_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(base_url, path.lstrip("/"))


def _redirect_uri_from_callback(parsed) -> str | None:
    if parsed.scheme != "com.prova.app":
        return None
    callback_uri = f"{parsed.scheme}:{parsed.path}"
    if callback_uri in APP_REDIRECT_URIS:
        return callback_uri
    return None


def _extract_login_form(html: str) -> _LoginForm:
    parser = _LoginFormParser()
    parser.feed(html)
    if parser._current_form is not None:
        parser.forms.append(parser._current_form)
        parser._current_form = None

    for form in parser.forms:
        action = str(form.get("action") or "")
        inputs = form.get("inputs") or {}
        form_id = str(form.get("id") or "")
        if form_id == "kc-form-login" or "login-actions/authenticate" in action:
            return _LoginForm(action, dict(inputs), str(form.get("enctype") or ""))
        if "email" in inputs and "passw" in inputs:
            return _LoginForm(action, dict(inputs), str(form.get("enctype") or ""))

    for form in parser.forms:
        inputs = form.get("inputs") or {}
        if "username" in inputs or "password" in inputs or "email" in inputs:
            return _LoginForm(
                str(form.get("action") or ""),
                dict(inputs),
                str(form.get("enctype") or ""),
            )

    raise VimarViewAuthError("Could not find Vimar login form")


def _build_form_payload(form_data: dict[str, str], enctype: str) -> Any:
    if "multipart/form-data" not in enctype.lower():
        return form_data
    payload = FormData()
    for key, value in form_data.items():
        payload.add_field(key, value)
    return payload


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]


def _as_dict(value: Any) -> JsonObject:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _iter_items(value: Any) -> Iterable[JsonObject]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(value, dict):
        for key in ("items", "data", "plants", "associations", "result", "results", "routines"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        yield item
                return
        yield value


def _extract_ids(value: Any, keys: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item not in (None, ""):
                found.add(str(item))
            found.update(_extract_ids(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.update(_extract_ids(item, keys))
    return found


def _first_string(value: JsonObject, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        item = value.get(key)
        if item not in (None, ""):
            return str(item)
    return None


def _first_existing(value: JsonObject, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _walk_dicts(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], JsonObject]]:
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, (*path, str(index)))


def _looks_like_data_entity(
    path: tuple[str, ...],
    node: JsonObject,
    entity_id: str | None,
    name: str | None,
    category: str | None,
    value: Any,
) -> bool:
    if value is None:
        return False
    joined_path = ".".join(path).lower()
    if any(token in joined_path for token in ("password", "token", "secret", "auth")):
        return False
    has_identity = bool(entity_id or name or category)
    has_signal_key = any(
        key in node
        for key in (
            "idsf",
            "sfId",
            "category",
            "function",
            "deviceType",
            "state",
            "status",
            "value",
            "measure",
            "measuredValue",
        )
    )
    has_relevant_path = any(
        token in joined_path
        for token in ("sf", "sfe", "element", "device", "function", "status", "capabilit")
    )
    return has_identity and (has_signal_key or has_relevant_path)


def _coerce_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        for key in ("value", "state", "status", "name", "label"):
            if key in value and isinstance(value[key], (str, int, float, bool)):
                return value[key]
    return json.dumps(value, sort_keys=True)[:255]


def _platform_for_value(value: Any, hint: str | None) -> str:
    if isinstance(value, bool):
        return "binary_sensor"
    normalized = str(hint or "").lower()
    if normalized in {"online", "connected", "available", "presence", "motion"}:
        return "binary_sensor"
    return "sensor"
