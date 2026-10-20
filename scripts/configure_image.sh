#!/bin/bash
# MirrorDash Automatic Resumable Appliance Setup Script
# Configure a fresh Debian Trixie (Raspberry Pi OS) installation into a production MirrorDash kiosk.
#
# This script is designed for production IoT appliances. It installs the application
# from PyPI — no source code is cloned onto the device (AGENTS.md Rule 29).

set -e

# Prevent locale warnings from apt/dpkg during package installation
export LC_ALL=C.UTF-8
export LANG=C.UTF-8
export LANGUAGE=C.UTF-8
export DEBIAN_FRONTEND=noninteractive

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash configure_image.sh)"
  exit 1
fi

echo "=== Setting up Container Build Policy ==="
# Officiell Debian-metod för att hindra tjänster (som Plymouth) från att starta under byggfasen
cat << 'EOF' > /usr/sbin/policy-rc.d
#!/bin/sh
exit 101
EOF
chmod +x /usr/sbin/policy-rc.d

# Ensure target disk has enough space (at least 8GB to fit bootfs + rootfs + storage)
# Bypass the physical disk size check if we are building the image in the cloud
if [ -z "${BUILDING_IMAGE:-}" ]; then
  ROOT_DISK="/dev/$(lsblk -ndo PKNAME "$(findmnt -nvo SOURCE /)")"

  if [ -b "$ROOT_DISK" ]; then
    disk_size_bytes=$(blockdev --getsize64 "$ROOT_DISK")
    if [ "$disk_size_bytes" -lt $((7 * 1024 * 1024 * 1024 + 500 * 1024 * 1024)) ]; then
      echo "Error: MirrorDash requires a system drive of at least 8GB (detected $((disk_size_bytes / 1024 / 1024 / 1024))GB)." >&2
      exit 1
    fi
  fi
fi

PI_USER="pi"
PI_HOME="/home/$PI_USER"
GITHUB_RAW="https://raw.githubusercontent.com/Menturan/MirrorDash/master"

# Every file the setup installs as it is (scripts, systemd units, sudo rules) lives in the repo at
# the same path under scripts/image/rootfs/, where it can be read, linted and tested on its own.
# put MODE /path installs it: from the repo copy the image build puts in /opt/MirrorDash, or from
# GitHub when this runs on a Pi set up by hand. Files with build-time values stay below as heredocs.
put() {
  local mode="$1" target="$2" source="scripts/image/rootfs$2"
  mkdir -p "$(dirname "$target")"
  if [ -f "/opt/MirrorDash/$source" ]; then
    install -m "$mode" "/opt/MirrorDash/$source" "$target"
  else
    curl -sSLf "$GITHUB_RAW/$source" -o "$target"
    chmod "$mode" "$target"
  fi
}
STATE_FILE="/var/lib/mirrordash-setup-state"
# Pinned so a given tag always produces the same image. Bump deliberately.
UV_VERSION="0.12.23"
CLOCK_REF="v1.1.1"  # a tag in Menturan/mirrordash-clock (must be its newest release, or mirrors offer a "newer" old one)

# Reset state if requested
if [ "$1" = "--fresh" ] || [ "$1" = "--reset" ]; then
  echo "Resetting installation state..."
  rm -f "$STATE_FILE"
  echo "Cleaning up existing virtual environments for fresh setup..."
  rm -rf /storage/mirrordash/venv_a /storage/mirrordash/venv_b /storage/mirrordash/venv_old /storage/mirrordash/venv_failed "$PI_HOME/mirrordash/base_venv"
fi

# Helper function to check if a step is already done
is_step_completed() {
  local step="$1"
  if [ -f "$STATE_FILE" ] && grep -Fxq "$step" "$STATE_FILE"; then
    return 0
  fi
  return 1
}

# Helper function to mark a step as done
mark_step_completed() {
  local step="$1"
  mkdir -p "$(dirname "$STATE_FILE")"
  echo "$step" >> "$STATE_FILE"
}

# Core runner function for steps
run_step() {
  local step_num="$1"
  local step_name="$2"
  local step_desc="$3"
  shift 3

  if is_step_completed "$step_name"; then
    echo "=== Skipping Step $step_num: $step_desc (Already Completed) ==="
    # Run critical side-effects for skipped steps
    if [ "$step_name" = "expanding_partition" ]; then
      if ! mountpoint -q /storage; then
        echo "Mounting /storage partition..."
        mount /storage
      fi
    fi
    return 0
  fi

  echo "=== $step_num. $step_desc ==="
  local start_time=$SECONDS
  "$@"
  local end_time=$SECONDS
  local duration=$((end_time - start_time))
  local minutes=$((duration / 60))
  local seconds=$((duration % 60))
  mark_step_completed "$step_name"
  echo ">>> Step $step_num completed in ${minutes}m ${seconds}s."
  echo ""
}

# --- Step Functions ---

step_expanding_partition() {
  echo "Configuring persistent storage mount in /etc/fstab..."
  mkdir -p /storage
  if ! grep -q "LABEL=mirrordash-data" /etc/fstab; then
    cat << 'EOF' >> /etc/fstab

# --- MirrorDash Persistent Storage ---
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,nofail,x-systemd.device-timeout=15s  0  2
EOF
  fi

  # (Network profiles survive OverlayFS because /storage is not locked)
  # NetworkManager refuses to follow symlinks for security reasons, so we bind-mount it in the hydrate script instead.
}

