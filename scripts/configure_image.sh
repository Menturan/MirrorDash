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
  cat << 'EOF' > /etc/NetworkManager/conf.d/mirrordash-wifi-powersave.conf
[connection]
wifi.powersave = 2
EOF

  echo "Enabling avahi-daemon service..."
  systemctl --root=/ enable avahi-daemon.service
  # Going from the setup hotspot to home Wi-Fi, avahi sees its own probe as a name conflict and
  # stays mirrordash-2.local until restarted, so mirrordash.local/admin breaks right after setup.
  mkdir -p /etc/NetworkManager/dispatcher.d
  cat << 'EOF' > /etc/NetworkManager/dispatcher.d/50-mirrordash-avahi
#!/bin/sh
[ "$2" = up ] && [ "$CONNECTION_ID" != MirrorDash-Setup ] && systemctl try-restart avahi-daemon.service
exit 0
EOF
  chmod 755 /etc/NetworkManager/dispatcher.d/50-mirrordash-avahi
}

step_configuring_nginx() {
  rm -f /etc/nginx/sites-enabled/default
  cat << 'EOF' > /etc/nginx/sites-available/mirrordash
server {
    listen 80 default_server;
    server_name mirrordash.local _;

    # WebSocket endpoint — must upgrade the connection
    location /ws {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        proxy_read_timeout 86400;
    }

    # All other requests
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    }
}
EOF
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
  cat << 'EOF' > /etc/systemd/system/labwc-kiosk.service
[Unit]
Description=Labwc Kiosk Wayland Compositor
After=systemd-user-sessions.service plymouth-start.service seatd.service
Wants=seatd.service
Conflicts=getty@tty1.service autologin@tty1.service plymouth-quit-wait.service

[Service]
User=pi
PAMName=login
WorkingDirectory=~
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardOutput=journal
StandardError=journal
Environment=WLR_LIBINPUT_NO_DEVICES=1
# The empty cursor theme (below), set here too: with only labwc/environment the pointer still showed
Environment=XCURSOR_THEME=empty
Environment=XCURSOR_SIZE=24
ExecStartPre=+-/usr/bin/plymouth quit --retain-splash
ExecStart=/usr/bin/labwc
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable labwc-kiosk.service

  echo "Configuring seatd permissions for unprivileged Wayland access..."
  # Ensure seatd.service is explicitly enabled using offline mode to avoid container build D-Bus/PID 1 errors.
  systemctl --root=/ enable seatd.service

  # Configure seatd to use the 'video' group instead of 'seat' because the 'pi' user is
  # created on first boot and is not added to the 'seat' group by default, but is always in 'video'.
  mkdir -p /etc/systemd/system/seatd.service.d
  cat << 'EOF' > /etc/systemd/system/seatd.service.d/group.conf
[Service]
ExecStart=
ExecStart=/usr/sbin/seatd -g video
EOF

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
  cat << 'EOF' > /etc/systemd/system/cog-kiosk.service
[Unit]
Description=Cog WebKit Kiosk
After=labwc-kiosk.service
BindsTo=labwc-kiosk.service
StartLimitIntervalSec=0

[Service]
User=pi
Environment="WAYLAND_DISPLAY=wayland-0"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="COG_PLATFORM_WL_VIEW_FULLSCREEN=1"
Environment="XCURSOR_THEME=empty"
ExecStart=/usr/bin/cog -P wl --bg-color=black file:///home/pi/mirrordash/loading.html
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  # labwc has no readiness notification, so start cog when its Wayland socket appears
  # (otherwise cog races labwc and aborts with "preferred module 'wl' not supported").
  # PathExists re-triggers after labwc restarts, since BindsTo stops cog with it.
  cat << 'EOF' > /etc/systemd/system/cog-kiosk.path
[Unit]
Description=Start Cog once the labwc Wayland socket exists

[Path]
PathExists=/run/user/1000/wayland-0

[Install]
WantedBy=multi-user.target
EOF
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
  cat << 'EOF' > /etc/sudoers.d/mirrordash
