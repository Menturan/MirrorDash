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
- [12. Unified Multi-Provider Weather Data Model](#12-unified-multi-provider-weather-data-model)
- [13. SMHI API Response Structure](#13-smhi-api-response-structure)
- [14. Persistent Config and Modules Relocation for PyPI Packages](#14-persistent-config-and-modules-relocation-for-pypi-packages)
- [15. Primary Persistent Storage Path (Directory Contract)](#15-primary-persistent-storage-path-directory-contract)
- [16. WiFi Fallback / Captive Portal State Machine](#16-wifi-fallback--captive-portal-state-machine)
- [17. Watchdog and Time Synchronization Boot Guard](#17-watchdog-and-time-synchronization-boot-guard)
- [18. Failsafe A/B Virtual Environment Updates](#18-failsafe-ab-virtual-environment-updates)
- [19. Boot Fallback Launcher and Settings Restoration](#19-boot-fallback-launcher-and-settings-restoration)

---

## 1. Peripheral Modular Grid Layout
* **Decision**: Snaps mirror modules into 9 distinct grid regions (`top_left`, `top_center`, `top_right`, `middle_left`, `middle_center`, `middle_right`, `bottom_left`, `bottom_center`, `bottom_right`) using CSS Grid, keeping the center void clear.
* **Rationale**: Designed for ambient heads-up display (HUD) observation through semi-reflective glass. Centering information is restricted to maintain the primary reflective function of the mirror.

## 2. Server-Side Rendering (SSR) via Jinja2 & WebSocket Push
* **Decision**: Modules run their own event loops on the Python backend, render their HTML templates using Jinja2, and push the fully formed HTML payload to the client via WebSockets.
* **Rationale**: Keeps the kiosk client extremely thin, lightweight, and framework-agnostic. The client only needs to mount and transition raw HTML strings into designated container divs, eliminating complex client-side state managers or rendering libraries.

## 3. Dynamic Module Discovery via Python Entry Points
* **Decision**: Rather than hardcoding modules in the loader, the system automatically discovers installed module packages dynamically using Python `importlib.metadata` entry points (group: `mirrordash.modules`).
* **Rationale**: Allows third-party modules to be developed, packaged, and installed completely independently as standard Python wheels or editable packages. The core platform discovers them implicitly on startup.

## 4. Config-Driven Lifespan & Hot Reloading
* **Decision**: System configurations stored in `config.json` dictate module coordinates, custom properties, and enabled states. Changes to the config trigger a soft reload: stopping, cancelling, and garbage-collecting running loops, and then spinning up newly configured instances.
* **Rationale**: Users can customize their mirror layouts and parameters dynamically without restarting the Uvicorn web server or causing full browser disconnects.

## 5. OverlayFS and Hardware Remounting Integration
* **Decision**: Admin/system modification commands (such as module installations or configuration updates) execute remounting scripts (`mount -o remount,rw /`) before performing operations, and switch back to read-only (`remount,ro`) immediately after completion.
* **Rationale**: Protects SD card longevity when deployed on Raspberry Pi systems running OverlayFS (Read-Only OS configuration), while still allowing seamless software administration.

## 6. Hybrid Template Loader Resolution
* **Decision**: Implemented a `ChoiceLoader` combining standard Jinja2 `PackageLoader` with a fallback `FileSystemLoader` that resolves physical package paths on disk using `importlib.util.find_spec`.
* **Rationale**: Resolves `TemplateNotFound` errors on PEP 660 editable installations (common in local dev/testing environments like Hatch/uv) where zipped virtual package paths can hide standard template subdirectories.

## 7. Scope Isolation for Dynamic Module Helpers
* **Decision**: Auto-injected helpers (such as `render_template` and `translate`) are bound to their respective module instances using factory closure functions (`make_render_template` and `make_translate`).
* **Rationale**: Solves Python's loop lexical closure late-binding behavior. Without these factories, nested helper definitions reference the loop variable by name, resulting in all module instances executing translations and templates using the scope of whichever module loaded last.

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

## 12. Unified Multi-Provider Weather Data Model
* **Decision**: Decoupled the weather module frontend (HTML/CSS/Jinja2 template) from specific weather provider APIs by implementing a unified Python weather dictionary model. Supported APIs include SMHI (`snow1g/version/1`), Open-Meteo, WeatherAPI.com, and OpenWeatherMap.
* **Rationale**: Weather sites and APIs change formats, endpoints, and codes frequently. Decoupling them allows the HTML layout, translations, and Lucide icons to remain completely unchanged even if backend APIs update or new weather providers are added. It also ensures consistent localization, temperature/wind unit formatting, and cardinal wind abbreviations across all selected providers.

## 13. SMHI API Response Structure
* **Decision**: The `mirrordash-weather` module uses the SMHI `snow1g/version/1` point forecast endpoint. Parsing reads the `timeSeries` camelCase array, each entry's `validTime` field, and extracts weather values directly from the flat parameter dictionary representation.
* **Rationale**: The SMHI opendata `snow1g` API uses a flat dictionary representation for parameters in each time series entry. Documenting this here prevents documentation drift and keeps it in sync with the codebase.

## 14. Persistent Config and Modules Relocation for PyPI Packages
* **Decision**: Migrated the primary location of `config.json` and custom local modules out of the package installation directory (which is read-only and wiped on package updates) into the user's home directory (`~/.mirrordash/config.json` and `~/.mirrordash/modules/`).
* **Rationale**: Allows the core platform to be installed and run cleanly as a standard PyPI package. User configurations and custom module directories are preserved across upgrades, while still allowing developers to run from a local cloned git workspace via fallbacks.

## 15. Primary Persistent Storage Path (Directory Contract)
* **Decision**: Adjusted the primary persistent configuration storage path to `~/.mirrordash/data/config.json` and module persistent data to `~/.mirrordash/data/<module-name>/`. High-frequency ephemeral cache files are placed in `~/.mirrordash/cache/<module-name>/`.
* **Rationale**: Aligns the platform with the locked read-only system blueprint (OverlayFS). Under read-only systems, `~/.mirrordash/cache/` is mapped directly to a RAM-disk tmpfs buffer to eliminate physical SD card wear and ensure crash immunity. `~/.mirrordash/data/` acts as the persistent sector.

## 16. WiFi Fallback / Captive Portal State Machine
* **Decision**: Implemented an automated fallback WiFi captive portal setup state machine. If network connectivity is not verified within 30 seconds of system boot, NetworkManager shifts `wlan0` to an autonomous Access Point (AP) setup hotspot. The FastAPI backend detects requests to `10.42.0.1` (or `?captive=true`) and redirects the user to `/wifi-setup`. Submitting credentials remounts the filesystem read-write, saves the new NetworkManager profiles, remounts read-only, and reboots the OS back into client mode.
* **Rationale**: Minimizes appliance maintenance and makes the device plug-and-play across different network environments without requiring terminal access or physical disassembly.

## 17. Watchdog and Time Synchronization Boot Guard
* **Decision**: Enabled the kernel hardware watchdog (`RuntimeWatchdogSec=14s` in `/etc/systemd/system.conf`) and modified the core systemd service file to require synchronization with network online and time wait-sync targets before startup.
* **Rationale**: Ensures the system restarts automatically if a deadlock occurs, and prevents module SSL handshake failures at startup due to the Raspberry Pi's lack of a hardware RTC battery.

## 18. Failsafe A/B Virtual Environment Updates
* **Decision**: Redirected the virtual environment `.venv` from the read-only root partition to the persistent writeable `/storage` partition via a symlink. When updates or module installations/removals are performed, they are staged in a cloned alternative directory (`venv_a` or `venv_b`). Once successful, the symlink is atomically updated.
* **Rationale**: Prevents package upgrades from bricking the system in the event of an update failure (network drops, syntax errors, or incompatible package versions).

## 19. Boot Fallback Launcher and Settings Restoration
* **Decision**: Implemented a boot launcher script (`launch.sh`) that monitors the startup lifespan of the application. If the primary virtual environment fails to boot successfully within 10 seconds, it automatically rolls back to the previous stable state (`venv_old`) or fallback boots the read-only Golden Copy (`base_venv` in Safe Mode), alerting the user via UI status banners. Additionally, user configurations (SSH state, timezone, and shadow-crypt password hash) are programmatically re-applied on boot.
* **Rationale**: Maintains a high standard of consumer appliance resilience and security under OverlayFS, ensuring the device remains accessible and self-healing.



