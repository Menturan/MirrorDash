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
  echo "Please run as root (sudo bash setup_appliance.sh)"
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
  ROOT_DEV_PARAM=$(cat /proc/cmdline | grep -o 'root=[^ ]*' | cut -d= -f2-)
  ROOT_PART=$(findfs "$ROOT_DEV_PARAM" 2>/dev/null || findmnt -n -o SOURCE /)
  PARENT_NAME=$(lsblk -no pkname "$ROOT_PART" 2>/dev/null | tr -d '[:space:]')

  if [ -n "$PARENT_NAME" ]; then
    ROOT_DISK="/dev/$PARENT_NAME"
  else
    if [[ "$ROOT_PART" =~ p[0-9]+$ ]]; then
      ROOT_DISK="${ROOT_PART%p[0-9]*}"
    else
      ROOT_DISK="${ROOT_PART%[0-9]*}"
    fi
  fi

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
      git
}

step_setting_hostname() {
  echo "mirrordash" > /etc/hostname
  sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts

  echo "Unblocking Wi-Fi radio permanently..."
  raspi-config nonint do_wifi_country US 2>/dev/null || true
  rfkill unblock wifi 2>/dev/null || true

  echo "Enabling avahi-daemon service..."
  systemctl --root=/ enable avahi-daemon.service
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

  echo "Provisioning headless user for Debian Trixie first-boot..."
  # Creates a userconf.txt in boot-partitionen with user 'pi' and password 'raspberry' (SHA-512 encrypted)
  echo "pi:$(echo 'raspberry' | openssl passwd -6 -stdin)" > /boot/firmware/userconf.txt
}

step_setting_up_wayland() {
  echo "Configuring labwc kiosk systemd service..."
  cat << 'EOF' > /etc/systemd/system/labwc-kiosk.service
[Unit]
Description=Labwc Kiosk Wayland Compositor
After=systemd-user-sessions.service plymouth-start.service seatd.service
Wants=seatd.service cog-kiosk.service
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
  systemctl --root=/ enable cog-kiosk.service

  echo "Setting up hourly OS safeguard to purge browser cache from RAM overlay..."
  cat << 'EOF' > /etc/cron.hourly/mirrordash-cache-purge
#!/bin/sh
# Aggressively clear the WebKit browser cache to prevent RAM overlay exhaustion
rm -rf /home/pi/.cache/wpe/* 2>/dev/null || true
rm -rf /home/pi/.cache/cog/* 2>/dev/null || true
EOF
  chmod +x /etc/cron.hourly/mirrordash-cache-purge

  echo "Masking default getty and autologin on tty1 to prevent terminal login preemption..."
  systemctl --root=/ mask getty@tty1.service autologin@tty1.service
}

step_installing_app() {
  # Install uv globally (standalone binary) securely via release artifact
  echo "Downloading uv binary securely to /usr/local/bin..."
  curl -sSLf https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz | tar -xz -C /usr/local/bin --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
  chmod +x /usr/local/bin/uv

  # Create app directory
  sudo -u "$PI_USER" HOME="$PI_HOME" mkdir -p "$PI_HOME/mirrordash"

  # Setup symlink structures (A/B venv layout & persistent data)
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT /storage/mirrordash/venv "$PI_HOME/mirrordash/.venv"
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT /storage/mirrordash/data "$PI_HOME/.mirrordash"

  # Create base_venv (Golden Copy), then install from PyPI
  sudo -u "$PI_USER" HOME="$PI_HOME" PATH="$PI_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" bash -e << 'EOF'
  cd "$HOME/mirrordash"

  echo 'Creating golden backup virtual environment base_venv...'
  rm -rf "$HOME/mirrordash/base_venv"
  uv venv --allow-existing --python 3.14 "$HOME/mirrordash/base_venv"

  echo 'Installing MirrorDash (PyPI) and clock module (GitHub) into golden venv...'
  uv pip install --python "$HOME/mirrordash/base_venv" mirrordash git+https://github.com/Menturan/mirrordash-clock.git
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
pi ALL=(ALL) NOPASSWD: /usr/bin/mount -o remount\,rw /
pi ALL=(ALL) NOPASSWD: /usr/bin/mount -o remount\,ro /
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl start ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop ssh
pi ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-timezone *
pi ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
pi ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/class/backlight/*/brightness
pi ALL=(ALL) NOPASSWD: /usr/sbin/reboot
EOF
  chmod 440 /etc/sudoers.d/mirrordash
  visudo -cf /etc/sudoers.d/mirrordash
}

