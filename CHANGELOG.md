# Changelog

What's new in MirrorDash, newest first.

There are two kinds of releases:

- **App releases** (`0.4.0`) update the MirrorDash software. Existing mirrors get them through *Updates* in the admin page.
- **OS image releases** (`0.4.0-os1`) are a new SD card image to flash. Each one contains the app release with the same number.

## [Unreleased]

### Added
- Connect a push button and choose what a single, double, triple and long press does: turn the screen on or off, restart MirrorDash, restart the mirror, or shut it down. Replaces the old screen on/off button setting.
- See the room's temperature, humidity and light level on the dashboard.
- Restart and shut down the mirror from the Power tab.
- A screen timeout: the screen can be "off until woken", or off outside its schedule, and light up for a while when someone is in front of the mirror, the button is pressed, or Home Assistant calls `/admin/screen` (optionally with its own time). Choose whether every new activity keeps it on longer or it stays on a fixed time. The Power tab explains each choice.
- Connect a fan that cools the Pi by CPU temperature: on/off (2 or 3 wires, with a transistor) or a 4-wire PWM fan with speed steps.
- A "Sensors & Inputs" list in the Hardware tab: add a push button, a PIR motion sensor, an mmWave presence sensor (also notices someone standing still), a DHT11 temperature & humidity sensor or a BH1750 light sensor, with wiring hints for each.
- Modules can react to the push button, presence and the room sensors (for module developers: the `hardware.button`, `hardware.motion`, `hardware.climate` and `hardware.light` events).
- A button on GPIO 3 can also start the mirror again after it has been shut down (shown as a tip in the Hardware tab).

### Changed
- Hardware and Power settings are applied as soon as you change them; the Apply buttons are gone.
- Installing, updating and removing modules shows progress right away, with a timer, and the page knows exactly when the mirror is back. If something goes wrong you see what failed.
- The admin page opens faster, also when the mirror has no internet connection.
- Update checks are remembered for 10 minutes instead of being repeated on every page view.
- The System Analytics values on the dashboard (temperature, memory, network, uptime and more) update every 10 seconds while the Dashboard tab is open.
- Settings are saved as soon as you change them, like Hardware and Power.
- Restart MirrorDash has moved from the top of every page to the Power tab, next to Restart Mirror and Shut Down, with a short explanation of the difference.
- Clearer, plain-language headings and descriptions on every tab, including what a backup does and doesn't contain.
- The Modules tab gets straight to the point: no banner, clearer filter buttons, and storage is only mentioned when it runs low.
- Modules without their own description show their package description.
- A new Wi-Fi setup page for the first start: tap your network in a list, type the password (with a Show button), and the page tells you where to find the mirror afterwards (mirrordash.local). The time zone is taken from your phone. The text on the mirror during setup is simpler too.
- The dashboard shows which module sits where on the screen (turned-off modules dimmed), not just which positions are in use.
- Logs: errors and warnings are coloured, a filter box shows only matching lines, and the log fills the screen.

### Fixed
- Loading spinners no longer keep spinning after a task has finished or was cancelled.
- On phones, all tabs fit in the bottom bar, only the selected tab is highlighted, and the Backup page no longer hides the tab bar.
- On phones, the saved backups' Restore, Download and Delete buttons are no longer cut off, the dashboard values fit two per row, and the screen buttons in the Power tab no longer run off the edge.
- Help texts are easier to read (higher contrast).
- Switching tabs quickly no longer sometimes shows the previous tab.
- Log text is always shown as plain text in the admin page.
- Wi-Fi setup no longer ends with "Network request failed" when the mirror leaves its setup network to connect.
- A wrong Wi-Fi password during setup no longer leaves the mirror without any network until it's unplugged: it restarts and opens MirrorDash-Setup again.
- Modules no longer show "Update available" again right after being updated.
- The update banner on the dashboard is only about MirrorDash itself and opens Settings, where the update is installed. Module updates are shown on each module in the Modules tab.
- The screen layout overview on the dashboard shows the real 3×3 grid on phones.
- Confirmation questions use the admin page's own dialog instead of the browser's pop-up.
- The admin page no longer shows system details (network, IP address, modules) before you have logged in.
- Error messages are always shown in the admin page; some failed actions used to show nothing at all.
- The PIR motion sensor works again: it never did on the OS image, because the app's Python had no GPIO library. The screen can now also be controlled by an mmWave presence sensor.
- Modules no longer receive the same event several times after settings have been saved.
- On first start without Wi-Fi, the mirror switches to the Wi-Fi setup screen by itself instead of staying on the admin password screen, and phones that join the setup hotspot are sent straight to Wi-Fi setup.

