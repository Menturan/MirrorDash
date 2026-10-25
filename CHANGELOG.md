# Changelog

What's new in MirrorDash, newest first.

There are two kinds of releases:

- **App releases** (`0.4.0`) update the MirrorDash software. Existing mirrors get them through *Updates* in the admin page.
- **OS image releases** (`0.4.0-os1`) are a new system underneath MirrorDash, with the app release of the same number. Mirrors from the A/B image on get them under *Updates* too; a new mirror (or an older one) flashes the image.

## [Unreleased]

- Fixed: a module could have a backup or a restore read or write MirrorDash's own files, by swapping one of its folders while the backup was being made.

### OS image

- Modules no longer see the mirror's own system details (its processor, memory and kernel settings), only their own processes.
- Modules run apart from MirrorDash, as their own user: they can no longer read MirrorDash's settings, passwords and logins, change the mirror's system or network, or reach the devices on your home network. A module that needs one (like Home Assistant) reaches exactly the address you type in its settings; the admin page says so under that field. Modules can still use the GPIO pins, I²C and SPI, but of the I²C buses only the one on the GPIO header: a module can no longer change the screen's brightness or turn it off. A module that fills the SD card no longer keeps MirrorDash from saving its settings. A module that uses too much memory is stopped before it slows down the screen.
- Update the Home Assistant module when its new version is out: until then it can't reach Home Assistant. Type your Home Assistant address in its settings: the suggested one is only a hint now and opens nothing until you type it.
- Modules are installed by the modules' own user, apart from MirrorDash: installing or updating one never runs its code as MirrorDash. Modules installed before this version keep running, but can't be updated or removed: use *Reinstall MirrorDash* (it removes them) and install them again. The admin page logs an error if the modules' limits don't hold on your mirror.
- *Reinstall MirrorDash* (was *Reinstall MirrorDash and Modules*) reinstalls MirrorDash only; your modules stay as they are.
- Modules get only the GPIO pins they ask for, and only free ones: never the pins of the devices in the *Hardware* tab, the I²C pins while a light sensor is set up, or the Pi's own (like the SD card's).
- The mirror's screen can no longer reach devices on your home network either. A module that shows a camera at home needs a version that shows it through its own address.
- Two mirrors with Matter in one home are two devices in the home apps, not one. Resetting the pairing makes the mirror a new device.
- Safer: modules can no longer use full admin (root) rights on the mirror; the system's own leftover rule that allowed it is removed. The mirror's logs, developer logging and storage setup can no longer be tricked into overwriting system files, and Wi-Fi rights are limited to what the mirror uses.
- Backups larger than 1 MB can be uploaded through `mirrordash.local` (and the setup page).
- The mirror no longer gets stuck without Wi-Fi or setup network when several saved networks are out of range.
- *Power* → *USB*: turn the USB ports off on a Raspberry Pi 3 (or 1, 2, Zero), so the mirror runs a little cooler. The Ethernet port goes off with them, so the mirror needs Wi-Fi.

## [0.9.2] - 2026-10-25

- Modules that keep stopping soon after they start (one using up its memory, most likely) are paused after the third time, instead of starting over every few seconds; the *Dashboard* says so and starts them again with one button.
- A new push button is suggested on GPIO 3 (pin 5), the pin that also starts the mirror after it was shut down.

## [0.9.1] - 2026-10-25

