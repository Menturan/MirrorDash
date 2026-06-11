# MirrorDash User Guide

Welcome to MirrorDash! This guide is designed for end-users and mirror administrators to help you set up, customize, and manage your smart mirror display. You do **not** need to be a programmer or software developer to follow this guide.

---

## 1. What is MirrorDash?

MirrorDash is an ambient heads-up display (HUD) designed to run on a screen behind a semi-reflective two-way mirror (often powered by a Raspberry Pi). 

*   **Ambient Display**: It is designed to be a passive, glanceable information screen (e.g. showing the time, calendar, Swedish name days, weather) — not an interactive tablet.
*   **The Zero-Light Philosophy**: The background is solid black. In a physical mirror, black areas behave as a standard reflective mirror, while the white/gray text and symbols float on the glass.

---

## 2. Accessing the System

Once your mirror server is running, you can access it via a web browser on any device (phone, tablet, or computer) connected to the same local Wi-Fi network:

*   **Mirror Display**: `http://localhost:8000/` (or `http://<your-pi-ip>:8000/`)
*   **Admin Dashboard**: `http://localhost:8000/admin` (or `http://<your-pi-ip>:8000/admin`)

---

## 3. Initial Setup & Security

The first time you open the **Admin Dashboard**, you will be prompted to secure your mirror:

1.  **Set Admin Password**: Choose a secure password. This password will protect your configuration and system controls.
2.  **API Authentication**: Background scripts and automated backup utilities access the system by providing your admin password in the `X-API-Key` HTTP header.

For subsequent visits, simply log in using your admin password.

---

## 4. Using the Admin Dashboard

The Admin Dashboard is organized into five main tabs:

### 4.1. Modules Tab
This is where you manage the widgets displayed on your mirror.
*   **Root Partition Storage (Virtual Env)**: Displays a real-time disk usage gauge showing the total, used, and free space on the system's root partition. Since modules and their dependencies are installed directly in the frozen environment, this gauge helps you monitor the 6GB boundary. A warning will appear if free space drops below 500MB.
*   **Active Modules**: Lists all currently running widgets. You can click **Configure** next to any active module to adjust its settings (e.g., changing refresh intervals, adding calendar URLs, or toggling headers).
*   **Install New Modules**: Search the community module database. Click **Details** on any module to read its setup guide and view screenshots. Click **Install** to add it to your system.
*   **Uninstalling**: If you no longer need a module, click **Uninstall** to cleanly remove it from the system and configuration.

### 4.2. Configuration Tab
Controls global settings shared by all modules. Adjust these to localize your mirror:
*   **Language**: Set display language (e.g., `en` for English, `sv` for Swedish).
*   **Timezone**: Your region's timezone identifier (e.g., `Europe/Stockholm`).
*   **Time Format**: Choose between `24h` or `12h` display.
*   **Units**: Change temperature units (`C` or `F`) and distance (`km` or `mi`).
*   **Coordinates**: Latitude and longitude (used by weather modules to locate your mirror).

### 4.3. System Tab
Allows you to adjust physical display properties and automate display power directly from your browser:
*   **Screen Rotation**: Rotate the screen layout (`normal`, `left`, `right`, or `inverted`) to support portrait-oriented mirrors.
*   **Screen Resolution**: Set display resolution or keep it on `auto`.
*   **Screen Brightness**: Adjust display backlight brightness (0% to 100%).
*   **System Volume**: Control mirror audio output levels.
*   **Display Power Management**: Choose how your screen is controlled automatically:
    *   *Manual / Always On*: The screen stays on unless you manually click "Turn Screen OFF".
    *   *Time of Day Schedule*: Specify an *Active Start Time* (e.g. `07:00`) and *Active End Time* (e.g. `22:30`) to turn the display on during the day and off at night.
    *   *PIR Motion Detector*: Connect a PIR motion sensor to a Raspberry Pi BCM GPIO pin (e.g. `18`). The screen will turn on when motion is detected and automatically shut down after $N$ minutes of inactivity.
    *   *Physical GPIO Button*: Connect a momentary push button to a BCM GPIO pin (e.g. `23`) to toggle the display power state manually by pressing it.
*   **Screen Power**: Instantly turn the mirror display output ON or OFF. (Manually overriding automation states will temporarily trigger that state).

### 4.4. Backup Tab
Protect your configurations and personal data files:
*   **Create Backup**: Downloads a single `.zip` file containing your entire setup, module settings, and authentication details.
*   **Restore Backup**: Upload a previously saved backup file to restore your mirror to that state instantly.

### 4.5. Logs Tab
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

### Calendar events are not showing up
*   Verify that your calendar URL is public and ends with `.ics`. 
*   Google Calendar private links must be the "Secret address in iCal format" found in your Google Calendar settings.

### System settings (brightness/rotation) are not applying
*   On Raspberry Pi, the system volume and brightness controls require administrative hardware privileges. Ensure your user has permissions to run system control scripts.

---

## 8. Network Setup (WiFi Captive Portal)

MirrorDash is designed to be a plug-and-play appliance. If you move your mirror to a new network or boot it for the first time without configuring WiFi, the system enters **Captive Portal fallback mode** automatically.

1. **Connect to Hotspot**: On your phone or computer, open WiFi settings and look for the network named **`MirrorDash-Setup`**.
2. **Enter Setup Password**: Connect using the password **`mirrordash`**.
3. **Configure WiFi**: Open a web browser and navigate to `http://10.42.0.1:8000`. You will be greeted by the minimalist **WiFi Setup Wizard**.
4. **Submit Details**: Select your home network SSID from the dropdown (or type it manually), enter your WiFi password, and click **Connect & Reboot**.
5. **System Startup**: The mirror will connect to your WiFi and reboot itself back into normal display mode.

---

## 9. Failsafe Operation & SD Card Preservation

To ensure 100% crash resilience and protect physical SD media from wear, MirrorDash runs on a locked read-only system (OverlayFS) with split directory lifecycles:

*   **Persistent Configuration (`~/.mirrordash/data/`)**: All permanent files, user settings, databases, and authentication tokens are saved in the persistent sector.
*   **Volatile Caching (`~/.mirrordash/cache/`)**: ephemerals, network logs, and downloaded icons live entirely in a RAM-disk buffer and are wiped cleanly when the system loses power.

You can safely pull the power plug at any time without risking database or partition corruption.

