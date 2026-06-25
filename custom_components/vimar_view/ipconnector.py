"""Vimar IPConnector websocket discovery."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, WSMsgType

from .const import APP_USER_AGENT, CLOUD_WSS_BASE_URL
from .models import JsonObject, VimarDataEntity, VimarDevice, VimarPlant

_LOGGER = logging.getLogger(__name__)

DEFAULT_SF_CATEGORIES = ("Plant", "Scene", "PhilipsHUE", "ThirdParty")

SFE_CMD_ONOFF = "SFE_Cmd_OnOff"
SFE_STATE_ONOFF = "SFE_State_OnOff"
SFE_CMD_BRIGHTNESS = "SFE_Cmd_Brightness"
SFE_STATE_BRIGHTNESS = "SFE_State_Brightness"
SFE_CMD_SHUTTER = "SFE_Cmd_Shutter"
SFE_STATE_SHUTTER = "SFE_State_Shutter"
SFE_CMD_SHUTTER_WO_POSITION = "SFE_Cmd_ShutterWithoutPosition"
SFE_STATE_SHUTTER_WO_POSITION = "SFE_State_ShutterWithoutPosition"
SFE_CMD_AMBIENT_SETPOINT = "SFE_Cmd_AmbientSetpoint"
SFE_STATE_AMBIENT_SETPOINT = "SFE_State_AmbientSetpoint"
SFE_STATE_AMBIENT_TEMP = "SFE_State_AmbientTemperature"
SFE_STATE_CLIMA_TEMP = "SFE_State_ClimaTemperature"
SFE_CMD_HVAC_MODE = "SFE_Cmd_HVACMode"
SFE_CMD_HVAC_MODE_DEAD_ZONE = "SFE_Cmd_HVACModeDeadZone"
SFE_STATE_HVAC_MODE = "SFE_State_HVACMode"
SFE_STATE_CHANGEOVER = "SFE_State_ChangeOverMode"
SFE_CMD_CHANGEOVER = "SFE_Cmd_ChangeOverMode"

TRUTHY_STATES = {"1", "active", "enable", "enabled", "on", "online", "open", "true", "yes"}
FALSEY_STATES = {"0", "closed", "disable", "disabled", "false", "off", "offline", "stop", "stopped", "no"}


class VimarIpConnectorError(Exception):
    """Raised when IPConnector discovery or control fails."""


@dataclass(frozen=True)
class VimarIpGateway:
    """Gateway credentials needed by the IPConnector websocket."""

    id: str
    name: str
    password: str
    plant_id: str | None = None
    username: str = ""
    useruid: str = ""
    raw: JsonObject = field(default_factory=dict)


@dataclass
class VimarIpDiscoveryResult:
    """Entities discovered from one gateway."""

    plants: dict[str, VimarPlant] = field(default_factory=dict)
    devices: dict[str, VimarDevice] = field(default_factory=dict)
    entities: dict[str, VimarDataEntity] = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)


class VimarIpConnector:
    """Small subset of Vimar IPConnector used by the mobile app."""

    def __init__(
        self,
        session: ClientSession,
        access_token: str,
        *,
        source_uid: str,
        username: str,
        useruid: str,
    ) -> None:
        """Initialize the websocket helper."""
        self._session = session
        self._access_token = access_token
        self._source_uid = source_uid
        self._username = username
        self._useruid = useruid

    async def async_discover_gateway(self, gateway: VimarIpGateway) -> VimarIpDiscoveryResult:
        """Attach to a gateway and discover its system functions."""
        async with _IpConnectorSocket(
            self._session,
            self._access_token,
            gateway.id,
            source_uid=self._source_uid,
        ) as socket:
            attach_response = await socket.request(
                "attach",
                args=[self._attach_args(gateway)],
                token="",
            )
            attach = _first_result(attach_response)
            session_token = str(attach.get("token") or "")
            if not session_token:
                raise VimarIpConnectorError(f"Gateway {gateway.id} did not return a session token")
            socket.session_token = session_token

            ambient_response: JsonObject | None = None
            try:
                ambient_response = await socket.request("ambientdiscovery")
            except VimarIpConnectorError as err:
                _LOGGER.debug("Vimar ambient discovery failed for %s: %s", gateway.id, err)

            categories = _string_list(attach.get("sfcategory")) or list(DEFAULT_SF_CATEGORIES)
            sf_responses: list[JsonObject] = []
            for category in categories:
                try:
                    response = await socket.request(
                        "sfdiscovery",
                        args=[{"sfcategory": category}],
                        params=[{"idambient": [], "accesscode": "", "withvalues": True}],
                    )
                    response["_sfcategory"] = category
                    sf_responses.append(response)
                except VimarIpConnectorError as err:
                    _LOGGER.debug(
                        "Vimar SF discovery failed for gateway %s category %s: %s",
                        gateway.id,
                        category,
                        err,
                    )

            status_map = await self._fetch_statuses(socket, sf_responses)
            return _build_discovery_result(
                gateway,
                attach,
                ambient_response,
                sf_responses,
                status_map,
            )

    async def async_do_action(
        self,
        gateway: VimarIpGateway,
        idsf: int,
        sfetype: str,
        value: str,
    ) -> None:
        """Attach to a gateway and execute a simple doaction command."""
        async with _IpConnectorSocket(
            self._session,
            self._access_token,
            gateway.id,
            source_uid=self._source_uid,
        ) as socket:
            attach_response = await socket.request(
                "attach",
                args=[self._attach_args(gateway)],
                token="",
            )
            attach = _first_result(attach_response)
            socket.session_token = str(attach.get("token") or "")
            if not socket.session_token:
                raise VimarIpConnectorError(f"Gateway {gateway.id} did not return a session token")
            await socket.request(
                "doaction",
                args=[{"idsf": idsf, "sfetype": sfetype, "value": value}],
                params=[{"accesscode": ""}],
            )

    async def _fetch_statuses(
        self,
        socket: "_IpConnectorSocket",
        sf_responses: list[JsonObject],
    ) -> dict[tuple[int, str], JsonObject]:
        """Fetch current values for discovered SFE entries."""
        status_map: dict[tuple[int, str], JsonObject] = {}
        for sf in _iter_sfs(sf_responses):
            idsf = _safe_int(sf.get("idsf"))
            if idsf is None:
                continue
            sfetypes = [
                str(element.get("sfetype"))
                for element in _iter_elements(sf)
                if element.get("sfetype")
            ]
            if not sfetypes:
                continue
            try:
                response = await socket.request(
                    "getstatus",
                    args=[{"idsf": idsf, "sfetype": sfetypes}],
                    params=[{"accesscode": ""}],
                )
            except VimarIpConnectorError as err:
                _LOGGER.debug("Vimar getstatus failed for idsf %s: %s", idsf, err)
                continue
            for result in response.get("result") or []:
                result_idsf = _safe_int(result.get("idsf"))
                if result_idsf is None:
                    continue
                for element in result.get("elements") or []:
                    sfetype = element.get("sfetype")
                    if isinstance(element, dict) and sfetype:
                        status_map[(result_idsf, str(sfetype))] = element
        return status_map

    def _attach_args(self, gateway: VimarIpGateway) -> JsonObject:
        username = gateway.username or self._username
        useruid = gateway.useruid or self._useruid
        return {
            "credential": {
                "username": username,
                "useruid": useruid,
                "password": gateway.password,
            },
            "clientinfo": {
                "manufacturertag": "Vimar",
                "clienttag": "userapp",
                "sfmodelversion": "1.0.0",
                "lang": "en",
                "protocolversion": "2.4",
            },
            "communication": {"ipaddress": ""},
        }


class _IpConnectorSocket:
    """Request/response wrapper around the gateway websocket."""

    def __init__(
        self,
        session: ClientSession,
        access_token: str,
        duid: str,
        *,
        source_uid: str,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._duid = duid
        self._source_uid = source_uid
        self._msgid = 0
        self._ws = None
        self.session_token = ""

    async def __aenter__(self) -> "_IpConnectorSocket":
        url = (
            f"{CLOUD_WSS_BASE_URL}/wssmqtt/deviceproxy"
            f"?duid={quote(self._duid)}&access_token={quote(self._access_token)}"
        )
        try:
            self._ws = await self._session.ws_connect(
                url,
                heartbeat=25,
                headers={"User-Agent": APP_USER_AGENT},
                timeout=30,
            )
        except (asyncio.TimeoutError, ClientError) as err:
            raise VimarIpConnectorError(f"Could not connect to gateway {self._duid}: {err}") from err
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def request(
        self,
        function: str,
        *,
        args: list[JsonObject] | None = None,
        params: list[JsonObject] | None = None,
        token: str | None = None,
    ) -> JsonObject:
        """Send a gateway request and wait for its matching response."""
        if self._ws is None:
            raise VimarIpConnectorError("Websocket is not connected")
        self._msgid += 1
        msgid = str(self._msgid)
        payload: JsonObject = {
            "type": "request",
            "function": function,
            "source": self._source_uid,
            "target": self._duid,
            "token": self.session_token if token is None else token,
            "msgid": msgid,
            "args": args or [],
            "params": params or [],
        }
        await self._ws.send_json(payload)
        try:
            while True:
                message = await asyncio.wait_for(self._ws.receive(), timeout=30)
                if message.type == WSMsgType.TEXT:
                    data = _parse_ws_json(message.data)
                    if data is None:
                        continue
                    if str(data.get("msgid") or "") != msgid:
                        continue
                    error = data.get("error")
                    if error not in (None, 0, "0"):
                        raise VimarIpConnectorError(
                            f"{function} returned error {error}: {data.get('result')}"
                        )
                    return data
                if message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.CLOSING):
                    raise VimarIpConnectorError(f"Gateway websocket closed during {function}")
                if message.type == WSMsgType.ERROR:
                    raise VimarIpConnectorError(f"Gateway websocket error during {function}")
        except asyncio.TimeoutError as err:
            raise VimarIpConnectorError(f"Timed out waiting for {function}") from err


def make_source_uid(user_key: str | None) -> str:
    """Return a stable source identifier for HA."""
    digest = hashlib.sha1((user_key or "homeassistant").encode("utf-8")).hexdigest()
    return f"homeassistant-{digest[:12]}"


def _build_discovery_result(
    gateway: VimarIpGateway,
    attach: JsonObject,
    ambient_response: JsonObject | None,
    sf_responses: list[JsonObject],
    status_map: dict[tuple[int, str], JsonObject],
) -> VimarIpDiscoveryResult:
    plant_id = str(attach.get("plantuid") or gateway.plant_id or "")
    plant_name = str(attach.get("plantname") or gateway.name or gateway.id)
    result = VimarIpDiscoveryResult(
        raw={
            "gateway_id": gateway.id,
            "attach": _redact_attach(attach),
            "ambient": ambient_response,
            "sfdiscovery": sf_responses,
        }
    )
    if plant_id:
        result.plants[plant_id] = VimarPlant(id=plant_id, name=plant_name, raw=_redact_attach(attach))

    ambient_names = _ambient_names(ambient_response)
    gateway_device = VimarDevice(
        id=gateway.id,
        name=gateway.name,
        plant_id=plant_id or gateway.plant_id,
        model="Vimar VIEW gateway",
        raw={"duid": gateway.id},
    )
    result.devices[gateway_device.id] = gateway_device

    for category, sf in _iter_sfs_with_category(sf_responses):
        idambient = _safe_int(sf.get("idambient"))
        idsf = _safe_int(sf.get("idsf"))
        if idsf is None:
            continue
        elements = _merged_elements(sf, status_map, idsf)
        platform = _platform_for_sf(category, sf, elements)
        sf_device_id = f"sf:{gateway.id}:{idsf}"
        room_name = ambient_names.get(idambient)
        name = str(sf.get("name") or f"Vimar {idsf}")
        result.devices[sf_device_id] = VimarDevice(
            id=sf_device_id,
            name=name,
            plant_id=plant_id or gateway.plant_id,
            model=_model_name(sf),
            raw={
                "gateway_id": gateway.id,
                "idsf": idsf,
                "idambient": idambient,
                "room": room_name,
                "sftype": sf.get("sftype"),
                "sstype": sf.get("sstype"),
            },
        )
        if platform in {"light", "cover", "climate", "switch"}:
            entity = _main_entity_for_sf(
                gateway,
                sf,
                idsf,
                idambient,
                room_name,
                category,
                platform,
                elements,
                plant_id or gateway.plant_id,
                sf_device_id,
            )
            result.entities[entity.id] = entity
            continue
        for entity in _sensor_entities_for_sf(
            gateway,
            sf,
            idsf,
            idambient,
            room_name,
            category,
            elements,
            plant_id or gateway.plant_id,
            sf_device_id,
        ):
            result.entities[entity.id] = entity
    return result


def _main_entity_for_sf(
    gateway: VimarIpGateway,
    sf: JsonObject,
    idsf: int,
    idambient: int | None,
    room_name: str | None,
    category: str | None,
    platform: str,
    elements: dict[str, JsonObject],
    plant_id: str | None,
    device_id: str,
) -> VimarDataEntity:
    features = _sf_features(gateway, sf, idsf, idambient, room_name, category, elements)
    value = _main_value(platform, features)
    key = f"{device_id}:{platform}"
    return VimarDataEntity(
        id=key,
        name=str(sf.get("name") or f"Vimar {idsf}"),
        platform=platform,
        value=value,
        plant_id=plant_id,
        device_id=device_id,
        category=category or str(sf.get("sftype") or ""),
        path=f"ipconnector.sf.{idsf}",
        enabled=_main_enabled(elements),
        features=features,
        raw=_sf_raw(gateway, sf, idsf, idambient, room_name, category),
    )


def _sensor_entities_for_sf(
    gateway: VimarIpGateway,
    sf: JsonObject,
    idsf: int,
    idambient: int | None,
    room_name: str | None,
    category: str | None,
    elements: dict[str, JsonObject],
    plant_id: str | None,
    device_id: str,
) -> Iterable[VimarDataEntity]:
    base_name = str(sf.get("name") or f"Vimar {idsf}")
    for sfetype, element in elements.items():
        value = _coerce_value(element.get("value"))
        if value in (None, "") or sfetype.startswith("SFE_Cmd_"):
            continue
        platform = "binary_sensor" if _looks_binary(value, sfetype) else "sensor"
        key = f"{device_id}:{sfetype}"
        yield VimarDataEntity(
            id=key,
            name=f"{base_name} {_friendly_sfetype(sfetype)}",
            platform=platform,
            value=value,
            plant_id=plant_id,
            device_id=device_id,
            category=category or str(sf.get("sftype") or ""),
            path=f"ipconnector.sf.{idsf}.{sfetype}",
            enabled=_element_enabled(element),
            features=_sf_features(gateway, sf, idsf, idambient, room_name, category, elements),
            raw={
                **_sf_raw(gateway, sf, idsf, idambient, room_name, category),
                "sfetype": sfetype,
            },
        )


def _sf_features(
    gateway: VimarIpGateway,
    sf: JsonObject,
    idsf: int,
    idambient: int | None,
    room_name: str | None,
    category: str | None,
    elements: dict[str, JsonObject],
) -> JsonObject:
    values = {sfetype: _coerce_value(element.get("value")) for sfetype, element in elements.items()}
    return {
        "gateway_id": gateway.id,
        "idsf": idsf,
        "idambient": idambient,
        "room": room_name,
        "sfcategory": category,
        "sftype": sf.get("sftype"),
        "sstype": sf.get("sstype"),
        "values": values,
        "commands": {sfetype: sfetype for sfetype in elements if sfetype.startswith("SFE_Cmd_")},
    }


def _sf_raw(
    gateway: VimarIpGateway,
    sf: JsonObject,
    idsf: int,
    idambient: int | None,
    room_name: str | None,
    category: str | None,
) -> JsonObject:
    return {
        "gateway_id": gateway.id,
        "idsf": idsf,
        "idambient": idambient,
        "room": room_name,
        "sfcategory": category,
        "sftype": sf.get("sftype"),
        "sstype": sf.get("sstype"),
    }


def _main_value(platform: str, features: JsonObject) -> Any:
    values = features.get("values") or {}
    if platform == "light":
        return _first_non_empty(values, (SFE_STATE_ONOFF, SFE_CMD_ONOFF))
    if platform == "cover":
        return _first_non_empty(
            values,
            (SFE_STATE_SHUTTER, SFE_STATE_SHUTTER_WO_POSITION, SFE_CMD_SHUTTER, SFE_CMD_SHUTTER_WO_POSITION),
        )
    if platform == "climate":
        return _first_non_empty(values, (SFE_STATE_AMBIENT_TEMP, SFE_STATE_CLIMA_TEMP))
    if platform == "switch":
        return _first_non_empty(values, (SFE_STATE_ONOFF, SFE_CMD_ONOFF))
    return None


def _platform_for_sf(category: str | None, sf: JsonObject, elements: dict[str, JsonObject]) -> str:
    joined = " ".join(
        str(item or "")
        for item in (
            category,
            sf.get("sftype"),
            sf.get("sstype"),
            sf.get("name"),
            " ".join(elements),
        )
    ).lower()
    if any(token in joined for token in ("shutter", "curtain", "blind", "slat", "awning", "roller")):
        return "cover"
    if any(token in joined for token in ("clima", "thermo", "hvac", "setpoint")):
        return "climate"
    if any(token in joined for token in ("light", "dimmer", "brightness", "rgb", "hsv", "lamp", "hue")):
        return "light"
    if SFE_CMD_ONOFF in elements or SFE_STATE_ONOFF in elements:
        return "switch"
    return "sensor"


def _model_name(sf: JsonObject) -> str:
    parts = [str(sf.get(key) or "") for key in ("sftype", "sstype")]
    return " / ".join(part for part in parts if part) or "Vimar system function"


def _merged_elements(
    sf: JsonObject,
    status_map: dict[tuple[int, str], JsonObject],
    idsf: int,
) -> dict[str, JsonObject]:
    elements: dict[str, JsonObject] = {}
    for element in _iter_elements(sf):
        sfetype = element.get("sfetype")
        if not sfetype:
            continue
        merged = dict(element)
        status = status_map.get((idsf, str(sfetype)))
        if status:
            merged.update(status)
        elements[str(sfetype)] = merged
    return elements


def _iter_sfs_with_category(sf_responses: list[JsonObject]) -> Iterable[tuple[str | None, JsonObject]]:
    for response in sf_responses:
        category = response.get("_sfcategory")
        args = response.get("args")
        if isinstance(args, list) and args and isinstance(args[0], dict):
            category = args[0].get("sfcategory")
        result_list = response.get("result") or []
        for result in result_list:
            if not isinstance(result, dict):
                continue
            for sf in result.get("sf") or []:
                if isinstance(sf, dict):
                    yield str(category) if category else None, {**sf, "idambient": result.get("idambient")}


def _iter_sfs(sf_responses: list[JsonObject]) -> Iterable[JsonObject]:
    for _category, sf in _iter_sfs_with_category(sf_responses):
        yield sf


def _iter_elements(sf: JsonObject) -> Iterable[JsonObject]:
    for element in sf.get("elements") or []:
        if isinstance(element, dict):
            yield element


def _ambient_names(ambient_response: JsonObject | None) -> dict[int, str]:
    names: dict[int, str] = {}
    if not ambient_response:
        return names
    for item in ambient_response.get("result") or []:
        if not isinstance(item, dict):
            continue
        ambient_id = _safe_int(item.get("idambient"))
        name = item.get("name")
        if ambient_id is not None and name:
            names[ambient_id] = str(name)
    return names


def _first_result(response: JsonObject) -> JsonObject:
    result = response.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return result[0]
    raise VimarIpConnectorError("Gateway response did not contain a result object")


def _parse_ws_json(data: str) -> JsonObject | None:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        _LOGGER.debug("Ignoring non-JSON Vimar websocket payload: %s", data[:120])
        return None
    return parsed if isinstance(parsed, dict) else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if value not in (None, ""):
        return [str(value)]
    return []


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        number = _maybe_number(value)
        return number if number is not None else value
    return value


def _maybe_number(value: str) -> int | float | None:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _first_non_empty(values: JsonObject, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _looks_binary(value: Any, hint: str) -> bool:
    if isinstance(value, bool):
        return True
    normalized = str(value).strip().lower()
    if normalized in TRUTHY_STATES or normalized in FALSEY_STATES:
        return True
    return any(token in hint.lower() for token in ("alarm", "lock", "openwindow", "onoff", "presence"))


def _main_enabled(elements: dict[str, JsonObject]) -> bool | None:
    states = [_element_enabled(element) for element in elements.values()]
    states = [state for state in states if state is not None]
    if not states:
        return None
    return any(states)


def _element_enabled(element: JsonObject) -> bool | None:
    value = element.get("enable")
    return value if isinstance(value, bool) else None


def _friendly_sfetype(sfetype: str) -> str:
    label = sfetype
    for prefix in ("SFE_State_", "SFE_Cmd_", "SFE_Synoptic_"):
        label = label.removeprefix(prefix)
    return label.replace("_", " ")


def _redact_attach(attach: JsonObject) -> JsonObject:
    redacted = dict(attach)
    for key in ("password", "token"):
        if key in redacted:
            redacted[key] = "***"
    return redacted
