# MirrorDash User Guide

The admin page explains most settings where you change them. This guide covers what it can't: setting the mirror up before the admin page is reachable, wiring, the rules for when the screen is on, recovery, and Home Assistant.

- [1. Reaching the mirror](#1-reaching-the-mirror)
- [2. First start: Wi-Fi setup](#2-first-start-wi-fi-setup)
- [3. Where things go on the screen](#3-where-things-go-on-the-screen)
- [4. When the screen is on](#4-when-the-screen-is-on)
- [5. Connecting hardware](#5-connecting-hardware)
- [6. Updates, backups and recovery](#6-updates-backups-and-recovery)
- [7. Home Assistant](#7-home-assistant)
- [8. When something is wrong](#8-when-something-is-wrong)
- [9. How the SD card is protected](#9-how-the-sd-card-is-protected)

---

## 1. Reaching the mirror

Open **`http://mirrordash.local/admin`** on a phone or computer on the same Wi-Fi as the mirror. If your device can't find `mirrordash.local`, use the mirror's IP address instead (shown in your router).

The first time, you choose an admin password. Forgot it? Tap **Forgot password?** on the login page: the mirror shows a 6-digit PIN on its screen, and with it you set a new password.

> **Tip:** Put the admin page on your phone's home screen, so it opens like an app. iPhone: open it in Safari, tap *Share* → *Add to Home Screen*. Android: open it in Chrome, tap the menu (⋮) → *Add to Home screen*.

---

## 2. First start: Wi-Fi setup

A new mirror (or one that can't find its Wi-Fi) starts its own network for the setup:

1. **Join `MirrorDash-Setup`** on your phone. The mirror shows its password with a QR code: point the camera at it, or type the password. It stays the same until the SD card is flashed again.
2. **Sign in**: the phone says you need to sign in to the network, like on a hotel Wi-Fi. Tap it and the setup page opens. If it doesn't ask, open `http://mirrordash.setup`.
3. **Choose your Wi-Fi**, type its password (*Show* lets you check it) and tap **Connect the mirror**.
4. The mirror shows its clock in a moment. Put your phone back on your home Wi-Fi and open `mirrordash.local`. If the password was wrong, `MirrorDash-Setup` comes back within a minute: join it and try again.

The time zone is taken from your phone. It also sets the country for Wi-Fi (which channels may be used) from the next restart; until then, networks on channel 12 or 13 don't show up.

---

## 3. Where things go on the screen

The screen has nine areas: top, middle and bottom, each left, center and right. Each module has a position in its settings (Modules tab → **Configure**).

*   Several modules in the same area are stacked.
*   A module can be added more than once, for example two clocks in different time zones, each with its own settings.
*   **Taking turns (carousel):** give modules in the same area the same **Carousel Group** name, and they show one at a time, switching every **Carousel Interval** seconds. Modules in that area without the group name stay stacked as usual.

---

## 4. When the screen is on

The Power tab chooses *Always on*, *On a schedule* or *Off until woken*. Outside the schedule, and in *Off until woken*, the screen can still be woken for a while:

*   What wakes it: someone in front of the mirror (with a motion or presence sensor, if *Wake when someone is in front of the mirror* is on), a button press set to *Wake the screen*, **Turn Screen ON**, or Home Assistant (see [7](#7-home-assistant)).
*   *From the last activity* keeps it on while someone is still there; *A fixed time* turns it off after exactly that time, for example for a doorbell.
*   Turning the screen off by hand keeps it off until something wakes it again or the schedule starts.

Always use **Shut Down** (Power tab) before unplugging the mirror. To start it again, unplug the power and plug it back in, or use a button on GPIO 3 (below).

---

## 5. Connecting hardware

Add buttons, sensors and a fan in the Hardware tab under *Sensors & Inputs*; it shows how to wire each one. Some things it doesn't say:

*   Something new is used after the mirror restarts; the card then shows a **Restart Mirror** button.
*   A **push button on GPIO 3** (pin 5) also starts the mirror again after it has been shut down. With *Shut down the mirror* on its long press, the button works as an on/off switch. GPIO 3 is also used by the light sensor, so the two can't be combined.
*   An **on/off fan** (2 or 3 wires) needs a transistor or MOSFET between the GPIO and the fan, never the GPIO alone. A 4-wire PWM fan is connected directly.
*   **Brightness on an HDMI screen** is sent over the cable (DDC/CI). Most computer monitors understand it, most TVs don't. Some monitors have it turned off in their own menu: turn it on there and move the slider again.

---

## 6. Updates, backups and recovery

*   **Updates** are in the Settings tab. *Test versions* gets new versions before everyone else; they can have bugs. After an update the mirror's screen reloads by itself; **Reload Screen** (Power tab) does the same by hand.
*   **If an update doesn't start**, the mirror goes back to the version before by itself and shows *System Restored*. If even that fails, it starts in *Safe Mode* without extra modules (the mirror shows *Safe Mode Active*): use **Rebuild Active Environment** in the admin page's banner to install everything again.
*   **A backup** (Backup tab) contains your settings, modules and their data, the hardware settings and the Home Assistant token. It doesn't contain the admin password or the Wi-Fi. Restoring keeps the mirror's admin password, installs the modules again (this needs internet) and restarts.

---

## 7. Home Assistant

Home Assistant can show the mirror's status and sensors, turn its screen on and off (for example when nobody is home) and set its brightness.

1.  **Create a token**: in the admin page, *Settings* → *Home Assistant & API* → **Create Token**, and copy it.
2.  **Save it in Home Assistant**: add this line to `secrets.yaml`, with `Bearer`, a space and your token:
    ```yaml
    mirrordash_token: "Bearer paste-your-token-here"
    ```
3.  **Add the mirror**: paste this into `configuration.yaml` and restart Home Assistant. Delete the sensors you don't have connected (temperature, humidity, light, motion). If Home Assistant can't find `mirrordash.local`, use the mirror's IP address instead.

```yaml
rest:
  - resource: http://mirrordash.local/api/v1/status
    headers:
      Authorization: !secret mirrordash_token
    scan_interval: 30
    sensor:
      - name: "Mirror CPU temperature"
        value_template: "{{ value_json.cpu_temperature_c }}"
        unit_of_measurement: "°C"
        device_class: temperature
      - name: "Mirror brightness"
        value_template: "{{ value_json.brightness }}"
        unit_of_measurement: "%"
      - name: "Mirror room temperature"
        value_template: "{{ value_json.sensors.temperature_c }}"
        unit_of_measurement: "°C"
        device_class: temperature
      - name: "Mirror humidity"
        value_template: "{{ value_json.sensors.humidity }}"
        unit_of_measurement: "%"
        device_class: humidity
      - name: "Mirror light"
        value_template: "{{ value_json.sensors.lux }}"
        unit_of_measurement: "lx"
        device_class: illuminance
    binary_sensor:
      - name: "Mirror screen on"
        value_template: "{{ value_json.screen_on }}"
      - name: "Mirror motion"
        value_template: "{{ value_json.sensors.motion }}"
        device_class: motion

rest_command:
  mirror_screen:
    url: http://mirrordash.local/api/v1/screen
    method: POST
    headers:
      Authorization: !secret mirrordash_token
    content_type: application/json
    payload: '{"state": "{{ state }}"}'
  mirror_brightness:
    url: http://mirrordash.local/api/v1/brightness
    method: POST
    headers:
      Authorization: !secret mirrordash_token
    content_type: application/json
    payload: '{"value": {{ value | int }}}'

switch:
  - platform: template
    switches:
      mirror_screen:
        friendly_name: "Mirror screen"
        value_template: "{{ is_state('binary_sensor.mirror_screen_on', 'on') }}"
        turn_on:
          action: rest_command.mirror_screen
          data:
            state: "on"
        turn_off:
          action: rest_command.mirror_screen
          data:
            state: "off"

template:
  - number:
      - name: "Mirror screen brightness"
        state: "{{ states('sensor.mirror_brightness') | int(100) }}"
        min: 10
        max: 100
        step: 10
        unit_of_measurement: "%"
        set_value:
          - action: rest_command.mirror_brightness
            data:
              value: "{{ value }}"
```

What the API answers, for your own scripts (all with the header `Authorization: Bearer <token>`):

| Call | What it does |
|------|--------------|
| `GET /api/v1/status` | Version, uptime, CPU temperature, `screen_on`, `brightness`, running modules and `sensors` (`temperature_c`, `humidity`, `lux`, `motion`, `fan_level`, for what is connected). |
| `POST /api/v1/screen` `{"state": "on"}` | Wakes the screen for the screen timeout; add `"timeout_minutes": 2` for two minutes. `{"state": "off"}` turns it off. |
| `POST /api/v1/brightness` `{"value": 60}` | Sets and saves the brightness (10–100). |

A wrong or missing token gets `401`. (The older `POST /admin/screen` still works without a token, so existing automations keep running.)

---

## 8. When something is wrong

*   **"Power Warning: Under-voltage Detected" on the dashboard:** the power supply is too weak. The mirror slows down and the SD card can be damaged. Use the official Raspberry Pi power supply (5.1 V, 2.5 A for a Pi 3).
*   **Modules are empty or missing:** check the mirror's internet connection, and the Logs tab for errors.
*   **"MirrorDash isn't responding" on the mirror:** it lost contact with its software for more than two minutes and keeps trying. If it doesn't come back, use **Restart MirrorDash** in the Power tab, or unplug and plug in the mirror.
*   **"MirrorDash didn't start" on the mirror:** unplug it and plug it in again. If it keeps happening, the SD card may need a fresh MirrorDash image (restore your backup afterwards).
*   **The screen looks wrong:** **Reload Screen** in the Power tab.

---

## 9. How the SD card is protected

The system part of the SD card is read-only: everything written there during use is gone at the next start, so the system can't wear out or slowly break. Your settings, modules and Wi-Fi are kept on a separate storage partition. That is the part a sudden power cut can damage, which is why you should use **Shut Down** before unplugging.
