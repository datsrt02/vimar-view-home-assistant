"""Constants for the Vimar VIEW integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "vimar_view"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.BUTTON,
]

CONF_CALLBACK_URL = "callback_url"
CONF_DISCOVERY = "discovery"
CONF_TOKEN = "token"
CONF_USER = "user"

DATA_API = "api"
DATA_COORDINATOR = "coordinator"

APP_CLIENT_ID = "mobile-user-view2"
APP_SCOPE = "openid"
APP_REDIRECT_URI = "com.prova.app:/oauth2redirect/example-provider"
APP_LEGACY_REDIRECT_URI = "com.prova.app:/oauth2redirect/example-provide"
APP_REDIRECT_URIS = (APP_REDIRECT_URI, APP_LEGACY_REDIRECT_URI)

BASE_URL = "https://prod.vimar.cloud/"
CLOUD_WSS_BASE_URL = "wss://prod.vimar.cloud"
DYNAMIC_LINK_BASE_URL = "https://app.vimar.cloud/"
IN_APP_PURCHASE_URL = "https://api.vimar.cloud/v1/"
EXTERNAL_CLOUD_RESOURCE_URL = "https://static.vimar.cloud/appview/locales/"
OIDC_DISCOVERY_URL = (
    "https://prod.vimar.cloud/auth/realms/vimaruser/.well-known/"
    "openid-configuration"
)

APP_USER_AGENT = "VIEW / 2.16.2 (1260324852) - home-assistant - Android"
DEFAULT_SCAN_INTERVAL_SECONDS = 300

MAX_DEVICE_FETCH_CONCURRENCY = 8