# MirrorDash application — scoped passwordless sudo
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl start ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-timezone *
pi ALL=(ALL) NOPASSWD: /usr/bin/raspi-config nonint do_wifi_country *
pi ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
pi ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/class/backlight/*/brightness
pi ALL=(ALL) NOPASSWD: /usr/bin/ddcutil --noverify setvcp 10 *
pi ALL=(ALL) NOPASSWD: /usr/sbin/reboot
pi ALL=(ALL) NOPASSWD: /usr/sbin/poweroff
pi ALL=(ALL) NOPASSWD: /usr/local/bin/mirrordash-gpio-overlays
# nmcli gets the Wi-Fi password as an argument: keep it out of the journal (and the admin Logs tab).
# ponytail: it still shows in `ps` while nmcli runs (local only); passwd-file would close that.
Defaults!/usr/bin/nmcli !log_allowed
EOF
  chmod 440 /etc/sudoers.d/mirrordash
  visudo -cf /etc/sudoers.d/mirrordash
}

step_gpio_helper() {
  # Root-owned helper the admin page calls (via sudo) to set which sensors/inputs use which pins.
  # It only writes a managed block of device-tree overlays to config.txt; the firmware applies
  # them at the next boot (overlays applied at boot can't be swapped safely at runtime).
  cat << 'EOF' > /usr/local/bin/mirrordash-gpio-overlays
#!/bin/bash
# Usage: mirrordash-gpio-overlays [type:value ...]
#   button, button_2..4, pir, mmwave, dht11: GPIO number 2-27      light (BH1750 on I2C): 0x23 or 0x5c
#   fan, pwm_fan: GPIO:temperature, e.g. fan:14:60 (switch-on temperature 40-80 °C)
# Each type at most once, one fan, every GPIO at most once, GPIO 2/3 reserved while I2C is used.
set -euo pipefail

die() { echo "$*" >&2; exit 2; }
declare -A seen_type=() seen_pin=()
lines=()
for arg in "$@"; do
  type=${arg%%:*} value=${arg#*:} temp=""
  if [ "$type" = fan ] || [ "$type" = pwm_fan ]; then
    temp=${value#*:} value=${value%%:*}
    { [[ "$temp" =~ ^[0-9]+$ ]] && [ "$temp" -ge 40 ] && [ "$temp" -le 80 ]; } || die "invalid fan temperature: $arg"
  fi
  [ -z "${seen_type[$type]:-}" ] || die "$type given twice"
  seen_type[$type]=1
  case "$type" in
    button|button_2|button_3|button_4|pir|mmwave|dht11|fan|pwm_fan)
      { [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 2 ] && [ "$value" -le 27 ]; } || die "invalid GPIO: $arg"
      [ -z "${seen_pin[$value]:-}" ] || die "GPIO $value used twice"
      seen_pin[$value]=1 ;;
    light)
      [ "$value" = 0x23 ] || [ "$value" = 0x5c ] || die "invalid I2C address: $arg" ;;
    *)
      die "unknown device type: $arg" ;;
  esac
  case "$type" in
    # Buttons/sensors become input devices (keycodes KEY_PROG1-3, KEY_MACRO1-3): kernel debounce, no polling
    button) lines+=("dtoverlay=gpio-key,gpio=$value,active_low=1,gpio_pull=up,keycode=148,label=mirrordash-button") ;;
    button_2) lines+=("dtoverlay=gpio-key,gpio=$value,active_low=1,gpio_pull=up,keycode=656,label=mirrordash-button-2") ;;
    button_3) lines+=("dtoverlay=gpio-key,gpio=$value,active_low=1,gpio_pull=up,keycode=657,label=mirrordash-button-3") ;;
    button_4) lines+=("dtoverlay=gpio-key,gpio=$value,active_low=1,gpio_pull=up,keycode=658,label=mirrordash-button-4") ;;
    pir)    lines+=("dtoverlay=gpio-key,gpio=$value,active_low=0,gpio_pull=down,keycode=149,label=mirrordash-pir") ;;
    mmwave) lines+=("dtoverlay=gpio-key,gpio=$value,active_low=0,gpio_pull=down,keycode=150,label=mirrordash-mmwave") ;;
    dht11)  lines+=("dtoverlay=dht11,gpiopin=$value") ;;
    light)  lines+=("dtparam=i2c_arm=on" "dtoverlay=i2c-sensor,bh1750,addr=$value") ;;
    # The kernel's thermal framework runs the fan by CPU temperature (millidegrees)
    fan)    lines+=("dtoverlay=gpio-fan,gpiopin=$value,temp=$((temp * 1000)),hyst=5000") ;;
    pwm_fan) lines+=("dtoverlay=pwm-gpio-fan,fan_gpio=$value,fan_temp0=$((temp * 1000)),fan_temp1=$((temp * 1000 + 5000)),fan_temp2=$((temp * 1000 + 12500)),fan_temp3=$((temp * 1000 + 20000))") ;;
  esac
done
if [ -n "${seen_type[fan]:-}" ] && [ -n "${seen_type[pwm_fan]:-}" ]; then die "only one fan"; fi
if [ -n "${seen_type[light]:-}" ] && { [ -n "${seen_pin[2]:-}" ] || [ -n "${seen_pin[3]:-}" ]; }; then
  die "GPIO 2 and 3 are needed for I2C"
fi

CONFIG=/boot/firmware/config.txt
BEGIN='# --- MirrorDash GPIO (managed by the admin page) ---'
END='# --- end MirrorDash GPIO ---'

{
  awk -v b="$BEGIN" -v e="$END" '$0 == b { skip = 1 } !skip { print } $0 == e { skip = 0 }' "$CONFIG"
  echo "$BEGIN"
  echo "[all]"  # applies to every Pi model, whatever section the file ended in
  if [ ${#lines[@]} -gt 0 ]; then printf '%s\n' "${lines[@]}"; fi
  echo "$END"
} > "$CONFIG.mirrordash-tmp"
mv "$CONFIG.mirrordash-tmp" "$CONFIG"
sync
EOF
  chmod 755 /usr/local/bin/mirrordash-gpio-overlays
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
  cat << 'EOF' > /usr/local/bin/mirrordash-wifi-check.sh
#!/bin/bash
# At boot: no network within 30 s -> start the setup hotspot. With --watch (every 3 minutes, from
# mirrordash-wifi-watch.timer): on the hotspot, try the saved Wi-Fi again (a router that came back
# after a power cut); off it, a Wi-Fi that is gone for good starts the hotspot without a restart.
INTERFACE="wlan0"
SSID="MirrorDash-Setup"
CACHE_FILE="/var/lib/mirrordash-wifi-scan.cache"

hotspot_active() {
    nmcli -t -f NAME connection show --active | grep -qxF "$SSID"
}

# A Wi-Fi the mirror has joined before (a new mirror has none, so its hotspot just stays up)
has_saved_wifi() {
    nmcli -t -f TYPE,NAME connection show | grep '^802-11-wireless:' | grep -vqxF "802-11-wireless:$SSID"
}

# Someone is on the hotspot right now (in the middle of setup): don't pull it away from them.
# ponytail: neighbour states, since iw isn't in the image; a phone idle for a minute counts as gone.
phone_on_hotspot() {
    ip neigh show dev "$INTERFACE" | grep -qE 'REACHABLE|DELAY|PROBE'
}

# The hotspot came or went while MirrorDash runs: its screen reloads into the setup prompt or the
# mirror. Nothing to tell at boot (MirrorDash starts after this), so a failure here is harmless.
tell_mirrordash() {
    curl -fsS -m 5 -X POST http://127.0.0.1:8000/api/wifi/changed >/dev/null || true
}

start_hotspot() {
    logger -t mirrordash-wifi "No network. Scanning before entering AP mode..."
    # Scan for nearby networks BEFORE entering AP mode (the radio can't scan as a hotspot).
    # --rescan yes returns once the fresh scan has finished.
    SCAN_RESULT=$(nmcli -t -f SSID dev wifi list --rescan yes | sort -u | grep -v '^$' || true)
    echo "$SCAN_RESULT" > "$CACHE_FILE"
    chmod 644 "$CACHE_FILE"
    logger -t mirrordash-wifi "Cached $(echo "$SCAN_RESULT" | grep -c . || echo 0) visible networks for captive portal."

    # Each mirror keeps its own hotspot password: the profile lives on /storage (bind-mounted
    # system-connections), so it survives reboots until the card is reflashed. The app reads the
    # password back from NetworkManager and shows it, with a QR code, on the mirror only.
    # Profiles from older images used the shared password "mirrordash"; those get a new one.
    SAVED_PASSWORD=$(nmcli -s -g 802-11-wireless-security.psk connection show "$SSID" 2>/dev/null || true)
    if [ -z "$SAVED_PASSWORD" ] || [ "$SAVED_PASSWORD" = "mirrordash" ]; then
        nmcli connection delete "$SSID" 2>/dev/null || true
        # 10 characters without look-alikes (no i l o 0 1); nothing that needs escaping in a Wi-Fi QR code.
        # ponytail: ~50 bits, enough for a hotspot that is only up during setup; add characters if that changes.
        PASSWORD=$(tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' </dev/urandom | head -c 10)
        nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode ap
        nmcli connection modify "$SSID" wifi-sec.key-mgmt wpa-psk
        nmcli connection modify "$SSID" wifi-sec.psk "$PASSWORD"
        nmcli connection modify "$SSID" wifi-sec.pmf 1
        nmcli connection modify "$SSID" ipv4.method shared
        # Only this script starts the hotspot; a saved AP profile must never come up on its own
        nmcli connection modify "$SSID" connection.autoconnect no
        logger -t mirrordash-wifi "Created hotspot '$SSID' with a new password."
    fi

    if nmcli connection up "$SSID"; then
        logger -t mirrordash-wifi "Hotspot '$SSID' started successfully."
    else
        logger -t mirrordash-wifi "Failed to start hotspot."
    fi
}

if [ "${1:-}" = "--watch" ] && hotspot_active; then
    if ! has_saved_wifi || phone_on_hotspot; then
        exit 0
    fi
    logger -t mirrordash-wifi "On the hotspot: trying the saved Wi-Fi again..."
    nmcli connection down "$SSID" || true
    # device connect picks the best saved profile, even one NetworkManager stopped retrying
    if nmcli --wait 45 device connect "$INTERFACE" && nm-online -q -t 15; then
        logger -t mirrordash-wifi "Back on the saved Wi-Fi."
        tell_mirrordash
        exit 0
    fi
    logger -t mirrordash-wifi "The saved Wi-Fi isn't there. Back to the hotspot."
    nmcli connection up "$SSID" || logger -t mirrordash-wifi "Failed to start hotspot."
    tell_mirrordash
    exit 0
fi

# nm-online waits on NetworkManager's own state: startup finished, then a connection (or the
# timeout). Any connection counts, also one without internet. --watch waits longer, so a Wi-Fi
# change from the admin page (a few seconds without a connection) never starts the hotspot.
if nm-online -q -t "$([ "${1:-}" = "--watch" ] && echo 60 || echo 30)"; then
    exit 0
fi
start_hotspot
[ "${1:-}" = "--watch" ] && tell_mirrordash
exit 0
EOF
  chmod +x /usr/local/bin/mirrordash-wifi-check.sh

  # Captive portal DNS: NetworkManager's dnsmasq reads this directory only for shared (hotspot)
  # connections, never on the home network. Every name answers with the mirror, so the phone's
  # connectivity check (captive.apple.com, connectivitycheck.gstatic.com, ...) reaches the app,
  # gets redirected, and the phone shows "Sign in to network" by itself.
  mkdir -p /etc/NetworkManager/dnsmasq-shared.d
  echo 'address=/#/10.42.0.1' > /etc/NetworkManager/dnsmasq-shared.d/mirrordash-captive.conf

  cat << 'EOF' > /etc/systemd/system/mirrordash-wifi-fallback.service
[Unit]
Description=MirrorDash WiFi Fallback Captive Portal Monitor
After=NetworkManager.service
Before=mirrordash.service

[Service]
# oneshot: mirrordash.service (Before=) only starts once the client-vs-hotspot decision
# is made, so the first page the kiosk loads already reflects the real network state.
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-wifi-check.sh
RemainAfterExit=yes
TimeoutStartSec=90

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash-wifi-fallback.service

  # Every 3 minutes: the hotspot tries the saved Wi-Fi again, and a Wi-Fi gone for good starts it
  cat << 'EOF' > /etc/systemd/system/mirrordash-wifi-watch.service
[Unit]
Description=MirrorDash WiFi watch (hotspot <-> saved Wi-Fi)
After=mirrordash-wifi-fallback.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-wifi-check.sh --watch
TimeoutStartSec=150
EOF
  cat << 'EOF' > /etc/systemd/system/mirrordash-wifi-watch.timer
[Unit]
Description=MirrorDash WiFi watch every 3 minutes

[Timer]
OnBootSec=3min
OnUnitInactiveSec=3min

[Install]
WantedBy=timers.target
EOF
  systemctl --root=/ enable mirrordash-wifi-watch.timer
}

step_systemd_service() {
  cat << 'EOF' > /etc/systemd/system/mirrordash.service
[Unit]
Description=MirrorDash Core App Backend
After=network.target mirrordash-storage-init.service
Requires=mirrordash-storage-init.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mirrordash
Environment="PATH=/home/pi/mirrordash/.venv/bin:/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="VIRTUAL_ENV=/home/pi/mirrordash/.venv"
Environment="WAYLAND_DISPLAY=wayland-0"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
ExecStart=/home/pi/mirrordash/launch.sh
# The app restarts itself via SIGTERM (exit 143); that is not a failure
SuccessExitStatus=143
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash.service
}

step_repart_service() {
  echo "Creating MBR partition expander script..."
  cat << 'EOF' > /usr/local/bin/mirrordash-repart.sh
#!/bin/bash
# MirrorDash MBR Partition Expander: runs at every boot, and only does something on the first one.
#
# Two layouts exist:
#   * An image from build_image.sh (A/B OS updates, see step_ab_updates):
#       p1 autoboot (Pi firmware + autoboot.txt), p2 boot A, p3 boot B, p4 extended partition,
#       p5 root A, p6 root B, p7 /storage. The image only holds p1-p5, so it stays small to flash;
#       this script adds p6 and p7 on the first boot. p4 is the "box" (extended partition) that MBR
#       needs to hold more than 4 partitions; p5, p6 and p7 live inside it.
#   * A Pi set up by running configure_image.sh by hand: p1 boot, p2 root, and this script adds
#     p3 /storage after it.
set -euo pipefail

# The root partition from the kernel's root= (PARTUUID=...): / is an overlay on a locked mirror
ROOT_PART=$(findfs "$(sed -n 's/\(^\|.* \)root=\([^ ]*\).*/\2/p' /proc/cmdline)")
ROOT_NAME=$(basename "$ROOT_PART")
DISK="/dev/$(lsblk -ndo PKNAME "$ROOT_PART")"
# The kernel's naming: a "p" before the number when the disk name ends in a digit (mmcblk0p3, sda3)
case "$DISK" in
    *[0-9]) PART_PREFIX="${DISK}p" ;;
    *)      PART_PREFIX="${DISK}" ;;