- Modules can offer their own web addresses on the home network (`http://mirrordash.local/api/modules/<module>/...`), for example for a sensor or a phone shortcut to call.
- MirrorDash no longer answers on port 8000 from the home network, only at `http://mirrordash.local` (port 80) as before.
- Fixed: a module could run code in the admin page, through its icon or the texts of its settings, and so act as you while you were logged in.
- MirrorDash never runs a module's code to show the admin page or to check an update any more.
- Saved files of modules from before this version are gone (modules keep them in a folder of their own now). Usually they only remember the last answer they fetched.
- *Settings* → *Updates* → *Check for Updates* also checks every module, lists the ones with a new version and updates them all at once with *Update All Modules* (MirrorDash restarts once). It says when it last checked.
- The *Dashboard* says when modules have new versions, not only MirrorDash.
- A backup with a password is now properly encrypted, and also holds the admin password, the SSH password and the saved Wi-Fi networks, so a new mirror can be set up from it in one step. A backup without a password holds none of them. Backups made before this version can't be restored.
- Set up a new mirror from a backup: on the Wi-Fi setup page, tap *Set up from a backup instead*. The mirror joins a Wi-Fi network from the backup and restores everything else by itself.
- A backup no longer contains your login sessions, and a crafted backup file can no longer delete folders on the mirror. A restore that fails halfway no longer damages the installed modules.
- Saving a module's settings no longer flickers the screen or briefly drops the mirror from Home Assistant and other Matter apps.
- *Logs* → *Diagnostics* shows whether the modules' limits hold, how much memory they use and how often they restarted, and downloads one file with the versions and logs to send when asking for help (no settings or passwords in it).
- Fixed: updating or adding a module when one was already installed failed with "Operation not permitted".
- Fixed: a button that bounces no longer turns a double press into a triple, or a long press into short ones first.
- Fixed: after a restart the cards of an area could show in another order than the one you set (a card that loaded faster came first).
- Fixed: the mirror screen no longer jumps back to the first slide, or stops rotating, when a module in a carousel updates often.
- Fixed: the mirror screen reloads after MirrorDash restarts (a module installed or removed, a backup restored) instead of showing old content.
- Fixed: removing a module whose uninstall fails keeps its settings.
- Fixed: the admin page and the mirror screen stay responsive while a password is checked or a module is installed, and one stuck browser no longer delays the screen.
- Fixed: the Wi-Fi API now locks out after wrong passwords, like the login.
- Fixed: turning SSH on or off says so when it fails, and a setting saved meanwhile is no longer lost.
- Fixed: a text setting can be "true" or "false", and wrong values for system settings give a clear error.
- Fixed: the "Reconnecting" badge no longer hides while the connection is still down.
- Fixed: after MirrorDash restarts with the screen off, the screen follows its schedule again; a screen on/off command that fails is no longer reported as done.
- Fixed: a system update that didn't start is no longer installed again every night.
- Fixed: sensor readings keep updating after one failed reading, and the Offline badge no longer shows on networks that only block the two test addresses.
- Fixed: the Matter on/off switch no longer flickers back to the old state, and a Wi-Fi country problem no longer reports the time zone change as failed.

## [0.9.0-os1] - 2026-10-23

OS image with MirrorDash 0.9.0.

- Node.js and the Matter node (matter.js), off until Matter is turned on in the admin page (about 90 MB on the card).
- The admin page shows the system's release (e.g. v0.8.2-os1) next to its build date, from the next OS image on.
- uv, which installs MirrorDash and the modules, is the newest 0.13.x, and its bug fixes come with each image.
- Safer: the app can no longer get full control of the system through its own few admin rights. It sets only the pi user's password, and the screen brightness and Wi-Fi without admin rights at all.
- The pi user has no default password any more (it was `raspberry`); turning SSH on sets yours.
- A network cable plugged in during the Wi-Fi setup stops the setup network at once, so the admin page opens without setting up Wi-Fi or restarting.

## [0.9.0] - 2026-10-21

- MirrorDash runs on FastAPI 0.158 and uvicorn 0.54, and from now on gets their bug fixes by itself, without a new MirrorDash version for each.
- Every MirrorDash update also brings the newest bug-fix version of its Python (3.14.x).
- Matter: add the mirror to Apple Home, Google Home, Alexa, SmartThings or Home Assistant and turn its screen on and off from there. Turn it on under *Settings* → *Matter* and scan the code (from the next OS image on).
- Fixed: changing a setting from Home Assistant or the *Power* tab could turn SSH on at the next restart, with the system's default password. SSH now only ever starts after you set your own password for it under *Settings*.
- *Add modules* marks the modules made by MirrorDash as *Official*, and says before installing any other that it gets full access to the mirror. A package on PyPI can no longer take the name of an official module.

## [0.8.1-os1] - 2026-10-21

OS image with MirrorDash 0.8.1.

- Each OS image comes with a list of the versions of all its packages (`mirrordash-os-*.packages.txt` with the release, `/usr/share/mirrordash/packages.txt` on the mirror), to compare builds when something breaks.
- Every OS image gets the newest system packages, certificates and uv when it is built, and a test version of the app the newest Python packages. This also brings the fix for the compromised eMudhra certificates (2026-10-20).

## [0.8.1] - 2026-10-21

- The mirror starts again: in 0.8.0 it stayed on "MirrorDash didn't start" after every start, while MirrorDash was running fine underneath.
- Creating or uploading a backup works on a new or reset mirror. Before, it failed with an error until the mirror had a backups folder, which only older mirrors had.

## [0.8.0-os1] - 2026-10-21

OS image with MirrorDash 0.8.0.

- The mirror can play sound and video for modules (radio, TV, a doorbell camera), with the Pi's own video decoder. It adds about 15 MB to the system.

- After a restart without internet, the mirror's clock starts at the last time it knew instead of the date the system was built, so times and secure connections are right sooner.