step_installing_packages() {
  echo "Diverting update-initramfs to prevent cloud-build kernel panics..."
  dpkg-divert --local --rename --add /usr/sbin/update-initramfs
  ln -sf /bin/true /usr/sbin/update-initramfs

  apt-get update
  apt-get install -y --no-install-recommends \
      labwc \
      seatd \
      dbus-user-session \
      fonts-liberation \
      cog \
      wlr-randr \
      avahi-daemon \
      plymouth \
      pix-plym-splash \
      systemd-timesyncd \
      initramfs-tools \
      network-manager \
      dnsmasq-base \
      nginx \
      parted \
      zip \
      overlayroot \
      ddcutil \
      git

  # HDMI screens take brightness over DDC/CI (I2C on the HDMI cable); ddcutil needs /dev/i2c-*
  echo "i2c-dev" > /etc/modules-load.d/mirrordash-i2c.conf
}

# Sound and hardware video for modules (ARCHITECTURE #32): GStreamer's base plugins, and only the
# plugins of good and bad that play radio, TV (HLS, the Pi's H.264 decoder) and camera streams. The
# whole of good and bad is about 270 MB (bad alone pulls in ONNX, oneDNN and GTK); this is about 12 MB.
# autodetect (autoaudiosink): without it WebKit's web process crashes on any <audio> or <video>.
# python3-gi and the two GStreamer typelibs (about 3 MB): a module's video player runs on the system's
# Python with them (ARCHITECTURE #34); the app's own Python has no GStreamer bindings.
MEDIA_PLUGINS_GOOD="video4linux2 adaptivedemux2 isomp4 audioparsers mpg123 soup icydemux autodetect"
MEDIA_PLUGINS_BAD="videoparsersbad waylandsink faad"

