# 🪞 MirrorDash

[![Python Version](https://img.shields.io/badge/python-3.14%2B-blue.svg)](pyproject.toml)
[![License: PolyForm_NC_1.0.0](https://img.shields.io/badge/license-PolyForm_NC_1.0.0-525252)](LICENSE.md)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-orange.svg)](#get-started)
[![PyPI version](https://img.shields.io/pypi/v/mirrordash.svg)](https://pypi.org/project/mirrordash/)
[![Downloads](https://pepy.tech/badge/mirrordash)](https://pepy.tech/project/mirrordash)

**A calm display for your hallway mirror: the time, the weather and your day, glowing through the glass. No touchscreen, no voice assistant, no notifications.**

![MirrorDash on a mirror](https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_concept.png)

### From SD card to mirror in three steps

1. **Flash** the MirrorDash image onto an SD card and put it in the Pi.
2. **Join** the mirror's own Wi-Fi with your phone (scan the QR code on the screen) and pick your home network.
3. **Done.** Open `mirrordash.local` on your phone to choose what the mirror shows.

No keyboard, no terminal, no config files, now or later.

## Why it's different from the smart mirror you built last time

| | A typical DIY smart mirror | MirrorDash |
|---|---|---|
| Setting it up | Keyboard, terminal, config files | Join its Wi-Fi from your phone, pick your network |
| Changing things | Edit a file over SSH | Your phone, in the admin page |
| An update goes wrong | A black screen, and an evening of debugging | It goes back to the last version by itself |
| The SD card | Wears out from constant writing | The system part is read-only |
| The screen | On all night | On when someone's there, on a schedule, or from Home Assistant |
| New widgets | Copy code into the project | Tap *Install* in the admin page |

![The mirror's screen](https://raw.githubusercontent.com/Menturan/MirrorDash/master/screenshot.png)

## Get started

**You need**

* A Raspberry Pi 3 or newer (MirrorDash is developed and tested on a Pi 3 B).
* An SD card of at least 8 GB.
* The official power supply (5.1 V, 2.5 A for a Pi 3). A weaker one makes the mirror slow, and it warns you.
* A screen behind a two-way mirror.

**Then**

1. Download the latest image (`mirrordash-os-….img.xz`) from [Releases](https://github.com/Menturan/MirrorDash/releases): the newest release whose name ends in `-os1`, `-os2`, ….
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (*Choose OS* → *Use custom*).
3. Start the mirror. On its first start it restarts once by itself, then shows the Wi-Fi setup. The [user guide](USER_GUIDE.md#2-first-start-wi-fi-setup) takes it from there.

## Modules

The clock comes with the mirror. Install the others in the admin page under **Modules → Discover New Modules**.

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

This creates a module with example data and starts a mirror on your computer that shows every change as you save. Put the module on GitHub as `mirrordash-<something>` and publish a GitHub Release: it then shows up in every mirror's Discover list. The [SDK](https://github.com/Menturan/mirrordash-sdk) and its [module guide](https://github.com/Menturan/mirrordash-sdk/blob/master/MODULE_GUIDE.md) have the details.

## Under the hood

* **Server:** FastAPI (Python 3.14). Modules push their content to the screen over a WebSocket, and a page that reconnects gets everything again right away.
* **Screen:** plain HTML, CSS and JavaScript without a build step, shown full screen by Cog (WPE WebKit) on labwc. Every mirror has its component library at `/design`.
* **Modules:** each is its own Python package, found through entry points. A module that crashes is restarted on its own.
* **System:** Raspberry Pi OS (Debian 13) with a read-only root (overlayroot). Settings, modules and Wi-Fi live on a separate partition. Updates go into a second environment, and the mirror switches back if the new one doesn't start.

## Documentation

* [User guide](USER_GUIDE.md): setting up, wiring, the screen's rules, recovery, Home Assistant.
* [Changelog](CHANGELOG.md): what's new in each release.
* [Architecture](ARCHITECTURE.md) and [Design system](DESIGN.md): for contributors.
* [Golden image](GOLDEN_IMAGE.md) and [Releasing](RELEASING.md): how the OS image is built and released.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md): free to use and change for personal, non-commercial use.