- If the storage partition (settings, modules, Wi-Fi) has errors after a power cut, the mirror repairs it at start and keeps the log. Before, it could start as if new and forget everything at the next restart; now, if it can't be repaired, the screen says so.
- The automatic restart of a frozen screen or app (0.7.1) comes with the OS image.
- A system update no longer deletes your modules or your MirrorDash updates when it brings a new Python: MirrorDash's Python now lives with your settings, and a system update doesn't touch it.
- If MirrorDash can't start on a new system version, the mirror goes back to the old system and keeps MirrorDash as it was. Before, it took the older MirrorDash, or Safe mode, and could keep the broken system.
- A system update is kept only if the mirror reaches the internet with it. Before, an update whose Wi-Fi didn't work could be kept because the setup hotspot counted as a connection, and the mirror then couldn't get the update that fixed it.
- A mirror that has put up `MirrorDash-Setup` (for example after a power cut, when the router starts after the mirror) goes back to your Wi-Fi by itself within a few minutes. Before, it kept putting the hotspot back up and stayed there until it was restarted.

## [0.8.0] - 2026-10-21

- Security: the admin page no longer keeps your password in the browser or sends it with every click. You log in once per device (for 30 days), there is **Log Out on This Device**, a new password logs out every other device, and after five wrong passwords the mirror waits a minute. Other web pages can no longer talk to the admin page on your behalf.

- **Reset Mirror** at the bottom of Settings makes the mirror as new, for giving it away or starting over, without reflashing (it needs the next system version).

- Updates install themselves at night (Settings → Updates → *Install updates automatically*, on unless you turn it off), with the same safety as the buttons: an update that doesn't work is undone and isn't tried again by itself.

- A wrong password for an encrypted backup now always says so. Before, about one wrong password in 256 was reported as a broken backup file.

- Security: a backup file or a community module's PyPI page could put script into the admin page (a crafted module name, release notes, or file name). Everything they bring is now shown as text only.

- Installing or updating a module, or MirrorDash, now stops at once with a clear message when the mirror's storage is nearly full. Before, it could fail halfway.

- When a new MirrorDash needs a newer Python, the mirror installs it with the update and first checks that every module works with it. If some don't, nothing changes: the update lists them and you choose whether to install anyway (they're turned off, with their settings kept) or wait.
- In Safe mode, **Reinstall MirrorDash and Modules** now really brings your modules back. Before, it reinstalled MirrorDash without them, and the modules were then gone for good.

## [0.7.1] - 2026-10-19

- A settings file damaged by a power cut no longer resets the mirror to its defaults: it goes back to the last good settings, and keeps the damaged file next to them.
- If the mirror's screen freezes or the app stops answering, the mirror restarts it by itself within about 6 minutes and keeps the log under **Logs → Saved Logs**.

## [0.7.0-os2] - 2026-10-19

OS image with MirrorDash 0.7.0.

- The system part of the SD card is now compressed (squashfs): about 1.3 GB to flash instead of 3.7 GB, and locked from the very first start, so the mirror no longer restarts once by itself when it's new.
- After a kernel crash the mirror restarts after 10 seconds instead of staying black.
- mirrordash.local works right after Wi-Fi setup. Before, the mirror could end up as mirrordash-2.local until the next restart.
- The system underneath MirrorDash can now be updated from **Settings → Check for Updates**, without reflashing the card and keeping everything. The mirror tries the new version first and goes back to the old one by itself if the network or MirrorDash doesn't start with it. This image is the last one you need to flash; it needs an SD card of at least 8 GB.
- A broken system update falls back within about half a minute instead of more than three.
- When an update goes wrong, the mirror keeps the log that explains why under **Logs → Saved Logs**, where it can be downloaded or deleted. Otherwise nothing is saved, so the SD card doesn't wear out.
- A mirror whose Wi-Fi stays connected but stops passing traffic now notices within about 6 minutes, keeps the log under **Logs → Saved Logs** and reconnects by itself. Before, it could stay unreachable until it was restarted.
- When the Wi-Fi chip doesn't start (it happens now and then on a Pi 3), the mirror starts it again by itself within a few minutes, and restarts as a last resort if it has no network at all. Before, it stayed without Wi-Fi until someone restarted it.
- On a Raspberry Pi 3, a system update also renews the Pi's start-up firmware (once the new system has started properly), so it keeps up with the new Linux version. It's written next to the old one and switched in one step, so a power cut can't leave it half-written.
- **Settings → Developer → Developer logging** keeps the whole system log across restarts, for finding out why something went wrong. It turns itself off after 7 days, so the SD card isn't worn by a forgotten switch.

## [0.7.0] - 2026-10-19