esac

# --- A/B image: add root B (p6, empty until the first OS update) and /storage (p7) ---
if [ -b "${PART_PREFIX}5" ]; then
    if [ -b "${PART_PREFIX}7" ]; then
        echo "A/B layout is complete."
        exit 0
    fi
    echo "First boot of an A/B image: adding root B (p6) and /storage (p7)..."
    # Only sfdisk touches this table, never parted: on a test card, parted rewrote the disk
    # identifier while growing p4. The identifier is the first half of every PARTUUID, and
    # cmdline.txt finds the root by it (root=PARTUUID=<identifier>-05), so the next boot would have
    # found no root. sfdisk keeps it. --no-reread / --no-tell-kernel: the card is in use (the root
    # is on it), so sfdisk only writes the table; partx below tells the kernel about p6 and p7.
    #
    # 1. Grow the extended partition (p4) to the end of the card, so p6 and p7 fit inside it.
    echo ',+' | sfdisk -q --no-reread --no-tell-kernel -N 4 "$DISK"
    # 2. Add p6 (root B, 1.5 GiB like root A) and p7 (/storage, the rest) inside it. Positions in
    #    512-byte sectors (/sys gives p5's start and size in them; 2048 sectors = 1 MiB). Each starts
    #    1 MiB after the previous one ends: every partition inside the extended one has a small
    #    header (an EBR) in front of it, and whole MiB keep the card's erase blocks aligned.
    P5_NAME=$(basename "${PART_PREFIX}5")
    P5_END=$(( $(cat "/sys/class/block/$P5_NAME/start") + $(cat "/sys/class/block/$P5_NAME/size") ))
    P6_START=$(( P5_END + 2048 ))
    P6_SIZE=$(( 1536 * 2048 ))
    P7_START=$(( P6_START + P6_SIZE + 2048 ))
    printf 'start=%s, size=%s, type=83\nstart=%s, type=83\n' "$P6_START" "$P6_SIZE" "$P7_START" |
        sfdisk -q --no-reread --no-tell-kernel --append "$DISK"
    # The card is in use (the root is on it), so the kernel can't reread the whole table:
    # partx -a adds just the new partitions. It complains about the ones that exist; that's fine.
    partx -a "$DISK" || true
    udevadm settle
    if [ ! -b "${PART_PREFIX}7" ]; then
        echo "Error: ${PART_PREFIX}7 did not appear after creating it" >&2
        exit 1
    fi
    echo "Formatting ${PART_PREFIX}7 as ext4 with label 'mirrordash-data'..."
    mkfs.ext4 -F -L mirrordash-data "${PART_PREFIX}7"
    udevadm settle
    exit 0
