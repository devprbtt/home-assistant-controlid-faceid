# Home Assistant Control iD FaceID

Home Assistant custom integration for Control iD FaceID and Access devices with:

- Config Flow setup from the UI
- Automatic device monitor configuration through `set_configuration.fcgi`
- Live webhook handling for `/dao` and `/secbox`
- Door state binary sensor
- Last access sensor with event mapping and friendly user-name mapping
- Gate open button using `execute_actions.fcgi`
- Automatic session re-login when the device expires the current session

## Features

This integration creates:

- A `button` entity to open the gate
- A `binary_sensor` entity for door state from `secbox.open`
- A `sensor` entity for the last access event from `dao`

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

1. Copy this repository's `custom_components/controlid_faceid` folder into your Home Assistant `custom_components` directory.
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

## Integration Options

After setup, open the integration options to configure:

- `user_map`: a JSON or Python-style dictionary mapping `user_id` to friendly name

Example:

```python
{"1000009": "Irlan", "1000010": "Maria"}
```

## Notes

- The default `SecBox ID` is `65793`.
- Gate opening uses the action payload `reason=3`.
- Authenticated requests automatically retry once if the device reports session expiration.

## Project Structure

```text
custom_components/
  controlid_faceid/
    __init__.py
    manifest.json
    config_flow.py
    button.py
    binary_sensor.py
    sensor.py
```
