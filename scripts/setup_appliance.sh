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
  ROOT_PART=$(findmnt -n -o SOURCE /)
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
  echo "Configuring systemd-repart for declarative automatic partition expansion..."
  mkdir -p /etc/repart.d

  cat << 'EOF' > /etc/repart.d/50-data.conf
[Partition]
Type=linux-generic
Label=mirrordash-data
Format=ext4
Weight=100
EOF

  mkdir -p /storage
  if ! grep -q "LABEL=mirrordash-data" /etc/fstab; then
    cat << 'EOF' >> /etc/fstab

# --- MirrorDash Persistent Storage ---
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,x-systemd.growfs,x-systemd.device-timeout=15s  0  2
EOF
  fi

  echo "Configuring systemd-tmpfiles to ensure persistent directories exist..."
  # NOTE: /storage/mirrordash/venv is intentionally NOT listed here.
  # It must be a symlink (created by mirrordash-hydrate.sh), not a directory.
  # A tmpfiles 'd' entry would create a real directory that ln -sfT cannot replace,
  # causing a cascading first-boot failure (hydration → storage-init → mirrordash).
  cat << 'EOF' > /etc/tmpfiles.d/mirrordash-storage.conf
d /storage/mirrordash/data 0755 pi pi - -
d /storage/mirrordash/system-connections 0700 root root - -
EOF

  # Länka NetworkManager till den nya partitionen (Wi-Fi överlever OverlayFS)
  rm -rf /etc/NetworkManager/system-connections
  ln -s /storage/mirrordash/system-connections /etc/NetworkManager/system-connections
}

step_installing_packages() {
  echo "Diverting update-initramfs to prevent cloud-build kernel panics..."
  dpkg-divert --local --rename --add /usr/sbin/update-initramfs
  ln -sf /bin/true /usr/sbin/update-initramfs

  apt-get update
  apt-get install -y --no-install-recommends \
      labwc \
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
      nginx
}

step_setting_hostname() {
  echo "mirrordash" > /etc/hostname
  sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts
  systemctl enable avahi-daemon

  echo "Unblocking Wi-Fi radio permanently..."
  raspi-config nonint do_wifi_country US 2>/dev/null || true
  rfkill unblock wifi 2>/dev/null || true
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
  systemctl enable nginx
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
After=systemd-user-sessions.service plymouth-quit-wait.service
Conflicts=getty@tty1.service
Wants=cog-kiosk.service

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
WantedBy=graphical.target
EOF
  systemctl enable labwc-kiosk.service

  # Ta bort högerklick/terminal-åtkomst på skärmen
  mkdir -p "$PI_HOME/.config/labwc"
  cat << 'EOF' > "$PI_HOME/.config/labwc/rc.xml"
<?xml version="1.0"?><labwc_config><mouse><context name="Root"><mousebind button="Right" action="Press"><action name="None" /></mousebind></context></mouse></labwc_config>
EOF

  cat << 'EOF' > "$PI_HOME/.config/labwc/autostart"
labwc-msg HideCursor 2>/dev/null || true
EOF
  chmod +x "$PI_HOME/.config/labwc/autostart"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config"

  echo "Configuring cog kiosk systemd service..."
  cat << 'EOF' > /etc/systemd/system/cog-kiosk.service
[Unit]
Description=Cog WebKit Kiosk
After=labwc-kiosk.service
BindsTo=labwc-kiosk.service

[Service]
User=pi
Environment="WAYLAND_DISPLAY=wayland-0"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="COG_PLATFORM_WL_VIEW_FULLSCREEN=1"
ExecStart=/usr/bin/cog -P wl --bg-color=black file:///home/pi/mirrordash/loading.html
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF
  systemctl enable cog-kiosk.service
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
  uv venv --allow-existing --python 3.14 "$HOME/mirrordash/base_venv"

  echo 'Installing MirrorDash from PyPI into golden venv...'
  uv pip install --python "$HOME/mirrordash/base_venv" mirrordash
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

  # Silence kernel logs in cmdline.txt: replace console=tty1 with console=tty3 and append quiet/splash options
  if [ -f /boot/firmware/cmdline.txt ]; then
    # Replace console=tty1 with console=tty3 if present
    sed -i 's/console=tty1/console=tty3/g' /boot/firmware/cmdline.txt
    # Ensure options are present
    for opt in "loglevel=0" "quiet" "splash" "systemd.show_status=false" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles" "logo.nologo"; do
      if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
        sed -i "1s/$/ $opt/" /boot/firmware/cmdline.txt
      fi
    done
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
  systemctl enable systemd-time-wait-sync.service
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
  systemctl enable mirrordash-wifi-fallback.service
}

step_systemd_service() {
  cat << 'EOF' > /etc/systemd/system/mirrordash.service
[Unit]
Description=MirrorDash Core App Backend
After=network.target mirrordash-storage-init.service
Requires=mirrordash-storage-init.service
RequiresMountsFor=/storage/mirrordash/data

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
  systemctl enable mirrordash.service
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

step_storage_hydration() {
  cat << 'EOF' > /usr/local/bin/mirrordash-hydrate.sh
#!/bin/bash
set -euo pipefail

# Only hydrate if venv_a is missing
if [ ! -d "/storage/mirrordash/venv_a" ]; then
    echo "Hydrating /storage with golden base_venv..."
    cp -a /home/pi/mirrordash/base_venv /storage/mirrordash/venv_a.tmp
    mv /storage/mirrordash/venv_a.tmp /storage/mirrordash/venv_a
    ln -sfT venv_a /storage/mirrordash/venv
    chown -R pi:pi /storage/mirrordash
fi
EOF
  chmod +x /usr/local/bin/mirrordash-hydrate.sh

  cat << 'EOF' > /etc/systemd/system/mirrordash-storage-init.service
[Unit]
Description=Hydrate MirrorDash Storage Partition
After=local-fs.target
Requires=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-hydrate.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl enable mirrordash-storage-init.service
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
    sed -i '2i do_resize() { return 0; }' /usr/lib/raspberrypi-sys-mods/firstboot
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
run_step "15" "storage_hydration" "Creating First-Boot Storage Hydration Service" step_storage_hydration
run_step "16" "system_cleanup" "Performing System Cleanup" step_system_cleanup

END_TIME_TOTAL=$SECONDS
DURATION_TOTAL=$((END_TIME_TOTAL - START_TIME_TOTAL))
minutes=$((DURATION_TOTAL / 60))
seconds=$((DURATION_TOTAL % 60))

echo "=========================================================="
echo " MirrorDash setup successfully completed in ${minutes}m ${seconds}s!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="