fi

# --- Hand-made setup: add /storage (p3) right after the root ---
PART_NUM=3
TARGET_PART="${PART_PREFIX}${PART_NUM}"

if [ ! -b "$TARGET_PART" ]; then
    echo "Persistent partition $TARGET_PART not found. Creating..."
    
    # Right after the root partition (start + size, in 512-byte sectors, from the kernel)
    START_SECTOR=$(( $(cat "/sys/class/block/$ROOT_NAME/start") + $(cat "/sys/class/block/$ROOT_NAME/size") ))
    echo "Creating partition $PART_NUM starting at ${START_SECTOR}s..."
    
    # Create partition using parted
    parted -s -a optimal "$DISK" -- mkpart primary ext4 "${START_SECTOR}s" 100%
    
    # Reload partition table and wait for udev to create the device node (using fallback for busy partition tables)
    partprobe "$DISK" || partx -a "$DISK" || true
    udevadm settle
    
    if [ -b "$TARGET_PART" ]; then
        echo "Formatting $TARGET_PART as ext4 with label 'mirrordash-data'..."
        mkfs.ext4 -F -L mirrordash-data "$TARGET_PART"
        # Ensure udev processes the new disk label symlink before exiting
        udevadm settle
    else
        echo "Error: Partition device $TARGET_PART did not appear after udevadm settle" >&2
        exit 1
    fi