### OS image
- Allows shutting down and setting the button and sensor pins from the admin page (needed for the features above).
- Updates and installed modules are no longer undone when the mirror restarts.
- On first start without Wi-Fi, the Wi-Fi setup screen is shown right away.
- The SD card is now protected automatically: the mirror switches to read-only mode by itself after its first start (this takes one extra restart), and only once the storage partition is ready.
- Wi-Fi no longer stalls for long periods on the Raspberry Pi 3 (Wi-Fi power saving is turned off).
- The browser no longer crashes a couple of times on every start-up.
- Every image contains exactly the app version it is released with, built from fixed versions of its tools.
- Source code and developer settings are no longer included in the image.

## [0.4.0-os1] - 2026-10-06

OS image with MirrorDash 0.4.0.

### Added
- The clock module comes pre-installed and enabled.

## [0.4.0] - 2026-10-06

### Added
- Use several copies of the same module, for example two clocks in different time zones.
- Modules float freely in their screen area, with optional maximum width and height, layering and transparency per module.
- Screen margin setting for each edge, for frames that cover part of the screen.
- Forgot your admin password? A recovery PIN is shown on the mirror screen. The same works if the password settings get damaged.
- Modules can have their own icons in the admin page.

### Changed
- The clock is now a separate module instead of being built in.
- Module updates from GitHub only use published releases.
- Confirmation messages in the admin page match the rest of the design instead of using browser pop-ups.

### Fixed
- The admin page no longer freezes while the list of community modules loads.
- Modules no longer overflow the screen height.
- Fixed login loops and a crash when opening module settings.

## [0.3.4-os1] - 2026-07-01

OS image with MirrorDash 0.3.4.

### Fixed
- The mouse pointer is hidden on the mirror.
- Saved Wi-Fi networks are no longer damaged.

## [0.3.4] - 2026-06-29

### Added
- Phones that join the setup hotspot open the Wi-Fi setup page automatically.
- A restart screen in the admin page while the mirror restarts.

## [0.3.3-os1] - 2026-06-29

OS image with MirrorDash 0.3.3.

### Fixed
- Wi-Fi passwords are stored safely and survive restarts.
- Several fixes for creating the storage partition on first start.

## [0.3.3] - 2026-06-29

### Added
- An "Offline" notice on the mirror when the internet connection is lost.
- A refresh button for the community module list.

### Fixed
- Setting up Wi-Fi from the setup hotspot works again.
- No more black screen at start-up while the mirror checks for Wi-Fi.
- Fixed crashes in the module settings panel and when restoring the system password.

## [0.3.2-os1] - 2026-06-28

OS image with MirrorDash 0.3.2.

### Fixed
- The storage partition is created automatically on first start.
- The mirror no longer gets stuck in recovery mode or on a black screen during start-up.
- The browser cache is cleared every hour so the mirror doesn't run out of memory.
- The browser no longer gives up after crashing a few times during start-up.

## [0.3.2] - 2026-06-28

### Added
- Community modules published on GitHub show up in the admin page.

### Fixed
- Screen on/off scheduling works right after start-up.

## [0.3.1-os1] - 2026-06-25

OS image with MirrorDash 0.3.1.

### Changed
- Start-up rebuilt for reliability: the screen, browser and Wi-Fi setup start in a fixed order, and an update that fails to start is rolled back automatically.

## [0.3.1] - 2026-06-25

### Fixed
- Updates and backup restores are more reliable.

## [0.3.0-os2] - 2026-06-23

OS image with MirrorDash 0.3.0.

### Fixed
- Fixed boot files being installed in the wrong place, which could stop the image from starting.

## [0.3.0-os1] - 2026-06-23

OS image with MirrorDash 0.3.0.

### Fixed
- Automatic login to the mirror screen works.

## [0.3.0] - 2026-06-23

### Added
- Community modules from PyPI are listed in the admin page.
- The admin page remembers which tab you were on.

### Changed
- The admin page was rebuilt and loads faster.
- Clearer instructions on the Wi-Fi setup screen.
- Modules can no longer break each other's styling.
- Icons and fonts work without an internet connection.

## [0.2.4-os1] - 2026-06-16

OS image with MirrorDash 0.2.4.

### Added
- First OS image built automatically, with a checksum file to verify the download.
- Quiet start-up without boot messages on the screen.

## [0.2.4] - 2026-06-15

### Added
- Wi-Fi setup and the loading screen work without an internet connection.

### Fixed
- Saved Wi-Fi networks survive restarts.

## [0.2.3] - 2026-06-14

### Fixed
- Settings and styling fixes.

## [0.2.2] - 2026-06-12

### Fixed
- Installing MirrorDash from PyPI works.

## [0.2.1] - 2026-06-12

Maintenance release; no changes for users.

## [0.2.0] - 2026-06-12

First release.

- Reach the mirror at `mirrordash.local`, with no IP address or port number.
- The mirror asks you to set an admin password on first start.
