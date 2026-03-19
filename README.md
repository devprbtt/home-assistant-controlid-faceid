# Home Assistant Control iD FaceID

Home Assistant custom integration for Control iD FaceID and Access devices with:

- Config Flow setup from the UI
- Automatic device monitor configuration through `set_configuration.fcgi`
- Live webhook handling for `/dao` and `/secbox`
- Direct door-state read on startup with webhook fallback
- Door state binary sensor
- Last access sensor with event mapping and friendly user-name mapping
- Last authorized user sensor
- Registered users count sensor
- Gate open button using `execute_actions.fcgi`
- Sync Users button to import names directly from the device
- Automatic session re-login when the device expires the current session

## Features

This integration creates:

- A `button` entity to open the gate
- A `button` entity to sync users from the device
- A `binary_sensor` entity for door state
- A `sensor` entity for the latest access event
- A `sensor` entity for the latest authorized user
- A `sensor` entity for total registered users on the device

The access sensor maps:

- `7` -> `Authorized`
- `11` -> `Door Opened`

You can also configure a `user_map` in the integration options to map device `user_id` values to friendly names like:

```json
{
  "1000009": "Irlan",
  "1000010": "Maria"
}
```

## Installation

1. Copy this repository's `custom_components/controlid` folder into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Go to `Settings` -> `Devices & Services` -> `Add Integration`.
4. Search for `Control iD FaceID`.
5. Enter:
   - Device IP
   - Username
   - Password
   - Optional `SecBox ID`

## Requirements

- Home Assistant must have an `Internal URL` or `External URL` configured.
- The Control iD device must be able to reach your Home Assistant webhook URL over the network.
- The device must allow API access with the configured credentials.

## Webhook Behavior

Control iD appends suffixes to the configured monitor path:

- `/dao` for access log events
- `/secbox` for relay and door-state events

This integration handles both:

- `/api/webhook/<id>/dao`
- `/api/webhook/<id>/secbox`

It also accepts the base webhook path and auto-detects payload type when possible.

Depending on firmware or product family, the integration also accepts `door`-style payloads for door-state updates.

## Integration Options

After setup, open the integration options to configure:

- `user_map`: a JSON or Python-style dictionary mapping `user_id` to friendly name

Example:

```python
{"1000009": "Irlan", "1000010": "Maria"}
```

## Startup Behavior

On startup, the integration attempts to populate current state immediately so entities do not remain `unknown` waiting for the next webhook.

It will:

- Load registered users count from the device
- Load the latest access log for `Last Access`
- Load the latest authorized access log (`event = 7`) for `Last Access User`
- Read the current door state directly from `door_state.fcgi` when supported
- Fall back to the latest door/secbox event if direct door-state reading is not available

## User Sync

The `Sync Users` button imports registered users from the device using `load_objects.fcgi` and stores them in the integration `user_map`.

After syncing:

- `Last Access User` will show the friendly name when available
- `Last Access` attributes will include `user_name` and `user_display`
- `Registered Users` will show the total number of users loaded from the device

`Last Access User` tracks the latest authorized access only. It does not switch to `user_id = 0` on later `Door Opened` events.

## Updating

If you are inside Home Assistant's `custom_components` folder, update the integration with:

```bash
rm -rf controlid && git clone --depth 1 https://github.com/devprbtt/home-assistant-controlid-faceid.git temp-controlid && mv temp-controlid/custom_components/controlid ./controlid && rm -rf temp-controlid
```

Then restart Home Assistant.

## Notes

- The default `SecBox ID` is `65793`.
- Gate opening uses the action payload `reason=3`.
- Authenticated requests automatically retry once if the device reports session expiration.

## Project Structure

```text
custom_components/
  controlid/
    __init__.py
    manifest.json
    config_flow.py
    button.py
    binary_sensor.py
    sensor.py
```