else
    echo "Persistent partition $TARGET_PART already exists."
fi
EOF
  chmod +x /usr/local/bin/mirrordash-repart.sh

  echo "Configuring mirrordash-repart.service..."
  cat << 'EOF' > /etc/systemd/system/mirrordash-repart.service
[Unit]
Description=MirrorDash MBR Partition Expander
DefaultDependencies=no
After=systemd-udevd.service
Before=local-fs-pre.target
# Every boot (a no-op once the partition is there): an image's root is locked from the first boot

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-repart.sh
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
EOF

  systemctl --root=/ enable mirrordash-repart.service
}

step_storage_hydration() {
  cat << 'EOF' > /usr/local/bin/mirrordash-hydrate.sh
#!/bin/bash
set -euo pipefail

# Defensive mount check: if the partition exists but is not mounted, force-mount it
if [ -b "/dev/disk/by-label/mirrordash-data" ]; then
    if ! mountpoint -q /storage; then
        echo "Warning: /storage is not mounted but the partition exists. Mounting it..."
        mount /storage || mount /dev/disk/by-label/mirrordash-data /storage
    fi
fi

# Ensure parent directory and subdirectories exist on mounted /storage
mkdir -p /storage/mirrordash/data
mkdir -p /storage/mirrordash/system-connections
chown pi:pi /storage/mirrordash
chown pi:pi /storage/mirrordash/data
chown root:root /storage/mirrordash/system-connections
chmod 700 /storage/mirrordash/system-connections

# Bind mount the NetworkManager connections directory to bypass symlink security restrictions
if ! mountpoint -q /etc/NetworkManager/system-connections; then
    mkdir -p /etc/NetworkManager/system-connections
    mount --bind /storage/mirrordash/system-connections /etc/NetworkManager/system-connections
fi

# Clean up active venv if it is a real directory instead of a symlink
if [ -e /storage/mirrordash/venv ] && [ ! -L /storage/mirrordash/venv ]; then
    rm -rf /storage/mirrordash/venv
fi

# The venvs on /storage use the Python that is in the root (/home/pi/.local/share/uv/python/cpython-3.14-...).
# A new OS version (A/B update) with another Python minor version leaves them unable to start. Then
# they are thrown away and the app that came with this OS (base_venv) is seeded again below, as on a
# first boot. Modules installed from the admin page come back with Settings → Rebuild environment.
# (bin/python is a link to that Python; it's the link that breaks.)
if [ -L /storage/mirrordash/venv/bin/python ] && ! /storage/mirrordash/venv/bin/python -c ''; then
    echo "The app's Python doesn't start with this OS version: seeding the app that came with it."
    rm -rf /storage/mirrordash/venv /storage/mirrordash/venv_a /storage/mirrordash/venv_b /storage/mirrordash/venv_old
fi

# Seed venv_a only when the active link is missing or dangling (first boot / wiped storage).
# A valid link is owned by the A/B updater and may point at venv_b — never reset it.
if [ ! -d /storage/mirrordash/venv ]; then
    if [ ! -d "/storage/mirrordash/venv_a" ]; then
        echo "Hydrating /storage with golden base_venv..."
        rm -rf /storage/mirrordash/venv_a.tmp
        cp -a /home/pi/mirrordash/base_venv /storage/mirrordash/venv_a.tmp
        mv /storage/mirrordash/venv_a.tmp /storage/mirrordash/venv_a
        chown -R pi:pi /storage/mirrordash/venv_a
    fi
    ln -sfT venv_a /storage/mirrordash/venv
fi
chown pi:pi /storage/mirrordash /storage/mirrordash/venv

# SSH host keys live here: every mirror boots the same root image, and changes to it are gone at reboot.
# Only the missing ones are made (the first boot).
mkdir -p /storage/mirrordash/ssh/etc/ssh
ssh-keygen -A -f /storage/mirrordash/ssh

# RAUC (A/B OS updates) keeps its state here: the root forgets everything at reboot
mkdir -p /storage/rauc
EOF
  chmod +x /usr/local/bin/mirrordash-hydrate.sh

  cat << 'EOF' > /etc/ssh/sshd_config.d/mirrordash-hostkeys.conf
HostKey /storage/mirrordash/ssh/etc/ssh/ssh_host_ed25519_key
HostKey /storage/mirrordash/ssh/etc/ssh/ssh_host_ecdsa_key
HostKey /storage/mirrordash/ssh/etc/ssh/ssh_host_rsa_key
EOF
  mkdir -p /etc/systemd/system/ssh.service.d
  cat << 'EOF' > /etc/systemd/system/ssh.service.d/mirrordash.conf
[Unit]
# The host keys are on /storage, made by the hydrate script
After=mirrordash-storage-init.service
EOF

  cat << 'EOF' > /etc/systemd/system/mirrordash-storage-init.service
[Unit]
Description=Hydrate MirrorDash Storage Partition
After=local-fs.target
Requires=local-fs.target
Before=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-hydrate.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash-storage-init.service
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
  cat << 'EOF' > /etc/rauc/system.conf
[system]
compatible=mirrordash-rpi
bootloader=custom
# The root forgets everything at reboot; RAUC's state (which slot holds what) is kept on /storage
data-directory=/storage/rauc

[keyring]
path=/etc/rauc/ca.cert.pem

[handlers]
bootloader-custom-backend=/usr/local/lib/mirrordash/rauc-bootloader

[slot.rootfs.0]
device=/dev/mmcblk0p5
type=raw
bootname=A

[slot.rootfs.1]
device=/dev/mmcblk0p6
type=raw
bootname=B

[slot.boot.0]
device=/dev/mmcblk0p2
type=vfat
parent=rootfs.0

[slot.boot.1]
device=/dev/mmcblk0p3
type=vfat
parent=rootfs.1
EOF

  mkdir -p /usr/local/lib/mirrordash
  cat << 'EOF' > /usr/local/lib/mirrordash/rauc-bootloader
#!/bin/bash
# RAUC's bootloader backend for the Raspberry Pi firmware ("bootloader=custom" in /etc/rauc/system.conf).
# RAUC calls it to ask which slot boots and to switch slots.
#   Slot A = boot partition 2 + root partition 5;  slot B = boot partition 3 + root partition 6.
#
# How the firmware picks a boot partition: it reads autoboot.txt on p1 (mounted at /boot/autoboot):
#     [all]
#     tryboot_a_b=1
#     boot_partition=2     <- the "primary" slot, booted normally
#     [tryboot]
#     boot_partition=3     <- booted once, when the one-time tryboot flag is set
# The flag is set with a firmware call (vcmailbox, below) and cleared by the firmware at the next
# restart, whatever happens. The firmware tells Linux what it did in the device tree:
# /proc/device-tree/chosen/bootloader/partition (the boot partition) and .../tryboot (1 on a trial boot).
# All of this was tested on a Pi 3B before it was built (tryboot spike, October 2026).
#
# Commands (RAUC's custom backend interface, rauc.readthedocs.io "Custom bootloader backend"):
#   get-primary             the slot that boots normally (A or B)
#   set-primary SLOT        make SLOT boot at the next restart. For the other slot (just installed)
#                           that is a trial boot only; mark-good makes it permanent. Its boot
#                           partition is made ready first (prepare_boot).
#   get-state SLOT          always "good": a bad slot is never primary (see set-state)
#   set-state SLOT good|bad good on the running trial slot makes it the primary one
#   get-current             the slot that is running now
# Ours, not RAUC's:
#   is-tryboot              exit 0 on a trial boot (mirrordash-mark-good.service)
#   prepare-boot DIR N      what set-primary does to a boot partition, on the files in DIR for root
#                           partition N (tests/test_ab_updates.py)
# A non-zero exit tells RAUC the step failed.
set -euo pipefail

# Overridable so tests/test_ab_updates.py can run this against fake files
AUTOBOOT=${MIRRORDASH_AUTOBOOT:-/boot/autoboot/autoboot.txt}
BOOTLOADER_DT=${MIRRORDASH_BOOTLOADER_DT:-/proc/device-tree/chosen/bootloader}
CURRENT_BOOT=${MIRRORDASH_CURRENT_BOOT:-/boot/firmware}  # the boot partition that is running now
VCMAILBOX=${MIRRORDASH_VCMAILBOX:-vcmailbox}
# The same markers as in /usr/local/bin/mirrordash-gpio-overlays
GPIO_BEGIN='# --- MirrorDash GPIO (managed by the admin page) ---'
GPIO_END='# --- end MirrorDash GPIO ---'

die() { echo "rauc-bootloader: $*" >&2; exit 1; }
slot_of() { case "$1" in 2) echo A ;; 3) echo B ;; *) die "unknown boot partition: $1" ;; esac; }
partition_of() { case "$1" in A) echo 2 ;; B) echo 3 ;; *) die "unknown slot: $1" ;; esac; }
# Device-tree numbers are 4-byte big-endian integers
dt_number() { od -An -tu4 --endian=big "$BOOTLOADER_DT/$1" | tr -d ' '; }
# boot_partition= in the [all] section of autoboot.txt
primary_partition() {
  awk '/^\[/ { section = $0 } section == "[all]" && /^boot_partition=/ { sub(/^boot_partition=/, ""); print; exit }' "$AUTOBOOT"
}