### Fixed
- The *Offline* badge on the mirror shows only when the mirror really can't reach the internet. Before, it could show while the mirror was online (and stay away while nothing got through).

## [0.6.3] - 2026-10-19

### Changed
- **Settings → Updates** shows MirrorDash and the system in two parts, each with its installed version and its own install button. When both have an update, the system goes first.
- The messages after a failed update say what happened in plain words: *Update Undone* when the new version didn't start and the old one is back, *Safe Mode* when MirrorDash started without your modules, each with a pointer to the saved log. The button to fix it is now **Reinstall MirrorDash and Modules**.

## [0.6.2] - 2026-10-12

### Added
- A Reload Screen button in the Power tab loads the mirror's page again, for example if it looks wrong.
- Every password field in the admin page has Show/Hide.
- Change the admin password under *Settings → Admin Password*.
- Change the mirror's Wi-Fi under *Settings → Wi-Fi*. If it can't join the new network, it goes back to the old one and says why.
- When its Wi-Fi is gone for good (a new router or password), the mirror starts `MirrorDash-Setup` within a few minutes, without a restart. *Needs the new OS image.*
- A recovery code, shown once when you set the admin password. *Forgot password?* now takes this code instead of a PIN on the mirror's screen, so standing in front of the mirror is no longer enough to take over the admin page. Lost the code too? The user guide shows how to reset the password over SSH or on the SD card.
- While the screen is off, the modules stop fetching data, which saves calls to paid or rate-limited services. When the screen comes back on, a module whose update was due fetches it right away; the others keep to their own schedule.
- Modules can fetch any kind of data through the mirror (RSS, calendars, POST APIs), not only JSON, so all of them get the sleep above and keep showing their last answer when the internet is gone. The calendar and news modules now use it.
- Saving a setting no longer makes every module fetch its data again, and a module on the screen no longer redraws (flickers) when nothing in it changed.
- The dashboard shows the CPU's load next to its temperature, and marks a module on the screen that is set up but no longer installed.

### Changed
- The Modules tab starts with a map of the mirror: its top, middle and bottom, with every module where it sits. Tap one to change it, or switch it on and off right there. Below it, the installed modules are a short list that says where each one is and whether an update is waiting, and *Add modules* shows each community module's screenshot, author, stars and last update before you install it.
- The admin page has one quiet look: white is the only accent, colour only means a status, and the MirrorDash monogram is at the top (also on a phone).
- SSH (now under *Developer*) and the Home Assistant token (*Home Assistant & API*) moved from Hardware to Settings. Hardware is the screen and what's connected to the Pi.

### Fixed
- *Add New* under a module's instances gives the new one its own name (Clock 2) and says it is new, with *Add to Mirror* instead of Save. Before, it could reuse the first instance's name and looked like nothing happened.
- A module that is set up but no longer installed (removed outside the admin page, or from a backup of another mirror) can be removed from the map on the Modules tab. Before, only editing `config.json` cleared it.
- *Rebuild environment* no longer loses modules installed from GitHub or added more than once: it reinstalls exactly what was installed, and if one of them can't be installed it keeps the old environment.
- The SDK for writing modules (`mirrordash-sdk`) no longer shows up as a module to install.
- After a power cut, the mirror no longer stays on `MirrorDash-Setup` when it started before the router: it tries your Wi-Fi again every few minutes. *Needs the new OS image.*
- *Forgot password?* no longer lets anyone on the home network lock you out: it used to remove the password before a new one was chosen.
- Turning on SSH says so: the button shows that it's working, then "SSH is on" with how to log in, or why the password was refused. Before, the click often did nothing visible, and a refused password still left SSH marked as on.
- The Modules tab no longer becomes wider than a phone's screen.
- A module setting with a quote (") in it no longer breaks the module's settings form.
- After an update the admin page loads its new look and script right away, instead of what the phone had cached.
- After a MirrorDash update the screen shows the new version right away; before, the mirror's page kept the old version's look and behaviour until the mirror was restarted.
- Updating MirrorDash right after a new version is published installs that version. Before, it could reinstall the current one, say it had updated and restart for nothing; now it says the new version isn't available yet and stays as it is.
- Discover New Modules lists every module on GitHub again (weather, calendar, Home Assistant, …), not just the clock. It now asks GitHub once per refresh instead of once per module, which used up GitHub's hourly limit for the home network. When GitHub refuses anyway, the page says the refresh failed instead of quietly showing only the clock.
- The clock (or any module) no longer keeps pulsing when the mirror's screen switches to the mirror page right after setup.

## [0.6.0-os1] - 2026-10-08

OS image with MirrorDash 0.6.0.

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