step_watchdog_boot_optimization() {
  # Watchdog RuntimeWatchdogSec=14s
  sed -i 's/#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=14s/' /etc/systemd/system.conf

  # Suppress splash, boot delay, Bluetooth, allocate gpu memory in config.txt
  if ! grep -q "disable_splash=1" /boot/firmware/config.txt; then
    cat << 'EOF' >> /boot/firmware/config.txt

# --- MirrorDash Hardware Hardening ---
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
      # Skip auto-resize parameters or legacy console configurations
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
    )

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
  # Skip rebuilding initramfs if our custom theme is already configured
  if [ "$(plymouth-set-default-theme)" = "mirrordash" ]; then
    echo "Plymouth splash screen is already configured. Skipping rebuild."
    return 0
  fi

  echo "Cloning 'pix' Plymouth theme to create a robust 'mirrordash' theme..."
  mkdir -p /usr/share/plymouth/themes/mirrordash
  cp -r /usr/share/plymouth/themes/pix/* /usr/share/plymouth/themes/mirrordash/ || true

  if [ -f /usr/share/plymouth/themes/mirrordash/pix.plymouth ]; then
    mv /usr/share/plymouth/themes/mirrordash/pix.plymouth /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
    sed -i 's/Name=Raspberry Pi/Name=MirrorDash/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
    sed -i 's/\/usr\/share\/plymouth\/themes\/pix/\/usr\/share\/plymouth\/themes\/mirrordash/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
    sed -i 's/pix\.script/mirrordash.script/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
    mv /usr/share/plymouth/themes/mirrordash/pix.script /usr/share/plymouth/themes/mirrordash/mirrordash.script
  fi

  # Apply our clean script modifications safely to our cloned theme
  if [ -f /usr/share/plymouth/themes/mirrordash/mirrordash.script ]; then
    echo "Silencing Plymouth message callbacks in mirrordash.script..."
    sed -i 's/^[[:space:]]*Plymouth\.SetMessageFunction/# Plymouth.SetMessageFunction/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
    sed -i 's/^[[:space:]]*Plymouth\.SetUpdateStatusFunction/# Plymouth.SetUpdateStatusFunction/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
    
    if ! grep -q 'Plymouth\.GetMode() == "shutdown"' /usr/share/plymouth/themes/mirrordash/mirrordash.script; then
      echo "Patching mirrordash.script to support separate shutdown splash image..."
      sed -i -E 's/([a-zA-Z0-9_]+)[[:space:]]*=[[:space:]]*Image[[:space:]]*\("splash.png"\);/if (Plymouth.GetMode() == "shutdown") { \1 = Image("shutdown.png"); } else { \1 = Image("splash.png"); }/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
    fi
  fi

  # Download or copy splash and shutdown images
  if [ -f "/opt/MirrorDash/mirrordash_core/static/splash.png" ]; then
    echo "Copying splash and shutdown images from local repository..."
    cp "/opt/MirrorDash/mirrordash_core/static/splash.png" /usr/share/plymouth/themes/mirrordash/splash.png
    cp "/opt/MirrorDash/mirrordash_core/static/shutdown.png" /usr/share/plymouth/themes/mirrordash/shutdown.png
  else
    echo "Downloading splash and shutdown images from GitHub..."
    curl -sSLf "$GITHUB_RAW/mirrordash_core/static/splash.png" -o /tmp/splash.png
    mv /tmp/splash.png /usr/share/plymouth/themes/mirrordash/splash.png

    curl -sSLf "$GITHUB_RAW/mirrordash_core/static/shutdown.png" -o /tmp/shutdown.png
    mv /tmp/shutdown.png /usr/share/plymouth/themes/mirrordash/shutdown.png
  fi

  # Register our theme (initrd will be fully rebuilt later in step_system_cleanup)
  plymouth-set-default-theme mirrordash
}

step_time_wait_sync() {
  systemctl --root=/ enable systemd-time-wait-sync.service
}

step_wifi_captive_portal() {
  cat << 'EOF' > /usr/local/bin/mirrordash-wifi-check.sh
#!/bin/bash
INTERFACE="wlan0"
SSID="MirrorDash-Setup"
PASSWORD="mirrordash"
CACHE_FILE="/var/lib/mirrordash-wifi-scan.cache"

logger -t mirrordash-wifi "Starting network connectivity check..."
# Wait for NetworkManager to claim wlan0
nmcli device wait wlan0 timeout 10 2>/dev/null || true
nmcli dev set wlan0 managed yes 2>/dev/null || true

if nm-online -q -t 30; then
    logger -t mirrordash-wifi "Network online. Exiting captive portal check."
    exit 0
fi

logger -t mirrordash-wifi "No network connectivity detected after 30 seconds. Scanning before entering AP mode..."

# Force a physical radio hardware scan to populate the cache
nmcli dev wifi rescan 2>/dev/null || true

# Scan for nearby networks BEFORE entering AP mode (client-mode scanning only)
SCAN_RESULT=$(nmcli -t -f SSID dev wifi list 2>/dev/null | sort -u | grep -v '^$' || true)
echo "$SCAN_RESULT" > "$CACHE_FILE"
chmod 644 "$CACHE_FILE"
logger -t mirrordash-wifi "Cached $(echo "$SCAN_RESULT" | grep -c . || echo 0) visible networks for captive portal."

# Purge any existing MirrorDash-Setup profiles
nmcli connection delete "$SSID" 2>/dev/null || true

# Add and configure the AP hotspot
nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode ap
nmcli connection modify "$SSID" wifi-sec.key-mgmt wpa-psk
nmcli connection modify "$SSID" wifi-sec.psk "$PASSWORD"
nmcli connection modify "$SSID" wifi-sec.pmf 1
nmcli connection modify "$SSID" ipv4.method shared

if nmcli connection up "$SSID"; then
    logger -t mirrordash-wifi "Hotspot '$SSID' started successfully."
else
    logger -t mirrordash-wifi "Failed to start hotspot."
fi
EOF
  chmod +x /usr/local/bin/mirrordash-wifi-check.sh

  cat << 'EOF' > /etc/systemd/system/mirrordash-wifi-fallback.service
[Unit]
Description=MirrorDash WiFi Fallback Captive Portal Monitor
After=NetworkManager.service
Before=mirrordash.service

[Service]
Type=simple
ExecStart=/usr/local/bin/mirrordash-wifi-check.sh
RemainAfterExit=yes
TimeoutStartSec=90

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash-wifi-fallback.service
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
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl --root=/ enable mirrordash.service
}

step_finalize_script() {
  if [ -f "/opt/MirrorDash/scripts/finalize_appliance.sh" ]; then
    echo "Copying finalize_appliance.sh from local repository..."
    cp "/opt/MirrorDash/scripts/finalize_appliance.sh" /usr/local/bin/mirrordash-finalize.sh
  else
    echo "Downloading finalize_appliance.sh from GitHub..."
    curl -sSLf "$GITHUB_RAW/scripts/finalize_appliance.sh" -o /usr/local/bin/mirrordash-finalize.sh
  fi
  chmod +x /usr/local/bin/mirrordash-finalize.sh
}

step_repart_service() {
  echo "Creating MBR partition expander script..."
  cat << 'EOF' > /usr/local/bin/mirrordash-repart.sh
#!/bin/bash
# MirrorDash MBR Partition Expander
set -euo pipefail

ROOT_DEV_PARAM=$(cat /proc/cmdline | grep -o 'root=[^ ]*' | cut -d= -f2-)
ROOT_PART=$(findfs "$ROOT_DEV_PARAM" 2>/dev/null || findmnt -n -o SOURCE /)
PARENT_NAME=$(lsblk -no pkname "$ROOT_PART" 2>/dev/null | tr -d '[:space:]')
if [ -n "$PARENT_NAME" ]; then
    DISK="/dev/$PARENT_NAME"
else
    if [[ "$ROOT_PART" =~ p[0-9]+$ ]]; then
        DISK="${ROOT_PART%p[0-9]*}"
    else
        DISK="${ROOT_PART%[0-9]*}"
    fi
fi

PART_NUM=3
if [[ "$DISK" == *nvme* || "$DISK" == *mmcblk* ]]; then
    TARGET_PART="${DISK}p${PART_NUM}"
else
    TARGET_PART="${DISK}${PART_NUM}"
fi

if [ ! -b "$TARGET_PART" ]; then
    echo "Persistent partition $TARGET_PART not found. Creating..."
    
    # Get the end sector of partition 2
    END_SECTOR=$(parted -s "$DISK" unit s print | awk '/^[[:space:]]*2/ {print $3}' | tr -d 's')
    if [ -z "$END_SECTOR" ]; then
        echo "Error: Could not determine end sector of partition 2" >&2
        exit 1
    fi
    
    START_SECTOR=$((END_SECTOR + 1))
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

# Only hydrate if venv_a is missing
if [ ! -d "/storage/mirrordash/venv_a" ]; then
    echo "Hydrating /storage with golden base_venv..."
    rm -rf /storage/mirrordash/venv_a.tmp
    cp -a /home/pi/mirrordash/base_venv /storage/mirrordash/venv_a.tmp
    mv /storage/mirrordash/venv_a.tmp /storage/mirrordash/venv_a
    chown -R pi:pi /storage/mirrordash/venv_a
fi

# Clean up active venv if it is a real directory instead of a symlink
if [ -e /storage/mirrordash/venv ] && [ ! -L /storage/mirrordash/venv ]; then
    rm -rf /storage/mirrordash/venv
fi

# Always ensure the active venv symlink is correct (extremely fast, no filesystem traversal)
ln -sfT venv_a /storage/mirrordash/venv
chown pi:pi /storage/mirrordash /storage/mirrordash/venv
EOF
  chmod +x /usr/local/bin/mirrordash-hydrate.sh

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

step_system_cleanup() {
  echo "Restoring update-initramfs diversion..."
  rm -f /usr/sbin/update-initramfs || true
  dpkg-divert --local --rename --remove /usr/sbin/update-initramfs || true

  echo "Rebuilding initramfs to embed plymouth and kernel updates..."
  sed -i 's/^MODULES=.*/MODULES=most/' /etc/initramfs-tools/initramfs.conf
  
  # Temporarily mask the root filesystem in fstab so mkinitramfs doesn't crash trying to probe it
  cp /etc/fstab /etc/fstab.bak
  trap 'mv -f /etc/fstab.bak /etc/fstab 2>/dev/null || true' EXIT ERR INT TERM
  sed -i '/ \/ /s/^/#/' /etc/fstab
  update-initramfs -u || echo "Warning: update-initramfs exited with a non-zero status"
  mv /etc/fstab.bak /etc/fstab
  trap - EXIT ERR INT TERM

  echo "Patching RPi OS firstboot to skip partition expansion (preserving SSH/PARTUUID regen)..."
  if [ -f /usr/lib/raspberrypi-sys-mods/firstboot ]; then
    if ! grep -q "do_resize() { return 0; }" /usr/lib/raspberrypi-sys-mods/firstboot; then
      sed -i '2i do_resize() { return 0; }' /usr/lib/raspberrypi-sys-mods/firstboot
    fi
  fi

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
run_step "9" "watchdog_boot_optimization" "Enabling Watchdog & Optimizing Boot" step_watchdog_boot_optimization
run_step "10" "plymouth_splash" "Configuring Plymouth Splash Screen" step_plymouth_splash
run_step "11" "time_wait_sync" "Enabling systemd-time-wait-sync" step_time_wait_sync
run_step "12" "wifi_captive_portal" "Creating WiFi Fallback Captive Portal Script & Service" step_wifi_captive_portal
run_step "13" "systemd_service" "Creating MirrorDash Background Service" step_systemd_service
run_step "14" "finalize_script" "Creating Appliance Finalization Script" step_finalize_script
run_step "15" "repart_service" "Creating MBR Repart Service" step_repart_service
run_step "16" "storage_hydration" "Creating First-Boot Storage Hydration Service" step_storage_hydration
run_step "17" "system_cleanup" "Performing System Cleanup" step_system_cleanup

END_TIME_TOTAL=$SECONDS
DURATION_TOTAL=$((END_TIME_TOTAL - START_TIME_TOTAL))
minutes=$((DURATION_TOTAL / 60))
seconds=$((DURATION_TOTAL % 60))

echo "=========================================================="
echo " MirrorDash setup successfully completed in ${minutes}m ${seconds}s!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="