# Swap the partitions in autoboot.txt ([all] 2 <-> [tryboot] 3): the slot that was tried becomes
# the normal one, and the old one becomes the one to try next. Written to a new file that then
# replaces the old one, with sync before and after: a power cut leaves either the old or the new
# file, never half of one (the card holds a single autoboot.txt; without it nothing boots).
swap_primary() {
  sed -e 's/^boot_partition=2$/boot_partition=X/' \
      -e 's/^boot_partition=3$/boot_partition=2/' \
      -e 's/^boot_partition=X$/boot_partition=3/' "$AUTOBOOT" > "$AUTOBOOT.tmp"
  sync "$AUTOBOOT.tmp"
  mv "$AUTOBOOT.tmp" "$AUTOBOOT"
  sync "$AUTOBOOT"
}

# prepare_boot DIR ROOT_NUMBER: the new boot partition (mounted at DIR) is a copy of the one in the
# bundle, which was built for slot A on no particular card. It gets what belongs to this mirror:
#   * root=PARTUUID=<card>-0N: its own root partition. A PARTUUID is the card's disk identifier
#     plus the partition number, so the identifier is taken from the running cmdline.txt.
#   * the Wi-Fi country (cfg80211.ieee80211_regdom= in cmdline.txt, set from the time zone).
#   * the GPIO block in config.txt (buttons, sensors, fans; written by mirrordash-gpio-overlays).
prepare_boot() {
  local dir=$1 root_number=$2 disk_id regdom
  disk_id=$(sed -n 's/.*root=PARTUUID=\([0-9a-f]*\)-.*/\1/p' "$CURRENT_BOOT/cmdline.txt")
  [ -n "$disk_id" ] || die "no root=PARTUUID= in $CURRENT_BOOT/cmdline.txt"
  sed -i "s/root=PARTUUID=[^ ]*/root=PARTUUID=${disk_id}-0${root_number}/" "$dir/cmdline.txt"

  regdom=$(grep -o 'cfg80211.ieee80211_regdom=[^ ]*' "$CURRENT_BOOT/cmdline.txt" || true)
  if [ -n "$regdom" ]; then
    sed -i "s/cfg80211.ieee80211_regdom=[^ ]*/$regdom/" "$dir/cmdline.txt"
  fi

  # The new config.txt without a GPIO block of its own (if it has one), then the running one's block
  {
    awk -v b="$GPIO_BEGIN" -v e="$GPIO_END" '$0 == b { skip = 1 } !skip { print } $0 == e { skip = 0 }' "$dir/config.txt"
    awk -v b="$GPIO_BEGIN" -v e="$GPIO_END" '$0 == b { copy = 1 } copy { print } $0 == e { copy = 0 }' "$CURRENT_BOOT/config.txt"
  } > "$dir/config.txt.new"
  mv "$dir/config.txt.new" "$dir/config.txt"
}