step_media() {
  # The libraries the copied plugins need (objdump -p, NEEDED); most come with Cog anyway
  apt-get install -y --no-install-recommends \
      gstreamer1.0-plugins-base gstreamer1.0-alsa gstreamer1.0-gl gstreamer1.0-tools \
      libgstreamer-plugins-bad1.0-0 libgudev-1.0-0 libv4l-0t64 libxml2 libsoup-3.0-0 libmpg123-0t64 libfaad2 \
      python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0

  # ponytail: copied out of the .debs, so dpkg doesn't know these files. A mirror never runs apt and
  # each image builds from Debian's current packages, so nothing is missed; a new plugin is a new name above.
  local tmp plugin_dir=/usr/lib/aarch64-linux-gnu/gstreamer-1.0 p
  tmp=$(mktemp -d)
  (cd "$tmp" && apt-get download gstreamer1.0-plugins-good gstreamer1.0-plugins-bad)
  for deb in "$tmp"/*.deb; do dpkg-deb -x "$deb" "$tmp/x"; done
  for p in $MEDIA_PLUGINS_GOOD $MEDIA_PLUGINS_BAD; do
    install -m 0644 "$tmp/x$plugin_dir/libgst$p.so" "$plugin_dir/"
  done
  rm -rf "$tmp"

  # GStreamer skips a plugin whose library is missing; stop the build here rather than on a mirror.
  # video4linux2 is checked as a plugin: its decoder element only exists where /dev/video10 does.
  for p in $MEDIA_PLUGINS_GOOD $MEDIA_PLUGINS_BAD alsa playback; do
    GST_REGISTRY=/tmp/gst-check.bin gst-inspect-1.0 "$p" > /dev/null || { echo "GStreamer plugin $p doesn't load"; exit 1; }
  done
  rm -f /tmp/gst-check.bin
  python3 -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' \
      || { echo "GStreamer's Python bindings don't load"; exit 1; }
}

step_setting_hostname() {
  echo "mirrordash" > /etc/hostname
  sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts

  echo "Unblocking Wi-Fi radio permanently..."
  # US until the mirror knows where it is: the time zone from Wi-Fi setup sets the real country
  raspi-config nonint do_wifi_country US 2>/dev/null || true
  rfkill unblock wifi 2>/dev/null || true

  # brcmfmac power save on Pi 3/4 stalls traffic for tens of seconds while staying associated
  echo "Disabling Wi-Fi power save..."
  mkdir -p /etc/NetworkManager/conf.d
  put 0644 /etc/NetworkManager/conf.d/mirrordash-wifi-powersave.conf

  echo "Enabling avahi-daemon service..."
  systemctl --root=/ enable avahi-daemon.service
  # Going from the setup hotspot to home Wi-Fi, avahi sees its own probe as a name conflict and
  # stays mirrordash-2.local until restarted, so mirrordash.local/admin breaks right after setup.
  mkdir -p /etc/NetworkManager/dispatcher.d
  put 0755 /etc/NetworkManager/dispatcher.d/50-mirrordash-avahi
}

step_configuring_nginx() {
  rm -f /etc/nginx/sites-enabled/default
  put 0644 /etc/nginx/sites-available/mirrordash
  ln -sf /etc/nginx/sites-available/mirrordash /etc/nginx/sites-enabled/mirrordash
  nginx -t

  echo "Enabling nginx service..."
  systemctl --root=/ enable nginx.service
}

step_configuring_console_login() {
  echo "Setting system to graphical target..."
  ln -fs /lib/systemd/system/graphical.target /etc/systemd/system/default.target

  echo "Setting up user 'pi' (password 'raspberry')..."
  # Done here, not by Pi OS's userconf.txt on the first boot: the image's root is read-only from that
  # boot on (squashfs), so a user set up then would be gone at the next one.
  usermod -s /bin/bash -p "$(echo 'raspberry' | openssl passwd -6 -stdin)" pi
  systemctl --root=/ disable userconfig.service
  # The rest of what Pi OS does once the user is set up (cancel-rename): its "SSH may not work" banner
  rm -f /etc/ssh/sshd_config.d/rename_user.conf
}

step_setting_up_wayland() {
  echo "Configuring labwc kiosk systemd service..."
  put 0644 /etc/systemd/system/labwc-kiosk.service
  systemctl --root=/ enable labwc-kiosk.service

  echo "Configuring seatd permissions for unprivileged Wayland access..."
  # Ensure seatd.service is explicitly enabled using offline mode to avoid container build D-Bus/PID 1 errors.
  systemctl --root=/ enable seatd.service

  # Configure seatd to use the 'video' group instead of 'seat' because the 'pi' user is
  # created on first boot and is not added to the 'seat' group by default, but is always in 'video'.
  mkdir -p /etc/systemd/system/seatd.service.d
  put 0644 /etc/systemd/system/seatd.service.d/group.conf

  # Ta bort högerklick/terminal-åtkomst på skärmen
  mkdir -p "$PI_HOME/.config/labwc"
  cat << 'EOF' > "$PI_HOME/.config/labwc/rc.xml"
<?xml version="1.0"?><labwc_config><mouse><context name="Root"><mousebind button="Right" action="Press"><action name="None" /></mousebind></context></mouse></labwc_config>
EOF

  # Generate a 1-pixel transparent X11 cursor theme to permanently hide the mouse pointer in Wayland
  # The XCURSOR_THEME environment variable (set in labwc/environment below) tells the compositor
  # to use this theme instead of any system default, hiding the cursor at the compositor level.
  mkdir -p "$PI_HOME/.icons/empty/cursors"
  echo "WGN1chAAAAAAAAEAAQAAAAIA/f8gAAAAHAAAACQAAAACAP3/IAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA=" | base64 -d > "$PI_HOME/.icons/empty/cursors/left_ptr"
  # Symlink all other common cursor names to left_ptr to prevent fallbacks to default system cursor icons
  for c in default pointer hand hand1 hand2 wait watch text xterm cross crosshair help question_arrow; do
    ln -sf left_ptr "$PI_HOME/.icons/empty/cursors/$c"
  done
  cat << 'EOF' > "$PI_HOME/.icons/empty/index.theme"
[Icon Theme]
Name=empty
EOF
  echo "XCURSOR_THEME=empty" > "$PI_HOME/.config/labwc/environment"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config" "$PI_HOME/.icons"
  # Also the system's default theme, so anything that falls back to "default" gets no pointer either
  cp -r "$PI_HOME/.icons/empty" /usr/share/icons/empty
  mkdir -p /usr/share/icons/default
  printf '[Icon Theme]\nInherits=empty\n' > /usr/share/icons/default/index.theme

  echo "Configuring cog kiosk systemd service..."
  put 0644 /etc/systemd/system/cog-kiosk.service

  # labwc has no readiness notification, so start cog when its Wayland socket appears
  # (otherwise cog races labwc and aborts with "preferred module 'wl' not supported").
  # PathExists re-triggers after labwc restarts, since BindsTo stops cog with it.
  put 0644 /etc/systemd/system/cog-kiosk.path
  systemctl --root=/ disable cog-kiosk.service 2>/dev/null || true
  systemctl --root=/ enable cog-kiosk.path

  echo "Masking default getty and autologin on tty1 to prevent terminal login preemption..."
  systemctl --root=/ mask getty@tty1.service autologin@tty1.service
}

step_installing_app() {
  # Install uv globally (standalone binary) securely via release artifact
  echo "Downloading uv binary securely to /usr/local/bin..."
  curl -sSLf "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz" | tar -xz -C /usr/local/bin --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
  chmod +x /usr/local/bin/uv

  # Create app directory
  sudo -u "$PI_USER" HOME="$PI_HOME" mkdir -p "$PI_HOME/mirrordash"

  # Setup symlink structures (A/B venv layout & persistent data)
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT /storage/mirrordash/venv "$PI_HOME/mirrordash/.venv"
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT /storage/mirrordash/data "$PI_HOME/.mirrordash"

  # Create base_venv (Golden Copy), then install the app into it
  sudo -u "$PI_USER" HOME="$PI_HOME" CLOCK_REF="$CLOCK_REF" PATH="$PI_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" bash -e << 'EOF'
  cd "$HOME/mirrordash"

  echo 'Creating golden backup virtual environment base_venv...'
  rm -rf "$HOME/mirrordash/base_venv"
  uv venv --allow-existing --python 3.14 "$HOME/mirrordash/base_venv"

  # Image builds install the checked-out source so the image matches its tag;
  # a standalone run (no repo copy) falls back to the latest PyPI release.
  CORE_PKG=mirrordash
  CONSTRAINTS=()
  if [ -f /opt/MirrorDash/pyproject.toml ]; then
    echo 'Building MirrorDash wheel from the repository...'
    rm -rf "$HOME/mirrordash-dist"
    uv build --wheel --out-dir "$HOME/mirrordash-dist" /opt/MirrorDash
    CORE_PKG=$(ls "$HOME"/mirrordash-dist/mirrordash-*.whl)
    # The exact dependency versions from uv.lock (the ones CI tested), not whatever is newest today
    uv export --project /opt/MirrorDash --frozen --no-dev --no-emit-project --no-hashes \
      -o "$HOME/mirrordash-dist/constraints.txt"
    CONSTRAINTS=(-c "$HOME/mirrordash-dist/constraints.txt")
  fi

  echo "Installing $CORE_PKG and clock module ($CLOCK_REF) into golden venv..."
  uv pip install --python "$HOME/mirrordash/base_venv" "${CONSTRAINTS[@]}" "$CORE_PKG" "git+https://github.com/Menturan/mirrordash-clock.git@${CLOCK_REF}"
  rm -rf "$HOME/mirrordash-dist"
EOF

  # Download or copy launch.sh and loading.html
  if [ -f "/opt/MirrorDash/scripts/launch.sh" ]; then
    echo "Copying launch.sh and loading.html from local repository..."
    cp "/opt/MirrorDash/scripts/launch.sh" "$PI_HOME/mirrordash/launch.sh"
    cp "/opt/MirrorDash/mirrordash_core/static/loading.html" "$PI_HOME/mirrordash/loading.html"
  else
    echo "Downloading launch.sh and loading.html from GitHub..."
    curl -sSLf "$GITHUB_RAW/scripts/launch.sh" -o "$PI_HOME/mirrordash/launch.sh"
    curl -sSLf "$GITHUB_RAW/mirrordash_core/static/loading.html" -o "$PI_HOME/mirrordash/loading.html"
  fi
  chmod +x "$PI_HOME/mirrordash/launch.sh"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/mirrordash"
}

step_passwordless_sudo() {
  put 0440 /etc/sudoers.d/mirrordash
  # Settings → Reset mirror (the sudo rule above allows exactly this, with --yes)
  put 0755 /usr/local/bin/mirrordash-factory-reset
  visudo -cf /etc/sudoers.d/mirrordash
}

step_gpio_helper() {
  # Root-owned helper the admin page calls (via sudo) to set which sensors/inputs use which pins.
  # It only writes a managed block of device-tree overlays to config.txt; the firmware applies
  # them at the next boot (overlays applied at boot can't be swapped safely at runtime).
  put 0755 /usr/local/bin/mirrordash-gpio-overlays
}

step_watchdog_boot_optimization() {
  # The hardware watchdog is already on: Raspberry Pi OS ships RuntimeWatchdogSec=1m as a drop-in
  # (/usr/lib/systemd/system.conf.d/40-rpi-enable-watchdog.conf).

  # No apt in the background: the app updates itself, and a new OS comes as a RAUC bundle (step_ab_updates).
  # These timers only downloaded package lists, writing to the system disk for nothing.
  systemctl --root=/ mask apt-daily.timer apt-daily-upgrade.timer

  # Suppress splash, boot delay, Bluetooth, allocate gpu memory in config.txt
  if ! grep -q "disable_splash=1" /boot/firmware/config.txt; then
    cat << 'EOF' >> /boot/firmware/config.txt

# --- MirrorDash Hardware Hardening ---
[all]
disable_splash=1
boot_delay=0
dtoverlay=disable-bt
EOF
  fi

  # Silence kernel logs in cmdline.txt: replace console=tty1 with console=tty3, remove auto-resize parameters, and append quiet/splash options
  if [ -f /boot/firmware/cmdline.txt ]; then
    echo "Parsing and optimizing /boot/firmware/cmdline.txt..."
    read -r -a current_opts < /boot/firmware/cmdline.txt

    declare -a new_opts
    for opt in "${current_opts[@]}"; do
      # "resize" makes the initramfs (resize_early) grow the root over the whole card, leaving no
      # room for the data partition. Also drop legacy console settings.
      if [[ "$opt" == "resize" || "$opt" == init=*init_resize.sh || "$opt" == console=* ]]; then
        continue
      fi
      new_opts+=("$opt")
    done

    declare -a target_opts=(
      "console=tty3"
      "loglevel=0"
      "quiet"
      "splash"
      "systemd.show_status=false"
      "vt.global_cursor_default=0"
      "plymouth.ignore-serial-consoles"
      "logo.nologo"
      # A kernel panic, or a root the initramfs can't find, restarts the mirror instead of halting it
      "panic=10"
    )
    # Image builds: the root is a squashfs (build_image.sh) under a RAM overlay, from the first boot on.
    # recurse=0 overlays only /: by default overlayroot also puts every fstab mount (/storage!) on the
    # RAM overlay, losing settings and Wi-Fi at reboot. The image's machine-id is "uninitialized", so
    # systemd would treat every boot as the first: enable every unit Debian's presets name (SSH,
    # systemd-networkd, Cog without its path unit...) and rerun the first-boot units (new SSH keys).
    # Nothing in the image needs a first boot, so there is none.
    # rootdelay=20: how long the initramfs waits for the root partition (initramfs-tools' default is
    # 180 s). The SD card shows up within a couple of seconds; if the root still isn't there, a new OS
    # version is broken, and panic=10 then restarts the mirror into the previous one (A/B, tryboot)
    # after about half a minute instead of more than three.
    if [ -n "${BUILDING_IMAGE:-}" ]; then
      target_opts+=("rootfstype=squashfs" "overlayroot=tmpfs:recurse=0" "systemd.condition_first_boot=false" "rootdelay=20")
    fi

    for target in "${target_opts[@]}"; do
      prefix="${target%%=*}"
      found=0
      for i in "${!new_opts[@]}"; do
        if [[ "${new_opts[i]}" == "$prefix"* ]]; then
          new_opts[i]="$target"
          found=1
          break
        fi
      done
      if [ "$found" -eq 0 ]; then
        new_opts+=("$target")
      fi
    done

    echo "${new_opts[*]}" > /boot/firmware/cmdline.txt
  fi
}

step_plymouth_splash() {
  # Our own small theme instead of a patched copy of Raspberry Pi's "pix" theme: pix never draws
  # its image in shutdown mode, and it scales a large splash down (blurry) on smaller screens.
  # The script is pix.script with as few changes as possible (same calls Raspberry Pi ships).
  # Plymouth is never required for booting: a broken theme only means a black screen meanwhile.
  local THEME=/usr/share/plymouth/themes/mirrordash
  mkdir -p "$THEME"

  cat << 'EOF' > "$THEME/mirrordash.plymouth"
[Plymouth Theme]
Name=MirrorDash
Description=MirrorDash boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/mirrordash
ScriptFile=/usr/share/plymouth/themes/mirrordash/mirrordash.script
EOF

  # mirrordash.script = pix.script with: black background, one image per mode (boot, reboot,
  # shutdown) drawn in every mode, no status text, and the image slowly breathing (opacity only)
  # so a long boot never looks frozen. No comments inside, like pix.script.
  cat << 'EOF' > "$THEME/mirrordash.script"
Window.SetBackgroundTopColor(0, 0, 0);
Window.SetBackgroundBottomColor(0, 0, 0);

screen_width = Window.GetWidth();
screen_height = Window.GetHeight();

image_file = "splash.png";
if (Plymouth.GetMode() == "shutdown")
{
	image_file = "shutdown.png";
}
if (Plymouth.GetMode() == "reboot")
{
	image_file = "restart.png";
}
theme_image = Image(image_file);
image_width = theme_image.GetWidth();
image_height = theme_image.GetHeight();

scale_x = image_width / screen_width;
scale_y = image_height / screen_height;

if (scale_x > 1 || scale_y > 1)
{
	if (scale_x > scale_y)
	{
		resized_image = theme_image.Scale (screen_width, image_height / scale_x);
		image_x = 0;
		image_y = (screen_height - ((image_height  * screen_width) / image_width)) / 2;
	}
	else
	{
		resized_image = theme_image.Scale (image_width / scale_y, screen_height);
		image_x = (screen_width - ((image_width  * screen_height) / image_height)) / 2;
		image_y = 0;
	}
}
else
{
	resized_image = theme_image.Scale (image_width, image_height);
	image_x = (screen_width - image_width) / 2;
	image_y = (screen_height - image_height) / 2;
}

sprite = Sprite (resized_image);
sprite.SetPosition (image_x, image_y, -100);

breath.angle = 0;
fun refresh_callback ()
{
	breath.angle += ((2 * 3.14) / 50) * 0.4;
	opacity = (Math.Cos(breath.angle) + 1) / 2;
	opacity *= 1 - 0.5;
	opacity += 0.5;
	sprite.SetOpacity (opacity);
}
Plymouth.SetRefreshFunction (refresh_callback);
EOF

  # Boot images (rendered by scripts/render_boot_images.py)
  local img
  for img in splash.png restart.png shutdown.png; do
    if [ -f "/opt/MirrorDash/mirrordash_core/static/$img" ]; then
      cp "/opt/MirrorDash/mirrordash_core/static/$img" "$THEME/$img"
    else
      curl -sSLf "$GITHUB_RAW/mirrordash_core/static/$img" -o "/tmp/$img"
      mv "/tmp/$img" "$THEME/$img"
    fi
  done

  # Register our theme (initrd will be fully rebuilt later in step_system_cleanup)
  plymouth-set-default-theme mirrordash
}

step_time_wait_sync() {
  systemctl --root=/ enable systemd-time-wait-sync.service
}

step_wifi_captive_portal() {
  put 0755 /usr/local/bin/mirrordash-wifi-check.sh

  # Captive portal DNS: NetworkManager's dnsmasq reads this directory only for shared (hotspot)
  # connections, never on the home network. Every name answers with the mirror, so the phone's
  # connectivity check (captive.apple.com, connectivitycheck.gstatic.com, ...) reaches the app,
  # gets redirected, and the phone shows "Sign in to network" by itself.
  mkdir -p /etc/NetworkManager/dnsmasq-shared.d
  echo 'address=/#/10.42.0.1' > /etc/NetworkManager/dnsmasq-shared.d/mirrordash-captive.conf

  put 0644 /etc/systemd/system/mirrordash-wifi-fallback.service
  systemctl --root=/ enable mirrordash-wifi-fallback.service

  # Every 3 minutes: the hotspot tries the saved Wi-Fi again, and a Wi-Fi gone for good starts it
  put 0644 /etc/systemd/system/mirrordash-wifi-watch.service
  put 0644 /etc/systemd/system/mirrordash-wifi-watch.timer
  systemctl --root=/ enable mirrordash-wifi-watch.timer
}

step_systemd_service() {
  put 0644 /etc/systemd/system/mirrordash.service
  systemctl --root=/ enable mirrordash.service

  # Restart=always only helps when a process dies. This catches the two that hang alive: an app that
  # no longer answers, and a kiosk whose page stopped (a crashed or frozen WebKit keeps its window).
  # ponytail: found after ~6 minutes, not seconds; the upgrade is WatchdogSec= + sd_notify from the app.
  mkdir -p /usr/local/lib/mirrordash
  put 0755 /usr/local/lib/mirrordash/app-watch
  put 0644 /etc/systemd/system/mirrordash-app-watch.service
  put 0644 /etc/systemd/system/mirrordash-app-watch.timer
  systemctl --root=/ enable mirrordash-app-watch.timer

  # The last good time, kept on /storage: no clock battery, and the root forgets (clock script)
  put 0755 /usr/local/lib/mirrordash/clock
  put 0644 /etc/systemd/system/mirrordash-clock.service
  put 0644 /etc/systemd/system/mirrordash-clock-save.service
  put 0644 /etc/systemd/system/mirrordash-clock-save.timer
  systemctl --root=/ enable mirrordash-clock.service mirrordash-clock-save.timer

  # Nightly updates, when Settings → Updates has them on (the app decides what to install)
  put 0644 /etc/systemd/system/mirrordash-auto-update.service
  put 0644 /etc/systemd/system/mirrordash-auto-update.timer
  systemctl --root=/ enable mirrordash-auto-update.timer
}

step_repart_service() {
  echo "Creating MBR partition expander script..."
  put 0755 /usr/local/bin/mirrordash-repart.sh

  echo "Configuring mirrordash-repart.service..."
  put 0644 /etc/systemd/system/mirrordash-repart.service

  systemctl --root=/ enable mirrordash-repart.service
}

step_storage_hydration() {
  put 0755 /usr/local/bin/mirrordash-hydrate.sh

  # The system log (journal) lives in RAM only, so nothing is written to the SD card all day and it
  # doesn't wear out. The price: after a restart the log is gone. So when an update fails, this saves
  # a copy of the relevant part to /storage/mirrordash/logs/ right then (admin page → Logs). Called by
  # mirrordash-save-trial-log.service (a failed OS trial boot), the app (a failed OS install) and
  # launch.sh (an app update that was rolled back).
  mkdir -p /usr/local/lib/mirrordash
  put 0755 /usr/local/lib/mirrordash/save-log

  put 0644 /etc/ssh/sshd_config.d/mirrordash-hostkeys.conf
  mkdir -p /etc/systemd/system/ssh.service.d
  put 0644 /etc/systemd/system/ssh.service.d/mirrordash.conf

  put 0644 /etc/systemd/system/mirrordash-storage-init.service
  systemctl --root=/ enable mirrordash-storage-init.service
}

step_dev_logging() {
  # Developer logging: while it is on, the whole system log is kept on /storage across restarts, to
  # find out why something went wrong (a Wi-Fi that drops, an update that fails). It's off by default:
  # Pi OS keeps the log in RAM only (Storage=volatile), so nothing is written to the SD card all day.
  # Turned on from the admin page (Settings → Developer), it switches itself off after 7 days, since a
  # forgotten switch would keep writing to the card for years. The mechanism is journald's own:
  #   * a drop-in in /run (gone at every restart, so this script puts it back while it's on) sets
  #     Storage=persistent; its name sorts after Pi OS's own 40-... file, so it wins;
  #   * /var/log/journal, where a persistent journal lives, is bind-mounted from /storage.
  # Each start has its own machine-id here (the image's is "uninitialized"), and journald keeps one
  # folder per machine-id and only trims its own, so this script keeps the total under its cap by
  # deleting the oldest starts' folders. Reading them all back needs `journalctl -m` (merge).
  put 0755 /usr/local/bin/mirrordash-dev-logging

  # At every boot once /storage is there, and every hour (that's when the 7 days run out)
  put 0644 /etc/systemd/system/mirrordash-dev-logging.service
  put 0644 /etc/systemd/system/mirrordash-dev-logging.timer
  systemctl --root=/ enable mirrordash-dev-logging.timer

  # The admin page turns it on and off (features/settings/dev_logging.py): these exact commands only
  put 0440 /etc/sudoers.d/mirrordash-dev-logging
  visudo -cf /etc/sudoers.d/mirrordash-dev-logging
}

step_ab_updates() {
  # A/B OS updates: a new OS version (kernel, Pi OS, this setup) is installed while the mirror runs,
  # into the half of the card that isn't in use, and kept only if the mirror works with it.
  #
  # The card (made by build_image.sh; the first boot adds p6 and p7, see mirrordash-repart.sh):
  #   p1  autoboot  bootcode.bin, start.elf, fixup.dat + autoboot.txt: which boot partition to use
  #   p2  boot A    kernel, initramfs, config.txt, cmdline.txt (root=...-05)    } slot A
  #   p5  root A    the squashfs root                                           }
  #   p3  boot B    the same for slot B (root=...-06)                           } slot B
  #   p6  root B                                                                }
  #   p7  /storage  settings, Wi-Fi, the app: shared by both slots
  #
  # How an update goes (the parts are below):
  #   1. RAUC (the update tool, rauc.io) checks the bundle's signature against /etc/rauc/ca.cert.pem and
  #      writes its boot and root images into the slot that isn't running.
  #   2. RAUC then asks rauc-bootloader to make that slot boot ("set-primary"). rauc-bootloader points
  #      the new boot partition at its own root, copies the Wi-Fi country and the GPIO settings over
  #      from the running one, and sets the firmware's one-time "tryboot" flag. If any of that fails,
  #      RAUC reports the install as failed and the mirror keeps booting the old slot.
  #   3. At the next restart the firmware boots the [tryboot] partition in autoboot.txt (the new slot)
  #      once; any restart after that boots the old slot again, unless step 4 made the new one permanent.
  #   4. On that trial boot, mirrordash-mark-good waits for the network and the app. When both are
  #      fine it makes the new slot permanent (swaps the partitions in autoboot.txt). If not, it
  #      restarts the mirror, which then comes back on the old slot. A kernel that doesn't start or a
  #      missing root restart it too (panic=10, rootdelay=20, the hardware watchdog).
  #
  # ponytail: only image builds; a Pi set up by hand has one root and keeps updating through new images.
  if [ -z "${BUILDING_IMAGE:-}" ]; then
    echo "Not an image build — no A/B OS updates."
    return 0
  fi

  # rauc is the command; rauc-service the background service it talks to (over D-Bus) to install and
  # to mark a slot good. Neither marks a slot good by itself: only mirrordash-mark-good does that.
  apt-get install -y --no-install-recommends rauc rauc-service

  # Bundles are signed by the build (build_image.sh, key in the RAUC_KEY secret); RAUC installs only
  # what this certificate's key signed. The certificate is public; the key is not in the repository.
  mkdir -p /etc/rauc
  cp /opt/MirrorDash/scripts/rauc/cert.pem /etc/rauc/ca.cert.pem

  # Which partitions make up each slot. RAUC installs both images of a slot together: a boot slot
  # names its root slot as "parent". bootname is the name the bootloader backend uses (A or B).
  # The devices are fixed: MirrorDash boots from the SD card, which is always mmcblk0.
  put 0644 /etc/rauc/system.conf

  mkdir -p /usr/local/lib/mirrordash
  put 0755 /usr/local/lib/mirrordash/rauc-bootloader

  # p1 holds the Pi 3's firmware (bootcode.bin, start.elf, fixup.dat) and autoboot.txt. On a Pi 3 it
  # is p1's start.elf that boots the chosen slot, so the firmware files in the slots are never used
  # there: without this, a Pi 3 would keep the firmware it was flashed with while every system update
  # brings a newer kernel (Pi OS ships the two as a pair). update-p1 renews p1 from the slot that is
  # running, once a trial boot has proven it (mirrordash-mark-good, below), and only when the files
  # differ. It never overwrites the p1 in use: the image left 32 MiB empty after p1 (build_image.sh),
  # so the new p1 is built there and then the MBR's p1 entry is moved over in one 512-byte write.
  # Not RAUC's "boot-mbr-switch" slot type: it writes p1 from the bundle, autoboot.txt included, and
  # so forgets which slot is the good one.
  put 0755 /usr/local/lib/mirrordash/update-p1

  # Step 4 above: keep a new OS version only if the mirror works with it. On a normal boot, and on a
  # trial boot started by hand, the ExecCondition is false and nothing runs. On the trial boot of the
  # slot RAUC activated last (after installing it), each ExecStart must succeed in turn:
  #   curl:      github.com answers within 5 minutes: the internet, not just a connection. nm-online
  #              counts the setup hotspot as online, so an OS whose Wi-Fi can't join the home network
  #              (the hotspot comes up instead) was kept, and the mirror could never fetch the update
  #              that fixes it. github.com is where the next update comes from (no API rate limit).
  #   curl:      the app answers /health (curl retries itself every 5 s, up to 5 minutes)
  #   rauc:      mark this slot good, which makes it the one that boots (rauc-bootloader set-state)
  #   update-p1: renew p1's firmware if it changed (never undoes the update when it fails)
  # If one fails, OnFailure starts mirrordash-save-trial-log: it saves this boot's log (the RAM log
  # is gone after a restart) and restarts the mirror. The tryboot flag is gone by then, so it boots
  # the old slot. The app's own admin page then says the update didn't start (features/updates).
  put 0644 /etc/systemd/system/mirrordash-mark-good.service
  systemctl --root=/ enable mirrordash-mark-good.service

  # The leading "-" lets the restart happen even if saving the log fails, and FailureAction restarts
  # the mirror if this unit itself fails some other way. Either way it ends up on the old slot.
  put 0644 /etc/systemd/system/mirrordash-save-trial-log.service

  # /boot/firmware is the boot partition the firmware started from: p2 (slot A) or p3 (slot B).
  # fstab can't say which, since both slots share this root image, so a systemd generator (a script
  # systemd runs very early at every boot) writes the mount from what the firmware reports.
  # The app writes the Wi-Fi country and the GPIO settings there, for the slot that is running.
  mkdir -p /etc/systemd/system-generators
  put 0755 /etc/systemd/system-generators/mirrordash-bootslot

  # fstab: p1 (autoboot.txt) goes to /boot/autoboot instead of /boot/firmware, which the generator
  # above mounts. The root line goes: the root is whichever of p5/p6 the kernel got in root=, and
  # overlayroot mounts it (an fstab line for p2 would name the boot partition of slot A).
  mkdir -p /boot/autoboot
  sed -i -e 's|\s/boot/firmware\s| /boot/autoboot |' -e '/\s\/\s/d' /etc/fstab

  # The admin page installs OS updates (features/updates/service.py: update_os): it downloads the
  # bundle to this one file and has RAUC install it. RAUC installs only bundles signed with our key.
  put 0440 /etc/sudoers.d/mirrordash-rauc
  visudo -cf /etc/sudoers.d/mirrordash-rauc
}

step_first_boot_lock() {
  # On the locked root, / is an overlay that can't be remounted: systemd-remount-fs fails, and is
  # retried (and fails) every time a unit starts. Nothing to remount there, so skip it.
  mkdir -p /etc/systemd/system/systemd-remount-fs.service.d
  put 0644 /etc/systemd/system/systemd-remount-fs.service.d/mirrordash.conf
  # Swap in compressed RAM only: the default zram+file puts a swap file on the root partition,
  # which is a RAM overlay once locked (and needs systemd-remount-fs).
  # This setting is read by Pi OS's rpi-swap. If a new Pi OS drops it, the setting would silently do
  # nothing (and a swap file would land on the RAM overlay), so stop the build instead.
  if [ ! -e /usr/lib/systemd/system-generators/rpi-swap-generator ]; then
    echo "Error: Pi OS's rpi-swap is gone; swap needs setting up another way (step_first_boot_lock)." >&2
    exit 1
  fi
  mkdir -p /etc/rpi/swap.conf.d
  put 0644 /etc/rpi/swap.conf.d/mirrordash.conf

  # ponytail: a run straight on a Pi (no BUILDING_IMAGE) stays unlocked; real images come from build_image.sh.
  if [ -z "${BUILDING_IMAGE:-}" ]; then
    echo "Not an image build — skipping the lock."
    return 0
  fi

  # Image builds are locked from the first boot: the root is a squashfs and cmdline.txt has overlayroot
  # (step_watchdog_boot_optimization). cloud-init would see a new machine every boot (its state is on
  # the RAM overlay) and set it up again; Pi OS's seed files on the boot partition are empty anyway.
  touch /etc/cloud/cloud-init.disabled
  # Pi OS grows the root over the card on its first boot (rpi-resize.service). The service itself is
  # skipped here (no first boot), but it still pulls in systemd-growfs-root, which fails on the
  # overlay root at every boot. The card is laid out by mirrordash-repart.sh instead.
  # Masking a unit that doesn't exist succeeds silently, so a renamed one is caught here.
  if [ ! -e /usr/lib/systemd/system/rpi-resize.service ]; then
    echo "Error: Pi OS's rpi-resize.service is gone or renamed; find what grows the root now and turn it off." >&2
    exit 1
  fi
  systemctl --root=/ mask rpi-resize.service
}

step_system_cleanup() {
  echo "Restoring update-initramfs diversion..."
  rm -f /usr/sbin/update-initramfs || true
  dpkg-divert --local --rename --remove /usr/sbin/update-initramfs || true

  echo "Rebuilding initramfs to embed plymouth and kernel updates..."
  sed -i 's/^MODULES=.*/MODULES=most/' /etc/initramfs-tools/initramfs.conf
  # The root is a squashfs in image builds, and it's a module in Pi OS's kernels
  grep -qx squashfs /etc/initramfs-tools/modules || echo squashfs >> /etc/initramfs-tools/modules
  
  # Temporarily mask the root filesystem in fstab so mkinitramfs doesn't crash trying to probe it
  cp /etc/fstab /etc/fstab.bak
  trap 'mv -f /etc/fstab.bak /etc/fstab 2>/dev/null || true' EXIT ERR INT TERM
  sed -i '/ \/ /s/^/#/' /etc/fstab
  # ponytail: a non-zero exit is tolerated (it crashed under nspawn before); build_image.sh instead
  # checks the result: overlayroot, squashfs and the boot theme must be inside the initramfs.
  update-initramfs -u || echo "Warning: update-initramfs exited with a non-zero status"
  mv /etc/fstab.bak /etc/fstab
  trap - EXIT ERR INT TERM

  echo "Cleaning up build policies and caches..."
  rm -f /usr/sbin/policy-rc.d
  apt-get clean
  rm -rf /var/lib/apt/lists/*

  echo "Pruning package manager caches..."
  apt-get purge -y --auto-remove man-db vim-tiny nano wireless-tools || true
  apt-get clean
  apt-get autoremove -y

  echo "Purging heavy international fonts..."
  apt-get purge -y "fonts-noto-cjk*" "fonts-noto-core*" "fonts-kacst*" "fonts-tlwg*" "fonts-nanum*" || true
  apt-get autoremove -y

  echo "Removing heavy UI assets..."
  rm -rf /usr/share/icons/Adwaita
  rm -rf /usr/share/icons/hicolor
  rm -rf /usr/share/backgrounds/*
  rm -rf /usr/share/doc/*
  rm -rf /usr/share/man/*

  echo "Pruning temporary directories and system caches..."
  rm -rf /tmp/* /var/tmp/*
  rm -rf /root/.cache /home/pi/.cache
  rm -rf /var/lib/apt/lists/*
  rm -rf /home/pi/.local/share/uv/cache
  rm -rf /root/.local/share/uv/cache

  echo "Purging unnecessary firmware..."
  rm -rf /lib/firmware/amdgpu
  rm -rf /lib/firmware/radeon
  rm -rf /lib/firmware/nvidia
  rm -rf /lib/firmware/intel
  rm -rf /lib/firmware/mellanox
  rm -rf /lib/firmware/iwlwifi*

  echo "Truncating system logs and journals..."
  find /var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true
  journalctl --vacuum-time=1s 2>/dev/null || true

  echo "Clearing bash and execution histories..."
  rm -f /root/.bash_history /home/pi/.bash_history
  history -c 2>/dev/null || true
}
# --- Execution ---

START_TIME_TOTAL=$SECONDS

run_step "1" "expanding_partition" "Expanding Partition & Setting up Persistent Storage" step_expanding_partition
run_step "2" "installing_packages" "Updating and Installing APT Packages" step_installing_packages
run_step "2b" "media" "Installing the Media Stack (GStreamer)" step_media
run_step "3" "setting_hostname" "Setting Hostname & Enabling mDNS" step_setting_hostname
run_step "4" "configuring_nginx" "Configuring nginx Reverse Proxy" step_configuring_nginx
run_step "5" "configuring_console_login" "Configuring Console Auto-Login" step_configuring_console_login
run_step "6" "setting_up_wayland" "Setting up Wayland Auto-launch & Kiosk Config" step_setting_up_wayland
run_step "7" "installing_app" "Installing uv & MirrorDash App" step_installing_app
run_step "8" "passwordless_sudo" "Setting up Passwordless Sudo" step_passwordless_sudo
run_step "8b" "gpio_helper" "Installing GPIO Overlay Helper" step_gpio_helper
run_step "9" "watchdog_boot_optimization" "Enabling Watchdog & Optimizing Boot" step_watchdog_boot_optimization
run_step "10" "plymouth_splash" "Configuring Plymouth Splash Screen" step_plymouth_splash
run_step "11" "time_wait_sync" "Enabling systemd-time-wait-sync" step_time_wait_sync
run_step "12" "wifi_captive_portal" "Creating WiFi Fallback Captive Portal Script & Service" step_wifi_captive_portal
run_step "13" "systemd_service" "Creating MirrorDash Background Service" step_systemd_service
run_step "15" "repart_service" "Creating MBR Repart Service" step_repart_service
run_step "16" "storage_hydration" "Creating First-Boot Storage Hydration Service" step_storage_hydration
run_step "16a" "dev_logging" "Setting up Developer Logging" step_dev_logging
run_step "16b" "ab_updates" "Setting up A/B OS Updates (RAUC)" step_ab_updates
run_step "17" "first_boot_lock" "Locking the Root (OverlayFS)" step_first_boot_lock
run_step "18" "system_cleanup" "Performing System Cleanup" step_system_cleanup

END_TIME_TOTAL=$SECONDS
DURATION_TOTAL=$((END_TIME_TOTAL - START_TIME_TOTAL))
minutes=$((DURATION_TOTAL / 60))
seconds=$((DURATION_TOTAL % 60))

echo "=========================================================="
echo " MirrorDash setup successfully completed in ${minutes}m ${seconds}s!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="

