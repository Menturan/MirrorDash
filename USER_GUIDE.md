# MirrorDash User Guide

From a blank SD card to a mirror on the wall. The admin page explains its own settings where you change them; this guide covers the rest.

## Quick start

1. **Download** the newest image (`mirrordash-os-….img.xz`) from [Releases](https://github.com/Menturan/MirrorDash/releases) and flash it onto an SD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (*Choose OS* → *Use custom*).
2. **Start** the Pi with the card, the screen and the power plugged in. It restarts once by itself.
3. **Join** `MirrorDash-Setup` on your phone by scanning the QR code on the screen, and pick your home Wi-Fi.
4. **Open** `mirrordash.local/admin` on your phone, choose a password and save the recovery code.
5. **Choose** what the mirror shows under *Modules* → *Add modules*.

That's all. The rest of this guide is for when you want more.

- [1. Putting MirrorDash on the SD card](#1-putting-mirrordash-on-the-sd-card)
- [2. First start: Wi-Fi setup](#2-first-start-wi-fi-setup)
- [3. The admin page](#3-the-admin-page)
- [4. Choosing what the mirror shows](#4-choosing-what-the-mirror-shows)
- [5. When the screen is on](#5-when-the-screen-is-on)
- [6. Connecting hardware](#6-connecting-hardware)
- [7. Smart home: Matter, the API and Home Assistant](#7-smart-home-matter-the-api-and-home-assistant)
- [8. Updates and backups](#8-updates-and-backups)
- [9. When something is wrong](#9-when-something-is-wrong)
- [10. Safety: your home and the SD card](#10-safety-your-home-and-the-sd-card)

---

## 1. Putting MirrorDash on the SD card

You need a Raspberry Pi 3 B or newer, an SD card of 8 GB or more, the official power supply, a screen with HDMI, and a two-way mirror with a frame. The [README](README.md#get-started) has the whole list, with what to look for.

1. Download the image, `mirrordash-os-….img.xz`, from [Releases](https://github.com/Menturan/MirrorDash/releases): the newest release whose name ends in `-os1`, `-os2`, ….
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/): *Choose OS* → *Use custom*, pick the image, then your SD card.
3. Put the card in the Pi, connect the screen and plug in the power. On its first start the mirror restarts once by itself, then shows the Wi-Fi setup.

---

## 2. First start: Wi-Fi setup

With a network cable plugged in there is no Wi-Fi setup: open `http://mirrordash.local/admin` right away. A cable plugged in later stops the setup network within seconds.

A new mirror (or one that can't find its Wi-Fi) starts its own network for the setup:

1. **Join `MirrorDash-Setup`** on your phone. The mirror shows its password with a QR code: point the camera at it, or type the password. It stays the same until the SD card is flashed again.
2. **Sign in**: the phone says you need to sign in to the network, like on a hotel Wi-Fi. Tap it and the setup page opens. If it doesn't ask, open `http://mirrordash.setup`.
3. **Choose your Wi-Fi**, type its password (*Show* lets you check it) and tap **Connect the mirror**.
4. The mirror shows its clock in a moment. Put your phone back on your home Wi-Fi and open `mirrordash.local`. If the password was wrong, `MirrorDash-Setup` comes back within a minute: join it and try again.

**Setting up a new card from a backup:** in step 3, tap **Set up from a backup instead**, choose a backup made with a password, and type that password. The mirror joins a Wi-Fi network saved in the backup and restores everything else by itself, then restarts (a few minutes: the modules are downloaded again). Log in with the admin password from the backup.

The time zone is taken from your phone. It also sets the country for Wi-Fi (which channels may be used) from the next restart; until then, networks on channel 12 or 13 don't show up.

**Changing Wi-Fi later:** *Settings → Wi-Fi* in the admin page. Choose the network, type its password and tap **Connect**; then put your phone on the same network and open `mirrordash.local`. If the mirror can't join, it goes back to the network it was on, and the card says why.

---

## 3. The admin page

Open **`http://mirrordash.local/admin`** on a phone or computer on the same Wi-Fi as the mirror. If your device can't find `mirrordash.local`, use the mirror's IP address instead (shown in your router).

The first time, you choose an admin password, and the admin page shows a **recovery code** (`XXXX-XXXX-XXXX`) once. Save it in your password manager or write it down. You change the password under *Settings → Admin Password*.

**Logging in:** each phone or computer logs in once and stays logged in for 30 days. **Log Out on This Device** is in Settings → Admin Password; changing the password there logs out every other device. After five wrong passwords in a minute, the mirror waits a minute before it accepts another try.

Forgot the password? See [Forgot the password?](#forgot-the-password).

> **Tip:** Put the admin page on your phone's home screen, so it opens like an app. iPhone: open it in Safari, tap *Share* → *Add to Home Screen*. Android: open it in Chrome, tap the menu (⋮) → *Add to Home screen*.

---

## 4. Choosing what the mirror shows

The clock comes with the mirror; everything else is a module. In the admin page, open *Modules* → *Add modules* and tap **Install** on the ones you want. The ones made by MirrorDash are marked **Official**; install others only from people you trust ([why](#10-safety-your-home-and-the-sd-card)). A module's own settings, such as the city for the weather or the link to your calendar, open from it in the Modules tab.

### Where things go on the screen

The screen has nine areas: top, middle and bottom, each left, center and right. Each module has a position in its settings: in the Modules tab, tap it on the map or choose **Settings**.

*   Several modules in the same area are stacked.
*   A module can be added more than once, for example two clocks in different time zones, each with its own settings.
*   **Taking turns (carousel):** give modules in the same area the same **Carousel Group** name, and they show one at a time, switching every **Carousel Interval** seconds. Modules in that area without the group name stay stacked as usual.

---

## 5. When the screen is on

The Power tab chooses *Always on*, *On a schedule* or *Off until woken*. Outside the schedule, and in *Off until woken*, the screen can still be woken for a while:

*   What wakes it: someone in front of the mirror (with a motion or presence sensor, if *Wake when someone is in front of the mirror* is on), a button press set to *Wake the screen*, **Turn Screen ON**, a home app over Matter, or Home Assistant (see [7](#7-smart-home-matter-the-api-and-home-assistant)).
*   *From the last activity* keeps it on while someone is still there; *A fixed time* turns it off after exactly that time, for example for a doorbell.
*   Turning the screen off by hand keeps it off until something wakes it again or the schedule starts.
*   While the screen is off, the modules don't fetch new data (saving API calls). When it comes back on, those whose update was due fetch it right away.

Always use **Shut Down** (Power tab) before unplugging the mirror. To start it again, unplug the power and plug it back in, or use a button on GPIO 3 (below).

---

## 6. Connecting hardware

Add buttons, sensors and a fan in the Hardware tab under *Sensors & Inputs*; it shows how to wire each one. Some things it doesn't say:

*   Something new is used after the mirror restarts; the card then shows a **Restart Mirror** button.
*   A **push button on GPIO 3** (pin 5) also starts the mirror again after it has been shut down. With *Shut down the mirror* on its long press, the button works as an on/off switch. GPIO 3 is also used by the light sensor, so the two can't be combined.
*   An **on/off fan** (2 or 3 wires) needs a transistor or MOSFET between the GPIO and the fan, never the GPIO alone. A 4-wire PWM fan is connected directly.
*   **Brightness on an HDMI screen** is sent over the cable (DDC/CI). Most computer monitors understand it, most TVs don't. Some monitors have it turned off in their own menu: turn it on there and move the slider again.

---

## 7. Smart home: Matter, the API and Home Assistant

### Smart home (Matter)

With Matter, the mirror's screen shows up as a light in Apple Home, Google Home or Home Assistant (Alexa and SmartThings should work too, but haven't been tried yet): turn it on and off from the app, by voice, or in your automations. Turning it on wakes the screen for the screen timeout, just like someone walking by.

Apple Home and Google Home need a home hub for Matter: a HomePod or Apple TV for Apple, a Nest speaker or display for Google.

1. In the admin page, *Settings* → *Matter*, turn **Matter** on. After a few seconds the card shows a QR code and an 11-digit code.
2. In your home app, add a device (Apple Home: *+* → *Add Accessory*) and scan the QR code, or type the code. The phone has to be on the same network as the mirror; no Bluetooth is needed.
3. The app warns that the device isn't certified (certification costs far more than a hobby project has). Choose **Add Anyway**.

**Google Home** only adds uncertified devices it has been told about, once: in the [Google Home Developer Console](https://console.home.google.com), create a project, add a Matter integration with vendor ID `0xFFF1`, product ID `0x8000` and the device type *Light*, and use the same Google account in the Home app.

**More than one home app:** pair the mirror with the first one, then turn on pairing mode for it there (Apple Home: the light's settings → *Turn On Pairing Mode*) and use that new code in the next app.

**Starting over:** *Reset Pairing* on the card removes the mirror from every home app and shows a code again. Turning Matter off keeps the pairing for when you turn it on again; it costs about 100 MB of memory while on.

### MQTT

With MQTT the mirror appears in Home Assistant by itself, as a device with its screen, brightness, CPU temperature, the sensors and buttons you connected under *Hardware*, and what your modules share. Nothing to paste into `configuration.yaml`. You need an MQTT broker; the **Mosquitto broker** add-on in Home Assistant is the usual one, with its MQTT integration set up.

1.  **A login for the mirror**: in the Mosquitto add-on's *Configuration*, under `logins`, add one (for example `username: mirrordash` and a long password of its own). Use a login of the add-on, not a Home Assistant user: plain MQTT sends the password unencrypted on your network, and a Home Assistant user's password also opens Home Assistant itself.
2.  **Connect**: in the mirror's admin page, *Settings* → *MQTT*, type the broker (`homeassistant.local` or Home Assistant's IP address), the user and the password, and **Save and Connect**. The card says *Connected*, and the mirror shows up in Home Assistant under *Settings* → *Devices*.
3.  **Choose what Home Assistant may do.** At first it can only look. On the card you can let it turn the screen on and off and set the brightness, turn modules on and off, and, for each module that offers it, show the module and let Home Assistant control what it offers (a TV module's channel, say). Save after each change.

Buttons show up as events (single, double, triple, long press) that you can use as automation triggers. Until the broker is locked down (below), any device on it could fake a press or the motion sensor, so don't let them unlock a door or turn an alarm off before that.

**Lock the broker down.** MQTT can't tell who sent a message: without more, every device logged in to your broker (a Zigbee bridge, a smart plug) could send the mirror what you allowed, and take over its connection. The card's *Lock the broker down* shows the lines for it. In the Mosquitto add-on's configuration, turn on `customize` (`active: true`, `folder: mosquitto`), then make two files in Home Assistant's `/share/mosquitto` folder (the *File editor* or *Samba* add-on can):

- `acl.conf`:
  ```
  acl_file /share/mosquitto/accesscontrollist
  use_username_as_clientid true
  ```
- `accesscontrollist`: the card's lines for the mirror, and lines for everyone else that uses the broker, or they lose their access. Home Assistant logs in as `homeassistant`, and add-ons that find the broker by themselves (Zigbee2MQTT, for one) as `addons`; a device with a login of its own gets its own lines:
  ```
  user homeassistant
  topic readwrite #

  user addons
  topic readwrite zigbee2mqtt/#
  topic readwrite homeassistant/#
  ```

Restart the add-on and check that everything still reports in Home Assistant; the add-on's log names a login that was refused. Each device needs a login of its own (with `use_username_as_clientid`, two on one login throw each other off). The mirror connects with a client ID of its own that nobody else knows, so even before this, another client can only throw it off if it guesses that.

**TLS**: tick it on the card when your broker has a certificate a browser would trust (port 8883). A self-signed one isn't accepted. Without TLS, someone on your network can read the mirror's MQTT password and what it sends, and pass for the broker; keep the mirror's login for the mirror alone.

**Turning it off** (*Turn Off* on the card) removes the mirror from Home Assistant. A backup without a password leaves the broker password out: restoring one keeps the mirror's, and on a new mirror you type it again.

### The API

Other systems can read the mirror's status and sensors, turn its screen on and off and set its brightness. They need a token: in the admin page, *Settings* → *API* → **Create Token**. Every call is described, and can be tried with your token, at `http://mirrordash.local/api/v1/docs`.

The calls (all with the header `Authorization: Bearer <token>`):

| Call | What it does |
|------|--------------|
| `GET /api/v1/status` | Version, uptime, CPU temperature, `screen_on`, `brightness`, running modules and `sensors` (`temperature_c`, `humidity`, `lux`, `motion`, `fan_level`, for what is connected). |
| `POST /api/v1/screen` `{"state": "on"}` | Wakes the screen for the screen timeout; add `"timeout_minutes": 2` for two minutes. `{"state": "off"}` turns it off. |
| `POST /api/v1/brightness` `{"value": 60}` | Sets and saves the brightness (10–100). |

A wrong or missing token gets `401`, a value out of range `422`. (The older `POST /admin/screen` still works without a token, so existing automations keep running.)

### Home Assistant

Home Assistant can show the mirror's status and sensors, turn its screen on and off (for example when nobody is home) and set its brightness.

1.  **Create a token**: in the admin page, *Settings* → *API* → **Create Token**, and copy it.
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

---

## 8. Updates and backups

*   **Updates** are in the Settings tab. *Test versions* gets new versions before everyone else; they can have bugs. After an update the mirror's screen reloads by itself; **Reload Screen** (Power tab) does the same by hand.
*   **Automatic updates** are on unless you turn off *Install updates automatically* in the same card: at night (between 03:30 and 04:30) the mirror installs new versions of MirrorDash and of the system by itself, with the same checks as the buttons. A system version that didn't start properly isn't tried again by itself, and a MirrorDash that some of your modules don't work with waits until you choose on the card.
*   **System updates** (the Linux system underneath MirrorDash) have their own *System* part in the same card, checked by the same **Check for Updates**. **Install System Update** downloads it, installs it next to the running system and restarts the mirror into it. It takes 10–30 minutes; keep the mirror plugged in. If the network or MirrorDash doesn't come up with the new system, the mirror goes back to the old one by itself within a few minutes, and the admin page says so. Nothing is lost either way. When MirrorDash and the system both have an update, install the system first; the MirrorDash button waits until then.
*   **A backup** (Backup tab) contains your settings, modules and their data, the hardware settings and the API token. **With a password** it is encrypted and also contains every password: the admin and SSH passwords, the saved Wi-Fi networks, the MQTT broker's password, the Matter pairing code and the modules' API keys and passwords. Restoring it brings those back too, and a new card can be set up from it on the Wi-Fi setup page (section 2). **Without a password** it contains none of them, and restoring keeps the ones the mirror has (a module's key for the same module in the same place). Either way the modules are installed again (this needs internet) and the mirror restarts. Backups made before version 0.9.1 can't be restored.

---

## 9. When something is wrong

*   **"Power Warning: Under-voltage Detected" on the dashboard:** the power supply is too weak. The mirror slows down and the SD card can be damaged. Use the official Raspberry Pi power supply (5.1 V, 2.5 A for a Pi 3).
*   **Modules are empty or missing:** check the mirror's internet connection, and the Logs tab for errors.
*   **"MirrorDash isn't responding" on the mirror:** it lost contact with its software for more than two minutes and keeps trying. If it doesn't come back, use **Restart MirrorDash** in the Power tab, or unplug and plug in the mirror.
*   **"MirrorDash didn't start" on the mirror:** if a system update was just installed, wait a few minutes: the mirror goes back to the previous system by itself. Otherwise unplug it and plug it in again. If it keeps happening, the SD card may need a fresh MirrorDash image (restore your backup afterwards).
*   **To find out why something went wrong:** *Logs → Saved Logs* has the log of every update that failed, of Wi-Fi that stopped passing traffic, of a screen or app that froze and was restarted, and of a storage partition that was repaired (the newest 5). For a problem that keeps coming back, turn on *Settings → Developer → Developer logging*: the whole system log is then kept across restarts for 7 days (it wears the SD card a little, so it turns itself off).
*   **The screen looks wrong:** **Reload Screen** in the Power tab.
*   **If an update doesn't start**, the mirror goes back to the version before by itself and shows *Update Undone*. If even that fails, it starts in *Safe Mode* with the version that came with the system, without your modules: use **Reinstall MirrorDash** in the admin page's banner to install it again; your modules come back with it. Either way, the log that shows why is under *Logs → Saved Logs*.

### When the Wi-Fi goes away

*   **The internet is down, the router is on:** nothing happens; the modules show their last data until it's back.
*   **The router is off for a while** (a power cut, a restart): the mirror joins again by itself when it's back. If the mirror started first and put up `MirrorDash-Setup`, it tries your Wi-Fi again every few minutes and goes back to it.
*   **Your Wi-Fi is gone for good** (a new router or password): within a few minutes the mirror starts `MirrorDash-Setup`; set it up as in [2](#2-first-start-wi-fi-setup).
*   **The Wi-Fi chip doesn't start** (now and then on a Pi 3): within a few minutes the mirror starts it again by itself, and if that doesn't help and it has no network at all, it restarts (at most once an hour). The log is kept under *Logs → Saved Logs*.
*   **The Wi-Fi is connected but nothing gets through** (it happens with the Pi 3's Wi-Fi now and then): within about 6 minutes the mirror notices, joins the Wi-Fi again and keeps the log under *Logs → Saved Logs*. When it's the Wi-Fi chip that hangs (a Pi 3 can, during a long download like an OS update), the mirror starts the chip again instead, and if that doesn't help within about 12 minutes, it restarts (at most once an hour).

### Forgot the password?

Tap **Forgot password?** on the login page, enter the recovery code and choose a new password. The code then stops working, and you get a new one to save.

#### Lost the recovery code too?

Then you need the mirror itself. Your settings stay; only the admin password is removed, and the admin page asks for a new one. Choose it right away: until then, anyone on your Wi-Fi could choose it.

*   **Over SSH** (if SSH is on in *Settings → Developer*), log in with `ssh pi@mirrordash.local` and run:
    ```bash
    sudo systemctl stop mirrordash
    ~/mirrordash/.venv/bin/python -c "from mirrordash_core.config import load_config, save_config; c = load_config(); c.pop('admin_auth', None); save_config(c)"
    sudo systemctl start mirrordash
    ```
*   **On the SD card:** shut the mirror down (*Power → Shut Down*) and put its SD card in a Linux computer. Windows and macOS can't read the settings partition (ext4) without an extra tool. On the partition named `mirrordash-data`, remove the `"admin_auth"` entry from `mirrordash/data/data/config.json`, for example:
    ```bash
    sudo python3 -c "import json, sys; p = sys.argv[1]; c = json.load(open(p)); c.pop('admin_auth', None); json.dump(c, open(p, 'w'), indent=2)" /media/$USER/mirrordash-data/mirrordash/data/data/config.json
    ```
    Then put the card back and start the mirror.

### Starting over

To give the mirror away or start over, use **Reset Mirror** at the bottom of the Settings tab (type RESET): your settings, the admin password, modules and their data, backups, the Wi-Fi networks and the API token are deleted, and the mirror restarts as when its card was flashed: it asks for Wi-Fi and a password on its own screen. Download a backup first if you want to keep anything.

---

## 10. Safety: your home and the SD card

- **The admin page uses plain HTTP**, like most devices at home (printers, routers). On your own Wi-Fi that is fine, but someone who can listen in on that network could see the admin password. Don't use the same password anywhere else, and don't make the mirror reachable from the internet (no port forwarding to it).
- **A module is a small program that runs on the mirror.** Each one runs apart, as a user of its own: it can't read MirrorDash's settings or passwords, another module's settings or keys (like the Home Assistant token), or reach your home network, except the address you type in its settings (like Home Assistant's). Each one is also installed in a folder of its own, so one module's install can't change another's code. A module can still send what it reads to the internet, so under *Modules → Add modules*, the ones made by MirrorDash are marked **Official**; install others only from people you trust, and read their README first.
- **MQTT is only as closed as your broker.** Home Assistant can only look at the mirror until you allow more on the MQTT card, and it can never change settings, install anything or restart it. But anyone who can write to your broker can do what you allowed, so give each device its own user and lock the broker down as in [MQTT](#mqtt).
- **A guest network is the safest place** for the mirror if your router has one: the mirror only needs the internet, and then a module can't reach your computers.
- **SSH is off** unless you turn it on under *Settings*, with a password of your own.

### How the SD card is protected

The system part of the SD card is read-only: everything written there during use is gone at the next start, so the system can't wear out or slowly break. There are two copies of it: a system update is written to the one that isn't running, and the mirror keeps the old one until the new one has started properly. Your settings, modules and Wi-Fi are kept on a separate storage partition. That is the part a sudden power cut can damage, which is why you should use **Shut Down** before unplugging. The system log stays in memory, so nothing is written to the card all day; only a failed update leaves a log behind, unless *Developer logging* is on.