case "${1:-}" in
  get-primary)
    slot_of "$(primary_partition)" ;;
  set-primary)
    boot=$(partition_of "$2")
    if [ "$boot" != "$(primary_partition)" ]; then
      # The slot RAUC just installed: make its boot partition ready (p2 -> root p5, p3 -> root p6)
      mount_dir=$(mktemp -d)
      mount "/dev/mmcblk0p$boot" "$mount_dir"
      prepare_boot "$mount_dir" $(( boot + 3 ))
      sync
      umount "$mount_dir"
      rmdir "$mount_dir"
      # Firmware call "set reboot flags" (tag 0x00038064, a 4-byte value, flag 1 = tryboot).
      # The next restart boots the [tryboot] partition, once.
      "$VCMAILBOX" 0x00038064 4 0 1 > /dev/null
    fi ;;
  get-state)
    echo good ;;
  set-state)
    slot=$(partition_of "$2") primary=$(primary_partition) current=$(dt_number partition)
    case "$3" in
      good)
        # Only the slot that is running on a trial boot becomes primary
        if [ "$slot" = "$current" ] && [ "$slot" != "$primary" ]; then swap_primary; fi ;;
      bad)
        # RAUC marks the slot it's about to overwrite bad first. That must never be the primary
        # slot: if the install were cut short, nothing bootable would be left. It only can be
        # during a trial boot that isn't marked good yet, so the install is refused then.
        [ "$slot" != "$primary" ] || die "slot $2 is the one the mirror boots; finish the trial boot first" ;;
      *) die "unknown state: $3" ;;
    esac ;;
  get-current)
    slot_of "$(dt_number partition)" ;;
  is-tryboot)
    [ "$(dt_number tryboot)" = 1 ] ;;
  prepare-boot)
    prepare_boot "$2" "$3" ;;
  *)
    die "unknown command: ${1:-}" ;;
