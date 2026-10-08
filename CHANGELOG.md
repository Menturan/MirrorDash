# Changelog

What's new in MirrorDash, newest first.

There are two kinds of releases:

- **App releases** (`0.4.0`) update the MirrorDash software. Existing mirrors get them through *Updates* in the admin page.
- **OS image releases** (`0.4.0-os1`) are a new SD card image to flash. Each one contains the app release with the same number.

## [Unreleased]

### Fixed
- The clock (or any module) no longer keeps pulsing when the mirror's screen switches to the mirror page right after setup.

### OS image
- Wi-Fi follows the country of the mirror's time zone (taken from your phone during Wi-Fi setup), from the next restart. Before, every mirror used the US rules, and could not see networks on channel 12 or 13, which are common in Europe.
- Settings, Wi-Fi, installed modules and updates are kept when the mirror restarts. Before, the read-only protection also covered the storage partition, so everything saved after the first start was lost at the next restart.
- The Wi-Fi password is no longer written in plain text to the system log (also shown on the admin page's Logs tab).
- The mirror uses compressed memory as swap, so it no longer runs out of memory as easily. Before, swap failed to start and a failing system service was retried every time something started.
- Brightness on HDMI screens (ddcutil).
- Allows a second, third and fourth push button.
- Each mirror gets its own password for the MirrorDash-Setup network instead of `mirrordash`. The mirror shows it during Wi-Fi setup, and it stays the same until the SD card is flashed again.
- Includes `zip`, which creating a backup needs.
- Comes with clock module 1.1.1, where the clock ticks again (each clock on the screen keeps its own time) and no longer shifts sideways or shimmers as the seconds change. Before, the mirror offered the old clock as an "update".
- No mouse pointer in the top left corner of the screen.
- No package lists downloaded in the background (the app updates itself, the OS comes as a new image).
- The start, restart and shutdown screens show a slowly breathing monogram, so a long start never looks frozen.
- A sharp, pure-black start screen with the MD monogram, instead of a grainy picture that was scaled down (and blurry) on smaller screens. Restarting and shutting down now show their own screens; before, the screen just went black.
- Phones say "Sign in to network" when they join MirrorDash-Setup, like on a hotel Wi-Fi, and open the setup page by themselves. No address needs to be typed (if needed: http://mirrordash.setup).
- Allows shutting down and setting the button and sensor pins from the admin page (needed for the features above).
- Updates and installed modules are no longer undone when the mirror restarts.
- On first start without Wi-Fi, the Wi-Fi setup screen is shown right away.
- The SD card is now protected automatically: the mirror switches to read-only mode by itself after its first start (this takes one extra restart), and only once the storage partition is ready. This also works when the mirror has no Wi-Fi yet at its first start; before, it then silently stayed writable.
- Wi-Fi no longer stalls for long periods on the Raspberry Pi 3 (Wi-Fi power saving is turned off).
- The browser no longer crashes a couple of times on every start-up.
- Every image contains exactly the app version it is released with, built from fixed versions of its tools.
- Source code and developer settings are no longer included in the image.

## [0.6.0] - 2026-10-08

### Added
- Home Assistant can show the mirror's status and sensors, turn the screen on and off and set the brightness. Create a token under Hardware → API Access; the user guide has a ready setup to paste.
- Up to four push buttons, each with its own actions.
- Choose how long a long press is (1, 1.5, 2 or 3 seconds). It is 1.5 seconds to begin with; before, it was 1 second.
- Add the admin page to your phone's home screen and it opens full screen, with the MD icon (also on iPhone).
- A "Turn on SSH" button next to the new SSH password; before, only Enter saved it.
- The MD monogram on the Wi-Fi setup screens and the admin password screens.

### Changed
- Messages in the admin page float over it, instead of pushing the whole page down and back up.
- When the phone wakes up with the admin page open, it says "Reconnecting to the mirror…" and only says it can't reach the mirror after 20 seconds without an answer.
- After Wi-Fi setup the mirror no longer restarts: it shows its clock right away. With a wrong Wi-Fi password, MirrorDash-Setup comes back within a minute (same password) instead of after a restart.
- Installing or updating a module names it plainly, for example "mirrordash-clock (v1.0.1)", instead of showing its full web address.

### Fixed
- Brightness works on HDMI screens that understand DDC/CI (most computer monitors). Before, the setting did nothing on HDMI; now the Hardware tab says when a screen can't take it. Needs the new OS image.
- The progress window while a module is installed, updated or removed, MirrorDash is updated or restarted, was invisible, so nothing seemed to happen. It shows again.

## [0.5.0] - 2026-10-08

### Added
- The Wi-Fi setup screen on the mirror shows a QR code: point your phone's camera at it to join the setup network without typing.
- Try new versions of MirrorDash before everyone else: turn on "Test versions" under Settings → MirrorDash Updates.
- Connect a push button and choose what a single, double, triple and long press does: turn the screen on or off, restart MirrorDash, restart the mirror, or shut it down. Replaces the old screen on/off button setting.
- See the room's temperature, humidity and light level on the dashboard.
- Restart and shut down the mirror from the Power tab.
- A screen timeout: the screen can be "off until woken", or off outside its schedule, and light up for a while when someone is in front of the mirror, the button is pressed, or Home Assistant calls `/admin/screen` (optionally with its own time). Choose whether every new activity keeps it on longer or it stays on a fixed time. The Power tab explains each choice.
- Connect a fan that cools the Pi by CPU temperature: on/off (2 or 3 wires, with a transistor) or a 4-wire PWM fan with speed steps.
- A "Sensors & Inputs" list in the Hardware tab: add a push button, a PIR motion sensor, an mmWave presence sensor (also notices someone standing still), a DHT11 temperature & humidity sensor or a BH1750 light sensor, with wiring hints for each.
- Modules can react to the push button, presence and the room sensors (for module developers: the `hardware.button`, `hardware.motion`, `hardware.climate` and `hardware.light` events).
- A button on GPIO 3 can also start the mirror again after it has been shut down (shown as a tip in the Hardware tab).

### Changed
- For module developers: `self.fetch_json(url, headers=…)` fetches data with a timeout and falls back to the last answer when it fails, and every component on the design page (`/design`: data lists, forecast and agenda lists, gauges, stats, callouts, `.module-message` and more) now works inside modules: copy its markup into the module's template. The forecast and agenda components on the design page have their styling back.
- Installing a module by hand under Modules now asks for its GitHub address (Git URL) instead of a package name.
- For module developers: on a local mirror started with `mirrordash-sdk start`, saving a template, setting or translation file shows the change by itself, and the mirror no longer asks for your computer's password.
- For module developers: a module's script now gets its own part of the screen as `root`, so it no longer needs `document.currentScript` (which never worked there). Each copy of a module can keep its own timers there.
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
- The MD monogram is now the icon everywhere (browser tab, home screen), and the first screen after start looks exactly like the start screen before it.
- It's always visible that something is happening: a moving line while the mirror starts, a loading line at the top of the admin page while a tab loads, and animated dots on the Wi-Fi setup page.
- If MirrorDash doesn't start within 4 minutes, the mirror says so and what to do. If the mirror loses MirrorDash for more than 2 minutes, it shows "MirrorDash isn't responding" instead of reconnecting silently forever.
- A new Wi-Fi setup page for the first start: tap your network in a list, type the password (with a Show button), and the page tells you where to find the mirror afterwards (mirrordash.local). The time zone is taken from your phone. The text on the mirror during setup is simpler too.
- The dashboard shows which module sits where on the screen (turned-off modules dimmed), not just which positions are in use.
- Logs: errors and warnings are coloured, a filter box shows only matching lines, and the log fills the screen.

### Fixed
- Loading spinners no longer keep spinning after a task has finished or was cancelled.
- On phones, all tabs fit in the bottom bar, only the selected tab is highlighted, and the Backup page no longer hides the tab bar.
- On phones, the saved backups' Restore, Download and Delete buttons are no longer cut off, the dashboard values fit two per row, and the screen buttons in the Power tab no longer run off the edge.
- Help texts are easier to read (higher contrast).
- Restoring a backup keeps the admin password of the mirror you restore on; before, the old mirror's password came back after the restart. Backups no longer contain the admin password at all.
- After restoring a backup, the push button, sensors and fan work again: the mirror sets their pins and restarts itself.
- Modules installed from GitHub are installed again when you restore a backup (before, they went missing).
- The global settings (language, time zone, units) load on every mirror; before, they could fail to load when no installed module happened to include a language list.
- Switching tabs quickly no longer sometimes shows the previous tab.
- Log text is always shown as plain text in the admin page.
- Wi-Fi setup no longer ends with "Network request failed" when the mirror leaves its setup network to connect.
- A wrong Wi-Fi password during setup no longer leaves the mirror without any network until it's unplugged: it restarts and opens MirrorDash-Setup again.
- The Wi-Fi setup page always lists the networks the mirror found, and never its own MirrorDash-Setup network.
- Modules no longer show "Update available" again right after being updated.
- The update banner on the dashboard is only about MirrorDash itself and opens Settings, where the update is installed. Module updates are shown on each module in the Modules tab.
- The screen layout overview on the dashboard shows the real 3×3 grid on phones.
- Confirmation questions use the admin page's own dialog instead of the browser's pop-up.
- The admin page no longer shows system details (network, IP address, modules) before you have logged in.
- Error messages are always shown in the admin page; some failed actions used to show nothing at all.
- The PIR motion sensor works again: it never did on the OS image, because the app's Python had no GPIO library. The screen can now also be controlled by an mmWave presence sensor.
- Modules no longer receive the same event several times after settings have been saved.
- On first start without Wi-Fi, the mirror switches to the Wi-Fi setup screen by itself instead of staying on the admin password screen, and phones that join the setup hotspot are sent straight to Wi-Fi setup.

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
