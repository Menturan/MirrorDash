# MirrorDash User Guide

Welcome to MirrorDash! This guide is designed for end-users and mirror administrators to help you set up, customize, and manage your smart mirror display. You do **not** need to be a programmer or software developer to follow this guide.

## Table of Contents

- [1. What is MirrorDash?](#1-what-is-mirrordash)
- [2. Accessing the System](#2-accessing-the-system)
- [3. Initial Setup & Security](#3-initial-setup--security)
- [4. Using the Admin Dashboard](#4-using-the-admin-dashboard)
- [5. Screen Layout & Module Stacking](#5-screen-layout--module-stacking)
- [6. Setting Up Carousels (Switching Modules)](#6-setting-up-carousels-switching-modules)
- [7. Troubleshooting FAQ](#7-troubleshooting-faq)
- [8. Network Setup (WiFi Captive Portal)](#8-network-setup-wifi-captive-portal)
- [9. Failsafe Operation & SD Card Preservation](#9-failsafe-operation--sd-card-preservation)
- [10. Home Assistant](#10-home-assistant)

---

## 1. What is MirrorDash?

MirrorDash is an ambient heads-up display (HUD) designed to run on a screen behind a semi-reflective two-way mirror (often powered by a Raspberry Pi). 

*   **Ambient Display**: It is designed to be a passive, glanceable information screen (e.g. showing the time, calendar, Swedish name days, weather) — not an interactive tablet.
*   **The Zero-Light Philosophy**: The background is solid black. In a physical mirror, black areas behave as a standard reflective mirror, while the white/gray text and symbols float on the glass.

---

## 2. Accessing the System

Once your mirror server is running, you can access it via a web browser on any device (phone, tablet, or computer) connected to the same local Wi-Fi network:

*   **Mirror Display**: `http://mirrordash.local/`
*   **Admin Dashboard**: `http://mirrordash.local/admin`

> **Tip:** The `.local` address works automatically on macOS, Linux, and Windows 10/11 without any configuration. If it doesn't resolve on your device, fall back to the IP address: `http://<your-pi-ip>/admin`.

> **Tip:** Put the admin page on your phone's home screen, so it opens like an app. iPhone: open `http://mirrordash.local/admin` in Safari, tap *Share* → *Add to Home Screen*. Android: open it in Chrome, tap the menu (⋮) → *Add to Home screen*. (A full app install needs https, which a mirror on the home network doesn't have, so Android opens it in a browser tab.)

---

## 3. Initial Setup & Security

The first time you open the **Admin Dashboard**, you will be prompted to secure your mirror:

1.  **Set Admin Password**: Choose a secure password. This password will protect your configuration and system controls.
2.  **API Authentication**: Background scripts and automated backup utilities access the system by providing your admin password in the `X-API-Key` HTTP header.

For subsequent visits, simply log in using your admin password.

---

## 4. Using the Admin Dashboard

The Admin Dashboard is organized into seven main tabs:

### 4.1. Dashboard Tab
Provides a high-level overview of the mirror's current status:
*   **Sensors**: Temperature and humidity (DHT11), light level (BH1750) and the fan, if they are connected (see the Hardware tab). Updates every 30 seconds.
*   **Screen Layout Matrix**: A 3x3 grid showing which screen regions (Top Left, Top Center, etc.) are currently occupied by active module instances.
*   **System Analytics**: Real-time telemetry cards showing CPU temperature, persistent storage disk usage, system memory (RAM) usage, active modules running, local IP address, network connection status (SSID and signal strength), system uptime, and NTP time synchronization status.
*   **Power Throttling Warning**: Displayed automatically if Raspberry Pi under-voltage is detected (dropping below 4.63V), warning the user of potential system instability or file system corruption.
*   **Update Banners**: Displayed dynamically if there is a pending update to the core software or any installed module. Clicking the banner redirects you directly to the correct management tab (System Settings or Modules) to trigger the upgrade.

### 4.2. Modules Tab
This is where you manage the widgets displayed on your mirror.
*   **Root Partition Storage (Virtual Env)**: Displays a real-time disk usage gauge showing the total, used, and free space on the system's root partition. Since modules and their dependencies are installed in the A/B virtual environments, this gauge helps you monitor the 6GB boundary. A warning will appear if free space drops below 500MB.
*   **Failsafe Recovery & Rebuild**: If the system is running in rollback mode or Safe Mode due to a startup crash, a warning banner will be displayed at the top of the Admin dashboard. You can click the **Rebuild Active Environment** button to trigger a fresh rebuild of the virtual environment, reinstalling the core system and configured modules.
*   **Active Modules**: Lists all currently running widgets. You can click **Configure** next to any active module to adjust its settings (e.g., changing refresh intervals, adding calendar URLs, or toggling headers).
*   **Install New Modules**: Search the community module database. Click **Details** on any module to read its setup guide and view screenshots. Click **Install** to add it to your system.
*   **Uninstalling**: If you no longer need a module, click **Uninstall** to cleanly remove it from the system and configuration.

### 4.3. Configuration Tab
Controls global settings shared by all modules. Adjust these to localize your mirror:
*   **Language**: Set display language (e.g., `en` for English, `sv` for Swedish).
*   **Timezone**: Your region's timezone identifier (e.g., `Europe/Stockholm`).
*   **Time Format**: Choose between `24h` or `12h` display.
*   **Units**: Change temperature units (`C` or `F`) and distance (`km` or `mi`).
*   **Coordinates**: Latitude and longitude (used by weather modules to locate your mirror).
*   **MirrorDash Updates**: Check for a newer version and install it. The mirror restarts on the new version, and goes back to the old one by itself if the new one doesn't start.
*   **Test versions**: Turn on to get new versions before everyone else, to try them out. They can have bugs; turn it off to wait for the regular release.

### 4.4. Hardware Tab
Changes are applied as soon as you make them; there is no Apply button. (Turning SSH on waits until you have entered the new password.)
*   **Screen Rotation**: Rotate the screen layout (`normal`, `left`, `right`, or `inverted`) to support portrait-oriented mirrors.
*   **Screen Resolution**: Set display resolution or keep it on `auto`.
*   **Screen Brightness**: How bright the screen is (10% to 100%). A screen on the Pi's ribbon cable (DSI) sets its backlight. An HDMI screen gets the setting over the cable (DDC/CI), which most computer monitors understand and most TVs don't; if yours doesn't, the setting says so and you use the screen's own buttons. (Needs an OS image newer than 0.5.0.)
*   **System Volume**: Control mirror audio output levels.
*   **Sensors & Inputs**: Everything connected to the GPIO header, as a list. Choose **Connect something new**, pick the type and the GPIO (or the I²C address), and follow the wiring hint shown under it. Up to four push buttons can be connected, everything else once:
    *   *Push button* (between a GPIO and GND): choose what a *single*, *double*, *triple* and *long* press does: wake the screen, turn the screen on/off, restart MirrorDash, restart the mirror, or shut it down. *Long press is* sets how long the button is held for a long press (1, 1.5, 2 or 3 seconds; 1.5 to begin with). Each button has its own settings, and changes take effect on the next press. (A second, third and fourth button need an OS image newer than 0.5.0.)
        *   *Tip*: on **GPIO 3 (pin 5)** the same button also starts the mirror again after it has been shut down (the Raspberry Pi wakes up when GPIO 3 is connected to GND). With *Shut down the mirror* on a long press, the button works as an on/off switch. GPIO 3 is also the I²C clock line, so it can't be combined with the light sensor.
    *   *PIR motion sensor* and *mmWave presence sensor* (e.g. LD2410; it also notices someone standing still): used by the Power tab to turn the screen on and off.
    *   *DHT11 temperature & humidity sensor* and *BH1750 light sensor* (I²C, uses GPIO 2 and 3): their readings appear on the Dashboard.
    *   *Fan*: cools the Pi by CPU temperature; you choose the temperature it starts at. An *on/off* fan (2 or 3 wires) needs a transistor or MOSFET between the GPIO and the fan, never the GPIO alone; it turns off again 5 °C lower. A *PWM fan* (4 wires) gets its speed from the GPIO and speeds up in steps as the Pi gets warmer. The fan's state is shown on the Dashboard.
*   Adding or removing something takes effect after the mirror restarts; the card then shows a **Restart Mirror** button. The status line at the bottom shows whether each part is detected, its latest reading, or the last motion.
*   **API Access**: a token for Home Assistant and other systems (see [10. Home Assistant](#10-home-assistant)). **Create Token** shows it once; copy it right away. A new token replaces the old one, and **Remove Token** shuts the API. The token can read the status and run the screen, nothing else, and it is kept in backups.

### 4.5. Power Tab
Changes are applied as soon as you make them.
*   **Mirror Power**: **Restart Mirror** and **Shut Down**. Always shut down before unplugging the power, so the SD card can't be damaged. To start the mirror again, unplug the power and plug it back in.
*   **When is the screen on?**
    *   *Always on*: the screen is on, unless you turn it off yourself.
    *   *On a schedule*: on between a start and an end time (e.g. `07:00`–`22:30`). Outside them the screen is off, but it can still be woken.
    *   *Off until woken*: the screen is off and only lights up when something wakes it. Saves the most energy and screen life.
*   **Waking the screen** (for the schedule and "off until woken"): something wakes the screen for a number of minutes, then it turns off again by itself.
    *   *From the last activity* (recommended): every new movement or call starts the countdown again, and it waits while someone is still in front of the mirror.
    *   *A fixed time*: the screen stays on exactly that long after it was woken, even if someone is still there. Good for showing something briefly, like a doorbell.
    *   What wakes it: someone in front of the mirror (with a PIR or mmWave sensor, if *Wake when someone is in front of the mirror* is on), the push button (a press set to *Wake the screen*), the **Turn Screen ON** button, and other systems such as Home Assistant:
        *   `POST http://mirrordash.local/admin/screen` with `{"state": "on"}` wakes it for the chosen time, `{"state": "on", "timeout_minutes": 2}` for 2 minutes, and `{"state": "off"}` turns it off right away.
    *   Turning the screen off by hand keeps it off until something wakes it again or the schedule starts.
*   **Screen Power**: Instantly turn the mirror display output ON or OFF. (Manually overriding automation states will temporarily trigger that state).

### 4.6. Backup Tab
Protect your configurations and personal data files:
*   **Create Backup**: Saves a `.mirror` file with all your settings, your modules (and the data they keep), and the hardware settings. You can protect it with a password. The admin password and saved Wi-Fi networks are not included.
*   **Restore Backup**: Upload a backup file, for example on a freshly flashed mirror, to get that setup back. The mirror keeps its current admin password, installs the modules again (this needs internet) and restarts. If the backup has a push button, sensors or a fan, the whole mirror restarts so the pins take effect.

### 4.7. Logs Tab
Displays real-time system logs. If a module fails to fetch data or the screen behaves unexpectedly, open this tab to inspect the error messages.

---

## 5. Screen Layout & Module Stacking

The mirror display is split into a **3x3 Grid** with nine regions:
```
+---------------+-----------------+---------------+
|   top_left    |   top_center    |   top_right   |
+---------------+-----------------+---------------+
|  middle_left  |  middle_center  | middle_right  |
+---------------+-----------------+---------------+
|  bottom_left  |  bottom_center  | bottom_right  |
+---------------+-----------------+---------------+
```
*   **Default Stacking**: If you assign multiple modules to the same position (e.g., both Clock and Name Day to `top_right`), they will stack vertically.
*   **Multiple Instances**: You can add and run multiple instances of a module on the mirror. For example, you can add two separate clock modules with different positions, different timezone offsets, or different formatting. Each instance can be configured independently and has isolated data directories.
*   **Center Void**: By default, the `middle_center` region is kept empty to preserve the physical reflective surface of the mirror.

---

## 6. Setting Up Carousels (Switching Modules)

If you have many modules but limited screen space, you can group modules in the same region to automatically cycle (cross-fade) on a timer instead of stacking.

### How to set it up:
1.  Go to the **Modules** tab on the Admin Dashboard.
2.  Click **Configure** on the first module you want to cycle (e.g. `mirrordash-calendar`).
3.  Set the **Position** (e.g. `middle_left`).
4.  Add a **Carousel Group** name (e.g., `left-cycle`).
5.  Set a **Carousel Interval** (e.g., `20` to rotate every 20 seconds).
6.  Click **Save**.
7.  Repeat this for the other modules you want in the loop (e.g., `mirrordash-weather`), using the **exact same** Position and Carousel Group name.

All other modules in that region (e.g., a Todo list with no group name) will stack normally, while your grouped modules cycle smoothly in place.

---

## 7. Troubleshooting FAQ

### The mirror display is blank or only shows a spinner
*   Check if the server is running.
*   Open the **Logs** tab in the Admin panel to check for errors.
*   Verify that your device is connected to the internet if modules depend on external feeds (like calendar files).

### System settings (brightness/rotation) are not applying
*   Brightness on an HDMI screen only works if the screen understands DDC/CI; the Hardware tab says when it doesn't. Some monitors have DDC/CI turned off in their own menu: turn it on there and move the slider again.
*   On Raspberry Pi, the system volume and brightness controls require administrative hardware privileges. Ensure your user has permissions to run system control scripts.

### I forgot my admin password. How do I reset it?
If you forget your admin password, you can reset it securely without resetting your entire configuration:

#### Option A: Direct Web Recovery (Recommended for most users)
1. Go to the Admin Dashboard login page in your browser.
2. Click the **Forgot password?** link under the password input field.
3. Confirm the prompt to initialize recovery. This will immediately display a 6-digit Recovery PIN on your physical mirror screen (reloading the display if necessary).
4. Enter this 6-digit Recovery PIN into the recovery prompt in your web browser and set a new password.

#### Option B: Manual Command-Line Reset (For developers/system administrators)
1. Connect to your mirror via SSH (or access the terminal on the device).
2. Open the active configuration file:
   ```bash
   nano ~/.mirrordash/data/config.json
   ```
3. Locate the `"admin_auth"` section at the top of the file:
   ```json
   "admin_auth": {
     "hash": "...",
     "salt": "..."
   },
   ```
4. Delete the entire `"admin_auth"` block (making sure the remaining JSON is syntactically valid) and save the file.
5. Restart the server or reboot the mirror. The next time you open the Admin Dashboard in your browser, you will be prompted to set a new password during the first-run setup wizard.

---

## 8. Network Setup (WiFi Captive Portal)

MirrorDash is designed to be a plug-and-play appliance. If you move your mirror to a new network or boot it for the first time without configuring WiFi, the system enters **Captive Portal fallback mode** automatically.

1. **Connect to Hotspot**: On your phone or computer, open WiFi settings and look for the network named **`MirrorDash-Setup`**.
2. **Enter Setup Password**: The mirror shows its own password for this network, with a QR code. Point your phone's camera at the code to join without typing, or type the password shown. It stays the same for this mirror until its SD card is flashed again.
3. **Sign in**: Your phone says you need to sign in to the network (like on a hotel Wi-Fi). Tap it and the setup page opens. If your phone doesn't ask, open `http://mirrordash.setup` in the browser.
4. **Choose your Wi-Fi**: Tap your home network (or *Network not listed?*), type its password (tap *Show* to check it) and tap **Connect the mirror**.
5. **Afterwards**: The mirror shows its clock in a moment, without restarting. Reconnect your phone to your home Wi-Fi and open `mirrordash.local` to reach the admin page. If the password was wrong, `MirrorDash-Setup` appears again within a minute, with the same password; connect and try again.

---

## 9. Failsafe Operation & SD Card Preservation

To ensure 100% crash resilience and protect physical SD media from wear, MirrorDash runs on a locked read-only system (OverlayFS) with split directory lifecycles:

*   **Persistent Configuration & Virtual Environments (`/storage/mirrordash/`)**: All permanent files, user settings (including timezone, SSH daemon configurations, and password hashes), databases, authentication tokens, and the primary A/B virtual environments survive reboots on the writeable storage partition.
*   **Volatile Caching (`~/.mirrordash/cache/`)**: Ephemeral files, network logs, and downloaded icons live entirely in a RAM-disk buffer and are wiped cleanly when the system loses power.
*   **Failsafe Recovery**: If a software update causes a startup crash, the system automatically rolls back the symlink to the previous stable copy (`venv_old`) or fallback boots the read-only Golden Copy (`base_venv` in Safe Mode) to keep the mirror online.

You can safely pull the power plug at any time without risking database or partition corruption.

---

## 10. Home Assistant

Home Assistant can show the mirror's status and sensors, turn its screen on and off (for example when nobody is home) and set its brightness.

1.  **Create a token**: in the admin page, *Hardware* → *API Access* → **Create Token**, and copy it.
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