esac
EOF
  chmod 755 /usr/local/lib/mirrordash/rauc-bootloader

  # Step 4 above: keep a new OS version only if the mirror works with it. On a normal boot the
  # ExecCondition is false and nothing runs. On a trial boot each ExecStart must succeed in turn:
  #   nm-online: connected to the network within 5 minutes
  #   curl:      the app answers /health (curl retries itself every 5 s, up to 5 minutes)
  #   rauc:      mark this slot good, which makes it the one that boots (rauc-bootloader set-state)
  # If one fails, FailureAction restarts the mirror; the tryboot flag is gone by then, so it boots
  # the old slot. The app's own admin page then says the update didn't start (features/updates).
  cat << 'EOF' > /etc/systemd/system/mirrordash-mark-good.service
[Unit]
Description=Keep the new OS version if the mirror works with it (A/B update)
Wants=network-online.target
After=network-online.target mirrordash.service
FailureAction=reboot

[Service]
Type=oneshot
ExecCondition=/usr/local/lib/mirrordash/rauc-bootloader is-tryboot
ExecStart=/usr/bin/nm-online -q -t 300
ExecStart=/usr/bin/curl -fsS -o /dev/null --retry 60 --retry-delay 5 --retry-all-errors http://localhost:8000/health
ExecStart=/usr/bin/rauc status mark-good
TimeoutStartSec=12min

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash-mark-good.service

  # /boot/firmware is the boot partition the firmware started from: p2 (slot A) or p3 (slot B).
  # fstab can't say which, since both slots share this root image, so a systemd generator (a script
  # systemd runs very early at every boot) writes the mount from what the firmware reports.
  # The app writes the Wi-Fi country and the GPIO settings there, for the slot that is running.
  mkdir -p /etc/systemd/system-generators
  cat << 'EOF' > /etc/systemd/system-generators/mirrordash-bootslot
#!/bin/sh
# systemd generator: $1 is the directory for generated units (see man systemd.generator).
# The boot partition number comes from the firmware: a 4-byte big-endian integer in the device tree.
PARTITION=$(od -An -tu4 --endian=big /proc/device-tree/chosen/bootloader/partition 2>/dev/null | tr -d ' ')
# Not started by the Pi firmware's partition switch (0, or missing): leave /boot/firmware unmounted
case "$PARTITION" in 2|3) ;; *) exit 0 ;; esac
cat > "$1/boot-firmware.mount" << UNIT
[Unit]
Description=Boot partition the firmware started from (A/B slot)

[Mount]
What=/dev/mmcblk0p$PARTITION
Where=/boot/firmware
Type=vfat
Options=defaults,noatime
UNIT
mkdir -p "$1/local-fs.target.wants"
ln -sf ../boot-firmware.mount "$1/local-fs.target.wants/boot-firmware.mount"
EOF
  chmod 755 /etc/systemd/system-generators/mirrordash-bootslot

  # fstab: p1 (autoboot.txt) goes to /boot/autoboot instead of /boot/firmware, which the generator
  # above mounts. The root line goes: the root is whichever of p5/p6 the kernel got in root=, and
  # overlayroot mounts it (an fstab line for p2 would name the boot partition of slot A).
  mkdir -p /boot/autoboot
  sed -i -e 's|\s/boot/firmware\s| /boot/autoboot |' -e '/\s\/\s/d' /etc/fstab

  # The admin page installs OS updates (features/updates/service.py: update_os): it downloads the
  # bundle to this one file and has RAUC install it. RAUC installs only bundles signed with our key.
  cat << 'EOF' > /etc/sudoers.d/mirrordash-rauc
# MirrorDash: the admin page installs signed OS updates (A/B)
pi ALL=(ALL) NOPASSWD: /usr/bin/rauc install /storage/mirrordash/os-update.raucb
EOF
  chmod 440 /etc/sudoers.d/mirrordash-rauc
  visudo -cf /etc/sudoers.d/mirrordash-rauc
}

step_first_boot_lock() {
  # On the locked root, / is an overlay that can't be remounted: systemd-remount-fs fails, and is
  # retried (and fails) every time a unit starts. Nothing to remount there, so skip it.
  mkdir -p /etc/systemd/system/systemd-remount-fs.service.d
  cat << 'EOF' > /etc/systemd/system/systemd-remount-fs.service.d/mirrordash.conf
[Unit]
ConditionKernelCommandLine=!overlayroot
EOF
  # Swap in compressed RAM only: the default zram+file puts a swap file on the root partition,
  # which is a RAM overlay once locked (and needs systemd-remount-fs).
  mkdir -p /etc/rpi/swap.conf.d
  cat << 'EOF' > /etc/rpi/swap.conf.d/mirrordash.conf
[Main]
Mechanism=zram
EOF

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

