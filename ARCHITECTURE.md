# MirrorDash — Architectural Decisions Record (ADR)

This document records the core architectural decisions made during the design, development, and refinement of the MirrorDash codebase.

## Table of Contents

- [1. Peripheral Modular Grid Layout](#1-peripheral-modular-grid-layout)
- [2. Server-Side Rendering (SSR) via Jinja2 & WebSocket Push](#2-server-side-rendering-ssr-via-jinja2--websocket-push)
- [3. Dynamic Module Discovery via Python Entry Points](#3-dynamic-module-discovery-via-python-entry-points)
- [4. Config-Driven Lifespan & Hot Reloading](#4-config-driven-lifespan--hot-reloading)
- [5. OverlayFS and Hardware Remounting Integration](#5-overlayfs-and-hardware-remounting-integration)
- [6. Hybrid Template Loader Resolution](#6-hybrid-template-loader-resolution)
- [7. Scope Isolation for Dynamic Module Helpers](#7-scope-isolation-for-dynamic-module-helpers)
- [8. Skeletal Loading UI & Transition Flow](#8-skeletal-loading-ui--transition-flow)
- [9. WebSocket State Caching for Instant Updates](#9-websocket-state-caching-for-instant-updates)
- [10. Carousel Groups for Layout Regions](#10-carousel-groups-for-layout-regions)
- [11. Display Power Automation Strategies](#11-display-power-automation-strategies)
- [12. Persistent Config and Modules Relocation for PyPI Packages](#12-persistent-config-and-modules-relocation-for-pypi-packages)
- [13. Primary Persistent Storage Path (Directory Contract)](#13-primary-persistent-storage-path-directory-contract)
- [14. WiFi Fallback / Captive Portal State Machine](#14-wifi-fallback--captive-portal-state-machine)
- [15. Watchdog and Time Synchronization Boot Guard](#15-watchdog-and-time-synchronization-boot-guard)
- [16. Failsafe A/B Virtual Environment Updates](#16-failsafe-ab-virtual-environment-updates)
- [17. Boot Fallback Launcher and Settings Restoration](#17-boot-fallback-launcher-and-settings-restoration)
- [18. Background Jobs and Restart Detection in the Admin UI](#18-background-jobs-and-restart-detection-in-the-admin-ui)
- [19. Sensors and Inputs via Kernel Device-Tree Overlays](#19-sensors-and-inputs-via-kernel-device-tree-overlays)
- [20. Screen Wake Timer](#20-screen-wake-timer)
- [21. Module Scripts Receive Their Shadow Root](#21-module-scripts-receive-their-shadow-root)
- [22. Releases Are Made From What Was Tested](#22-releases-are-made-from-what-was-tested)

---

## 1. Peripheral Modular Grid Layout
* **Decision**: Snaps mirror modules into 9 distinct grid regions (`top_left`, `top_center`, `top_right`, `middle_left`, `middle_center`, `middle_right`, `bottom_left`, `bottom_center`, `bottom_right`), each an absolutely positioned anchor (a flex column pinned to an edge or the centre), keeping the center void clear.
* **Rationale**: Designed for ambient heads-up display (HUD) observation through semi-reflective glass. Centering information is restricted to maintain the primary reflective function of the mirror.

## 2. Server-Side Rendering (SSR) via Jinja2 & WebSocket Push
* **Decision**: Modules run their own event loops on the Python backend, render their HTML templates using Jinja2, and push the fully formed HTML payload to the client via WebSockets.
* **Rationale**: Keeps the kiosk client extremely thin, lightweight, and framework-agnostic. The client only needs to mount and transition raw HTML strings into designated container divs, eliminating complex client-side state managers or rendering libraries.

## 3. Dynamic Module Discovery via Python Entry Points
* **Decision**: Rather than hardcoding modules in the loader, the system automatically discovers installed module packages dynamically using Python `importlib.metadata` entry points (group: `mirrordash.modules`).
* **Rationale**: Allows third-party modules to be developed, packaged, and installed completely independently as standard Python wheels or editable packages. The core platform discovers them implicitly on startup.

## 4. Config-Driven Lifespan & Hot Reloading
* **Decision**: System configurations stored in `config.json` dictate module coordinates, custom properties, and enabled states. The config has been expanded to support multiple instances of the same module type. Each instance is indexed by a unique `instance_id` and contains a `"module"` property to identify the module type. Changes to the config trigger a soft reload: stopping, cancelling, and garbage-collecting running loops, and then spinning up newly configured instances.
* **Rationale**: Users can run multiple instances of modules (e.g. clocks in different timezones or positions) and customize their mirror layouts and parameters dynamically without restarting the Uvicorn web server or causing full browser disconnects.

## 5. OverlayFS and Hardware Remounting Integration
* **Decision**: Admin/system modification commands (such as module installations or configuration updates) execute remounting scripts (`mount -o remount,rw /`) before performing operations, and switch back to read-only (`remount,ro`) immediately after completion.
* **Rationale**: Protects SD card longevity when deployed on Raspberry Pi systems running OverlayFS (Read-Only OS configuration), while still allowing seamless software administration. Automated images enable OverlayFS on their first boot via a one-shot `mirrordash-lock.service`, because `raspi-config` must build the overlay initramfs for the device's running kernel and cannot do so in the build container.

## 6. Hybrid Template Loader Resolution
* **Decision**: Implemented a `ChoiceLoader` combining standard Jinja2 `PackageLoader` with a fallback `FileSystemLoader` that resolves physical package paths on disk using `importlib.util.find_spec`.
* **Rationale**: Resolves `TemplateNotFound` errors on PEP 660 editable installations (common in local dev/testing environments like Hatch/uv) where zipped virtual package paths can hide standard template subdirectories.

## 7. Scope Isolation for Dynamic Module Helpers
* **Decision**: Auto-injected helpers (such as `render_template` and `translate`) are bound to their module instance by `_inject_module_helpers` in `module_loader.py`, called once per instance after the constructor, so they are not available inside `__init__`.
* **Rationale**: Solves Python's loop lexical closure late-binding behavior. Defined inside the loader loop instead, nested helper definitions reference the loop variable by name, resulting in all module instances executing translations and templates using the scope of whichever module loaded last.

## 8. Skeletal Loading UI & Transition Flow
* **Decision**: Added a public unauthenticated `/api/active-modules` API to retrieve active modules list. On startup/refresh, the client fetches this metadata, pre-renders placeholder loading skeletons with visual spinners, and transitions them smoothly using a fade-in animation (`.module-enter`) once the first WebSocket frame for that module arrives.
* **Rationale**: Eliminates the flash of a blank screen on startup/page refresh. It provides immediate, responsive feedback ("Loading Swedish Name Day...", "Loading Clock...") while the backend modules fetch remote API data or perform slow initialization loops.

## 9. WebSocket State Caching for Instant Updates
* **Decision**: Implemented an in-memory frame cache (`latest_messages`) inside the WebSocket `ConnectionManager` ([ws_manager.py](file:///home/menturan/repos/mymagicmirror/mirrordash_core/ws_manager.py)). Every time a module broadcasts an HTML payload, it is cached. Upon a new connection, the manager immediately pushes all cached HTML frames to the newly connected client. The cache is automatically cleared when modules reload or stop.
* **Rationale**: Resolves the delay on page refresh where modules (especially those with long update intervals like the 60-second name day module or hourly updates) would remain as skeletons until their sleep intervals completed and they triggered a new broadcast. Now, refreshed screens load the last rendered frames instantly.

## 10. Carousel Groups for Layout Regions
* **Decision**: Implemented a client-side carousel grouping system (`.carousel-group-container` and `.carousel-slide`). When multiple modules in the same region share the same `carousel_group` string in the configuration, they are rendered inside a single grid container and cycle visibility on a set timer, while ungrouped modules stack normally.
* **Rationale**: Provides users with fine-grained control over which modules cycle and which ones remain static in a region, avoiding rigid full-screen transitions. By using CSS Grid overlaying (`grid-area: 1 / 1 / 2 / 2`), all slides occupy the exact same space, preventing visual layout jumping or shifting during cross-fade transitions, keeping the ambient mirror clean.

## 11. Display Power Automation Strategies
* **Decision**: Integrated a central `DisplayPowerManager` daemon executing alongside the module loader lifespan. It supports time schedules, PIR motion sensor triggers, and physical GPIO buttons. The GPIO libraries (`gpiozero` and `RPi.GPIO`) are dynamically imported in a try/except block to allow clean fallbacks on standard non-Pi systems. The Time of Day Schedule mode respects the global timezone configuration (e.g. `Europe/Stockholm`) when fetching the current time, ensuring timezone-aware scheduling.
* **Rationale**: Smart mirrors require automated power conservation. Supporting time scheduling, PIR sensors, and buttons allows different hardware setups to save energy automatically. Decoupling hardware imports ensures the codebase remains testable and runnable on standard developer machines. Timezone awareness prevents schedule misalignment if the host machine (e.g., Raspberry Pi) is configured to UTC or another local time.

## 12. Persistent Config and Modules Relocation for PyPI Packages
* **Decision**: Migrated the primary location of `config.json` and custom local modules out of the package installation directory (which is read-only and wiped on package updates) into the user's home directory (`~/.mirrordash/config.json` and `~/.mirrordash/modules/`).
* **Rationale**: Allows the core platform to be installed and run cleanly as a standard PyPI package. User configurations and custom module directories are preserved across upgrades, while still allowing developers to run from a local cloned git workspace via fallbacks.

## 13. Primary Persistent Storage Path (Directory Contract)
* **Decision**: Adjusted the primary persistent configuration storage path to `~/.mirrordash/data/config.json` and module persistent data to `~/.mirrordash/data/<instance-id>/`. High-frequency ephemeral cache files are placed in `~/.mirrordash/cache/<instance-id>/`.
* **Rationale**: Aligns the platform with the locked read-only system blueprint (OverlayFS). Under read-only systems, `~/.mirrordash/cache/` is mapped directly to a RAM-disk tmpfs buffer to eliminate physical SD card wear and ensure crash immunity. `~/.mirrordash/data/` acts as the persistent sector. Isolating directory paths per instance ID prevents separate instances of the same module type from clobbering each other's data.

## 14. WiFi Fallback / Captive Portal State Machine
* **Decision**: Implemented an automated fallback WiFi captive portal setup state machine. If network connectivity is not verified within 30 seconds of system boot, NetworkManager shifts `wlan0` to an autonomous Access Point (AP) setup hotspot. Phones get a real captive portal: the OS image makes the hotspot's dnsmasq (`/etc/NetworkManager/dnsmasq-shared.d/`, used only for shared connections) answer every DNS name with `10.42.0.1`, so the phone's HTTP connectivity check (`captive.apple.com/hotspot-detect.html`, `connectivitycheck.gstatic.com/generate_204`, ...) reaches the app, whose middleware answers anything but the setup page with a redirect to `http://mirrordash.setup/wifi-setup`; the unexpected answer makes the phone show "Sign in to network". A failed connection from the hotspot reboots the mirror, because the hotspot is already torn down by then. Submitting credentials remounts the filesystem read-write, saves the new NetworkManager profiles, remounts read-only, and reboots the OS back into client mode. The check service is `Type=oneshot` and ordered `Before=mirrordash.service`, so the app (and therefore the kiosk's first page) only starts after the client-vs-hotspot decision; the app's hotspot check uses a short TTL cache, never a permanent one, so a state change is picked up within seconds. The hotspot password is unique per mirror: the check script generates it when it creates the `MirrorDash-Setup` profile (with `autoconnect no`), and the profile is only taken down after setup, never deleted, so it lives on `/storage` (the bind-mounted `system-connections`) until the card is reflashed. The app reads the password back with `sudo nmcli -s` and shows it, with a Wi-Fi QR code, only to a loopback client (the mirror's own screen); phones on the hotspot can spoof the Host header but not their address.
* **Rationale**: Minimizes appliance maintenance and makes the device plug-and-play across different network environments without requiring terminal access or physical disassembly.

## 15. Watchdog and Time Synchronization Boot Guard
* **Decision**: Enabled the kernel hardware watchdog (`RuntimeWatchdogSec=14s` in `/etc/systemd/system.conf`) and modified the core systemd service file to require synchronization with network online and time wait-sync targets before startup.
* **Rationale**: Ensures the system restarts automatically if a deadlock occurs, and prevents module SSL handshake failures at startup due to the Raspberry Pi's lack of a hardware RTC battery.

## 16. Failsafe A/B Virtual Environment Updates
* **Decision**: Redirected the virtual environment `.venv` from the read-only root partition to the persistent writeable `/storage` partition via a symlink. When updates or module installations/removals are performed, they are staged in a cloned alternative directory (`venv_a` or `venv_b`). Once successful, the symlink is atomically updated.
* **Rationale**: Prevents package upgrades from bricking the system in the event of an update failure (network drops, syntax errors, or incompatible package versions). The boot-time hydration script only seeds `venv_a` and the link when the link is missing or dangling; it never resets a valid link, otherwise every reboot would silently revert A/B updates to the factory `base_venv`.

## 17. Boot Fallback Launcher and Settings Restoration
* **Decision**: Implemented a boot launcher script (`launch.sh`) that monitors the startup lifespan of the application. If the primary virtual environment fails to boot successfully within 10 seconds, it automatically rolls back to the previous stable state (`venv_old`) or fallback boots the read-only Golden Copy (`base_venv` in Safe Mode), alerting the user via UI status banners. Additionally, user configurations (SSH state, timezone, and shadow-crypt password hash) are programmatically re-applied on boot.
* **Rationale**: Maintains a high standard of consumer appliance resilience and security under OverlayFS, ensuring the device remains accessible and self-healing.

## 18. Background Jobs and Restart Detection in the Admin UI
* **Decision**: Long package operations (module install/upgrade/uninstall, core update, venv rebuild, backup restore) run as a single in-memory background job (`start_job` in `api/admin_shared.py`). The HTMX request returns immediately with a script that opens the progress overlay; the UI then polls the authenticated `/admin/jobs/current`. Every process start gets a random `BOOT_ID`, reported by `/health` and the job endpoint, and a restart counts as finished only when that id changes. All `hx-confirm` prompts go through the app's own `showConfirm()` dialog via one `htmx:confirm` handler, and button spinners use HTMX's `.htmx-request` state (`.htmx-indicator` / `.htmx-normal`, `hx-disabled-elt`) instead of being set in `onclick`.
* **Rationale**: On a Pi 3 these operations take minutes, longer than nginx's 60 s proxy timeout, so a synchronous request could fail while the work continued silently. Inferring a restart from timings ("down after 5 s, then up") broke on flaky Wi-Fi and left the overlay spinning. Spinners set in `onclick` started before the confirm dialog and were never reset, even when the user cancelled. One job at a time matches the A/B venv swap, which cannot run concurrently, and a restart ends every job, so no persistence is needed.

## 19. Sensors and Inputs via Kernel Device-Tree Overlays
* **Decision**: Everything on the GPIO header is a list of devices (`system.devices`, at most one per type: `button`, `pir`, `mmwave`, `dht11`, `light`, and one of `fan` (`gpio-fan`, on/off) or `pwm_fan` (`pwm-gpio-fan`, four speed steps)), and every device is a Raspberry Pi kernel driver instead of a Python GPIO library. A root-owned helper (`mirrordash-gpio-overlays`, allowed in sudoers) takes typed arguments (`button:17`, `light:0x23`), validates them against a fixed vocabulary and writes the matching overlays into a managed `[all]` block of `config.txt`; the firmware applies them at the next boot. `gpio-key` overlays turn the button, PIR and mmWave sensor into input devices (`KEY_PROG1`–`KEY_PROG3`) that `hardware.GpioInputs` reads as raw evdev events; `PressClassifier` turns button down/up times into single/double/triple/long presses. The DHT11 (`dht11` overlay) and the BH1750 light sensor (`i2c-sensor` overlay, enables I²C) are read by one generic IIO reader from `/sys/bus/iio/devices`, cached for 30 s. `hardware.sync_gpio_overlays()` validates the list (pins, I²C reserving GPIO 2/3) and only calls the helper when the overlay arguments change; the change is marked pending with the kernel boot id until the mirror restarts. A failure there never blocks saving other settings. Modules get the hardware through the event bus: `hardware.button` `{"press", "action"}`, `hardware.motion` `{"motion", "sensor"}` on every change (`motion` is true while any presence sensor sees someone), `hardware.climate` `{"temperature_c", "humidity"}`, `hardware.light` `{"lux"}` and `hardware.fan` `{"level", "max_level", "cpu_temperature_c"}` every 30 s. The fan itself is run by the kernel's thermal framework; the app only reads its state from `/sys/class/thermal`. Module subscriptions are cleared whenever the modules are reloaded, since the reload creates new instances.
* **Rationale**: The app runs on uv's Python 3.14, where `gpiozero`/`RPi.GPIO` are not installed and `lgpio` cannot be built on the device, so the previous GPIO code silently did nothing. The kernel debounces inputs and handles sensor timing far more reliably than Python polling, with no extra dependency. One device list, one IIO reader and one input reader mean a new sensor type is mostly one entry in `DEVICE_TYPES` plus one line in the helper. Changes only happen at boot because overlays applied by the firmware can't be removed at runtime. Only a fixed set of types is accepted by the root helper, so the app can never write arbitrary content into `config.txt`.

## 20. Screen Wake Timer
* **Decision**: `DisplayPowerManager` combines a base mode (`manual` = always on, `interval` = schedule, `wake` = off until woken) with one wake timer. Presence sensors (if enabled), the push button, the admin page and the open `POST /admin/screen` endpoint wake the screen for `timeout_minutes` (or a per-call `timeout_minutes`). With `extend` the countdown restarts on every wake and stands still while someone is present; without it the screen goes off a fixed time after the first wake. An explicit "off" wins until the next wake or base-mode change. The decision is a single method (`desired_state`) that gets the time and presence passed in, and commands wake the loop through an `asyncio.Event` instead of waiting for the next tick.
* **Rationale**: The PIR mode was a hard-coded special case of the same idea. One timer covers presence, buttons and home automation calls the same way, and the two counting strategies cover both "stay on while I'm here" and "show it briefly". An API call without a timeout gets the default time, so the screen can never be left on by mistake.

## 21. Module Scripts Receive Their Shadow Root
* **Decision**: The kiosk wraps every inline `<script>` in a module's HTML as `(function (root) { … })(…)`, where `root` is that module instance's shadow root. The root is kept across re-renders, so a script keeps per-instance state such as a timer on it (`clearInterval(root._timer); root._timer = setInterval(…)`).
* **Rationale**: Module HTML lives in a shadow root, and the HTML spec makes `document.currentScript` null there, so scripts had no reliable way to find their own markup; looking themselves up by `data-module` broke whenever the instance id differed from the package name, and globals made two instances of a module share one timer. One wrapper in the core gives every module the same answer without a client-side API.

## 22. Releases Are Made From What Was Tested
* **Decision**: One local script, `scripts/release.py`, makes every release, and each real release follows a test. The app is first published as a PyPI pre-release (`X.Y.ZrcN`) that only mirrors with `system.prerelease` ("Test versions") are offered and install (`uv pip install --prerelease=allow`); the release then bumps to `X.Y.Z`. The OS image is built only on request into a workflow artifact, flashed and checked, and the release publishes that artifact, so nothing is rebuilt. `publish.yml` refuses a tag that isn't `v` + the `pyproject.toml` version. Versions are compared with `config.version_key`, which orders a release above its own pre-releases.
* **Rationale**: Releases were made by hand in the GitHub UI, nothing tied the tag to the published version, an OS image was rebuilt at tagging so the shipped file was never the tested one, and there was no way to try an app version on one mirror before all of them got it. The script keeps the GitHub CLI as its only extra tool and the mirror gains no dependency (`packaging` isn't installed there).

## 23. Shared Building Blocks for Modules: Layout Classes and fetch_json
* **Decision**: The kiosk puts the module building blocks (typography scale, `.text-*`, `.flex-*`, `.module-message`) into every module's shadow root together with the design tokens (`static/js/kiosk/design-tokens.js`), and the module loader gives every module `self.fetch_json(url, headers=…, params=…)` next to `render_template` and `translate`. It returns `(data, error)`: on any failure the last good answer (kept in the module's `cache_dir`) and one of `rejected`, `offline`, `http <code>`, `invalid`. It logs only host and path. Module templates in `mirrordash-sdk` build on both.
* **Rationale**: The module guide recommended classes that only existed in the global stylesheet, which a shadow root never sees, so every module wrote its own CSS. Five modules each had their own fetch, JSON, timeout and error code, with different bugs (an error branch that could never run, a network error read as "nothing today", an API key in the log). One helper in the core fixes that once, needs no dependency (urllib in a thread), and keeps modules free of imports from the core.
