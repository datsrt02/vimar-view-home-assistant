# Vimar VIEW custom component for Home Assistant

This custom component is based on the reverse-engineered Vimar VIEW Android app.

## What works in this first build

- Links a Vimar VIEW account through the same OpenID/AppAuth flow used by the Android app.
- Stores and refreshes bearer tokens through Home Assistant config entries.
- Syncs plants, associations, cloud devices, extracted datapoints, and routines.
- Creates:
  - `sensor` entities for numeric/string datapoints
  - `binary_sensor` entities for boolean/status datapoints
  - `button` entities for cloud routines
- Adds services:
  - `vimar_view.refresh`
  - `vimar_view.execute_routine`

## Known limitation

Direct control of individual Vimar lights, relays, shutters, climate devices, and anti-intrusion functions uses the Android app's proprietary IPConnector WebSocket protocol:

`wss://prod.vimar.cloud/wssmqtt/deviceproxy?duid=<DUID>&access_token=<ACCESS_TOKEN>`

This build prepares the account/session/device model, but direct IPConnector action frames are not implemented yet. Routines are executable because the cloud REST endpoint is known.

## Install

Copy the `vimar_view` directory into:

`<home_assistant_config>/custom_components/vimar_view`

Restart Home Assistant, then add the integration from Settings > Devices & services > Add integration > Vimar VIEW.

## Login flow

The Android manifest registers this redirect URI:

`com.prova.app:/oauth2redirect/example-provider`

The app build config also references a legacy-looking variant:

`com.prova.app:/oauth2redirect/example-provide`

Home Assistant cannot receive that mobile-app URI directly, so the config flow shows an authorization URL. Open it in a desktop browser, sign in, and paste the final `com.prova.app:/...` redirect URL back into Home Assistant. If the browser shows a blank or unreachable page after login, copy the address from the address bar. If the first URL does not redirect, use the alternate URL shown in the config flow.
