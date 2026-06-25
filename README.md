# Vimar VIEW custom component for Home Assistant

This custom component is based on the reverse-engineered Vimar VIEW Android app.

## What works

- Links a Vimar VIEW account through the same OpenID/AppAuth flow used by the Android app.
- Stores and refreshes bearer tokens through Home Assistant config entries.
- Syncs plants, associations, cloud devices, routines, and IPConnector system functions from each gateway.
- Creates:
  - `sensor` entities for numeric/string datapoints
  - `binary_sensor` entities for boolean/status datapoints
  - `light` entities for Vimar/Philips HUE light system functions
  - `cover` entities for shutters, curtains, blinds, and slats
  - `climate` entities for thermostat/climate zones
  - `switch` entities for relay/on-off functions
  - `button` entities for cloud routines
- Sends basic IPConnector `doaction` commands for supported light, cover, switch, and climate setpoint entities.
- Adds services:
  - `vimar_view.refresh`
  - `vimar_view.execute_routine`

## IPConnector

Individual Vimar lights, relays, shutters, curtains, and climate devices are discovered through the Android app's proprietary IPConnector WebSocket protocol:

`wss://prod.vimar.cloud/wssmqtt/deviceproxy?duid=<DUID>&access_token=<ACCESS_TOKEN>`

The integration attaches to each gateway, runs `ambientdiscovery`, `sfdiscovery`, and `getstatus`, then maps each system function to a Home Assistant platform where possible.

## Install

Copy the `vimar_view` directory into:

`<home_assistant_config>/custom_components/vimar_view`

Restart Home Assistant, then add the integration from Settings > Devices & services > Add integration > Vimar VIEW.

## Login flow

The integration signs in with the same Vimar VIEW account used by the mobile app. It uses the Vimar public mobile client (`mobile-user-view2`) to request tokens directly from the Vimar OpenID token endpoint. The account password is used only for the token request and is not stored in Home Assistant.

## Manual OAuth fallback

The Android manifest registers this redirect URI:

`com.prova.app:/oauth2redirect/example-provider`

The app build config also references a legacy-looking variant:

`com.prova.app:/oauth2redirect/example-provide`

Home Assistant cannot receive that mobile-app URI directly, so the config flow shows an authorization URL. Open it in a desktop browser, sign in, and paste the final `com.prova.app:/...` redirect URL back into Home Assistant. If the browser shows a blank or unreachable page after login, copy the address from the address bar. If the first URL does not redirect, use the alternate URL shown in the config flow.
