<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/monogram-dark.svg">
    <img src="docs/monogram-light.svg" alt="MirrorDash" width="96">
  </picture>
</p>

# MirrorDash

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-525252)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-orange.svg)](#get-started)
[![PyPI version](https://img.shields.io/pypi/v/mirrordash.svg)](https://pypi.org/project/mirrordash/)

**A calm display for your hallway mirror: the time, the weather and your day, glowing through the glass. No touchscreen, no voice assistant, no notifications.**

**Website:** [mirrordash.net](https://mirrordash.net)

![MirrorDash on a mirror](https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_concept.png)

### From SD card to mirror in three steps

1. **Flash** the MirrorDash image onto an SD card and put it in the Pi.
2. **Join** the mirror's own Wi-Fi with your phone (scan the QR code on the screen) and pick your home network.
3. **Done.** Open `mirrordash.local` on your phone to choose what the mirror shows.

No keyboard, no terminal, no config files, now or later.

> **Status: beta.** Developed and tested on a Raspberry Pi 3 B, Matter with Apple Home and Google Home included. The image carries what a Pi 4 and a Pi 5 need, but hasn't been tried on them yet: [tell us](https://github.com/Menturan/MirrorDash/discussions) how it goes.

## Why it's different from the smart mirror you built last time

| | A typical DIY smart mirror | MirrorDash |
|---|---|---|
| Setting it up | Keyboard, terminal, config files | Join its Wi-Fi from your phone, pick your network |
| Changing things | Edit a file over SSH | Your phone, in the admin page |
| An update goes wrong | A black screen, and an evening of debugging | It goes back to the last version by itself |
| The SD card | Wears out from constant writing | The system part is read-only |
| The screen | On all night | On when someone's there, on a schedule, or from Apple Home, Google Home and Home Assistant (Matter) |
| New widgets | Copy code into the project | Tap *Install* in the admin page |

**Coming from MagicMirror²?** Same idea, a glance at the mirror on your way out. What's different is everything around it: the setup, the phone instead of a config file, the updates that undo themselves. MagicMirror² modules (JavaScript) don't run here; a MirrorDash module is a small Python package, and the [SDK](#make-your-own) makes one with example data in a minute.

![The mirror's screen](https://raw.githubusercontent.com/Menturan/MirrorDash/master/screenshot.png)

## Get started

**You need** (rough prices in 2026)

| Part | About | Notes |
|---|---|---|
| Raspberry Pi 3 B or newer | €35–60 | Developed on a Pi 3 B; a Pi 4 or 5 should work too. |
| SD card, 8 GB or more | €8 | |
| The official power supply | €10 | 5.1 V, 2.5 A for a Pi 3. A weaker one makes the mirror slow, and it warns you. |
| A screen with HDMI | €0–100 | An old computer monitor is ideal: most of them let the mirror set their brightness (DDC/CI), most TVs don't. Take it out of its case for a slim build. |
| A two-way mirror | €30–150 | Glass is the clearest; acrylic is cheaper and lighter but can warp; mirror film on plain glass is the cheapest. A bit larger than the screen. |
| A frame | €0–30 | Wood from the hardware store, or an old picture frame. |
| *Optional:* a motion sensor | €3–6 | A PIR sensor (HC-SR501) or an mmWave sensor (LD2410, which also notices someone standing still), so the screen is on only when someone's there. Buttons, a light sensor and a fan can be added too. |

All in, about €90–350, depending mostly on the screen and the mirror.

**Then**

1. Download the newest image at [mirrordash.net/download](https://mirrordash.net/download).
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (*Choose OS* → *Use custom*).
3. Start the mirror. On its first start it restarts once by itself, then shows the Wi-Fi setup. The [user guide](USER_GUIDE.md#2-first-start-wi-fi-setup) takes it from there, and its [quick start](USER_GUIDE.md#quick-start) has every step on one screen.

## Modules

The clock comes with the mirror. Install the others in the admin page under **Modules → Add modules**.

| Module | Shows |
|---|---|
| [Clock](https://github.com/Menturan/mirrordash-clock) | The time and date |
| [Weather](https://github.com/Menturan/mirrordash-weather) | The weather now and the forecast |
| [Calendar](https://github.com/Menturan/mirrordash-calendar) | Your upcoming events |
| [News](https://github.com/Menturan/mirrordash-news) | The latest headlines from built-in or your own sources |
| [Home Assistant](https://github.com/Menturan/mirrordash-homeassistant) | Sensors from Home Assistant |
| [Krisinformation](https://github.com/Menturan/mirrordash-krisinformation) | Swedish crisis information from Krisinformation.se |
| [Namnsdag](https://github.com/Menturan/mirrordash-namnsdag) | The Swedish name day |

### Make your own

```bash
uvx mirrordash-sdk quickstart mirrordash-my-widget
```

This creates a module with example data and starts a mirror on your computer that shows every change as you save. Put the module on GitHub as `mirrordash-<something>` and publish a GitHub Release: it then shows up under *Modules → Add modules* on every mirror. The [SDK](https://github.com/Menturan/mirrordash-sdk) and its [module guide](https://github.com/Menturan/mirrordash-sdk/blob/master/MODULE_GUIDE.md) have the details.

## Help and ideas

* **Questions, ideas, or a photo of your mirror:** [Discussions](https://github.com/Menturan/MirrorDash/discussions).
* **Something wrong:** an [issue](https://github.com/Menturan/MirrorDash/issues). *Logs → Diagnostics* in the admin page gathers what helps find the fault.

## Under the hood

* **Server:** FastAPI (Python 3.14). Modules push their content to the screen over a WebSocket, and a page that reconnects gets everything again right away.
* **Screen:** plain HTML, CSS and JavaScript without a build step, shown full screen by Cog (WPE WebKit) on labwc. Every mirror has its component library at `/design`.
* **Modules:** each is its own Python package, found through entry points, and runs in a sandboxed process as a user of its own. A module that crashes is restarted on its own.
* **System:** Raspberry Pi OS (Debian 13) with a read-only root (overlayroot). Settings, modules and Wi-Fi live on a separate partition. Updates go into a second environment, and the mirror switches back if the new one doesn't start.

## Documentation

* [User guide](USER_GUIDE.md) (also [on the website](https://mirrordash.net/guide.html)): setting up, wiring, the screen's rules, recovery, Matter, the API and Home Assistant.
* The website is `docs/` (GitHub Pages from master, no build step): `index.html`, and `guide.html`, which renders USER_GUIDE.md in the browser.
* [Changelog](CHANGELOG.md): what's new in each release.
* [Contributing](CONTRIBUTING.md): run and change the core on your computer.
* [Architecture](ARCHITECTURE.md) and [Design system](DESIGN.md): why it's built and looks the way it does.
* [Releasing](RELEASING.md): how the app and the OS image are released.

## License

Copyright (C) 2026 Jonas Öhlander. MirrorDash is dual-licensed:

* [GNU AGPL 3.0](LICENSE): free to use, change and share. If you share it, or let others use a changed version over the network, you share your source under the AGPL too.
* A commercial license for products that can't share their source: [ask Menturan on GitHub](https://github.com/Menturan).

Modules are separate projects with their own licenses; the [SDK](https://github.com/Menturan/mirrordash-sdk) is MIT.
