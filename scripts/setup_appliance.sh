#!/bin/bash
# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
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

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash setup_appliance.sh)"
  exit 1
fi

# Ensure target disk has enough space (at least 8GB to fit bootfs + rootfs + storage)
ROOT_PART=$(findmnt -n -o SOURCE /)
ROOT_DISK=$(echo "$ROOT_PART" | sed 's/p[0-9]\+$//; s/[0-9]\+$//')
if [ -b "$ROOT_DISK" ]; then
  disk_size_bytes=$(blockdev --getsize64 "$ROOT_DISK")
  if [ "$disk_size_bytes" -lt $((7 * 1024 * 1024 * 1024 + 500 * 1024 * 1024)) ]; then
    echo "Error: MirrorDash requires a system drive of at least 8GB (detected $(($disk_size_bytes / 1024 / 1024 / 1024))GB)." >&2
    exit 1
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
  "$@"
  mark_step_completed "$step_name"
  echo ">>> Step $step_num completed."
  echo ""
}

# --- Step Functions ---

step_expanding_partition() {
  # Expand root partition (p2) to 6GB and resize filesystem
  if [ ! -b /dev/mmcblk0p3 ]; then
    printf "Yes\nIgnore\n" | parted /dev/mmcblk0 ---pretend-input-tty resizepart 2 6GB
    resize2fs /dev/mmcblk0p2

    # Create p3, format it
    printf "Ignore\n" | parted /dev/mmcblk0 ---pretend-input-tty mkpart primary ext4 6GB 100%
    mkfs.ext4 -F -L mirrordash-data /dev/mmcblk0p3
  fi

  # Mount the new partition first, then create directory structure on it
  mkdir -p /storage
  if ! mountpoint -q /storage; then
    mount /dev/mmcblk0p3 /storage
  fi

  # Now create directories on the actual persistent partition
  mkdir -p /storage/mirrordash/data /storage/mirrordash/venv_a /storage/mirrordash/venv_b
  chown -R "$PI_USER:$PI_USER" /storage

  # Create application data directories
  mkdir -p "$PI_HOME/.mirrordash/cache" "$PI_HOME/.mirrordash/data"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.mirrordash"

  # Update fstab
  if ! grep -q "LABEL=mirrordash-data" /etc/fstab; then
    cat << 'EOF' >> /etc/fstab

# --- MirrorDash Storage ---
# Persistent data partition (survives OverlayFS)
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60,nofail,x-systemd.device-timeout=5  0  2

# Bind-mount persistent data into the application's expected path
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind,nofail,x-systemd.device-timeout=5  0  0

# Volatile module cache in RAM (100 MB)
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF
  fi

  # Mount all fstab entries (bind mounts, tmpfs)
  mount -a
}

step_installing_packages() {
  apt update
  apt full-upgrade -y
  apt install -y --no-install-recommends \
      labwc \
      chromium \
      wlr-randr \
      avahi-daemon \
      nginx \
      plymouth \
      plymouth-themes \
      pix-plym-splash \
      parted
  apt autoclean -y
  apt autoremove -y
}

step_setting_hostname() {
  hostnamectl set-hostname mirrordash
  sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts
  systemctl enable avahi-daemon
  systemctl start avahi-daemon
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
  systemctl restart nginx
}

step_configuring_console_login() {
  raspi-config nonint do_boot_behaviour B2
}

step_setting_up_wayland() {
  # Launch labwc on tty1 login
  if ! grep -q "exec labwc" "$PI_HOME/.bash_profile" 2>/dev/null; then
    echo '[[ -z $WAYLAND_DISPLAY && $XDG_VTNR -eq 1 ]] && exec labwc' >> "$PI_HOME/.bash_profile"
  fi
  chown "$PI_USER:$PI_USER" "$PI_HOME/.bash_profile"

  # Create labwc autostart
  mkdir -p "$PI_HOME/.config/labwc"
  cat << 'EOF' > "$PI_HOME/.config/labwc/autostart"
# --- MirrorDash Kiosk Autostart ---

# Prevent Chromium "didn't shut down correctly" restore prompt after power loss
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' /home/pi/.config/chromium/'Local State' 2>/dev/null
sed -i 's/"exit_type":"[^"]\+"/\"exit_type\":\"Normal\"/' /home/pi/.config/chromium/Default/Preferences 2>/dev/null

# Kiosk Browser Crash Supervisor Loop
CRASH_COUNTER=0
MAX_CRASHES=5
THRESHOLD_SECS=10

while true; do
  START_TIME=$(date +%s)
  
  # Launch Chromium in kiosk mode with native Wayland rendering and touch/gesture hardening
  chromium \
      --kiosk \
      --ozone-platform=wayland \
      --noerrdialogs \
      --disable-infobars \
      --no-first-run \
      --disable-session-crashed-bubble \
      --disable-features=TranslateUI \
      --enable-features=OverlayScrollbar \
      --disable-pinch \
      --overscroll-history-navigation=0 \
      --disable-dev-tools \
      http://localhost:8000
      
  EXIT_CODE=$?
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  
  if [ "$DURATION" -lt "$THRESHOLD_SECS" ]; then
    CRASH_COUNTER=$((CRASH_COUNTER + 1))
    echo "Chromium crashed in $DURATION seconds. (Crash: $CRASH_COUNTER/$MAX_CRASHES)" >&2
  else
    CRASH_COUNTER=0
  fi
  
  if [ "$CRASH_COUNTER" -ge "$MAX_CRASHES" ]; then
    echo "Chromium crash loop detected! Launching diagnostic fallback..." >&2
    
    cat << 'ERR_EOF' > /tmp/kiosk_error.html
<!DOCTYPE html>
<html>
<head>
    <style>
        body { background: #000; color: #fff; font-family: sans-serif; text-align: center; padding-top: 20%; }
        h1 { color: #ff3333; font-size: 2.5rem; }
        p { color: #999; font-size: 1.2rem; }
    </style>
</head>
<body>
    <h1>Kiosk Display Error</h1>
    <p>The kiosk browser crashed repeatedly. Please restart the device or contact support.</p>
</body>
</html>
ERR_EOF
    
    chromium --kiosk --ozone-platform=wayland file:///tmp/kiosk_error.html
    while true; do sleep 3600; done
  fi
  
  SLEEP_TIME=$((CRASH_COUNTER * 2))
  [ "$SLEEP_TIME" -eq 0 ] && SLEEP_TIME=1
  sleep "$SLEEP_TIME"
done &
EOF
  chmod +x "$PI_HOME/.config/labwc/autostart"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config"
}

step_installing_app() {
  # Install uv as the pi user (standalone binary — does not require git or Python)
  sudo -u "$PI_USER" HOME="$PI_HOME" bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

  # Create app directory
  sudo -u "$PI_USER" HOME="$PI_HOME" mkdir -p "$PI_HOME/mirrordash"

  # Setup symlink structures (A/B venv layout)
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT venv_a /storage/mirrordash/venv
  sudo -u "$PI_USER" HOME="$PI_HOME" ln -sfT /storage/mirrordash/venv "$PI_HOME/mirrordash/.venv"

  # Create base_venv (Golden Copy) and active venv_a, then install from PyPI
  sudo -u "$PI_USER" HOME="$PI_HOME" PATH="$PI_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" bash -e << 'EOF'
  cd "$HOME/mirrordash"

  echo 'Creating primary virtual environment in venv_a...'
  uv venv --python 3.14 /storage/mirrordash/venv_a

  echo 'Creating golden backup virtual environment base_venv...'
  uv venv --python 3.14 "$HOME/mirrordash/base_venv"

  echo 'Installing MirrorDash from PyPI into primary venv...'
  uv pip install --python /storage/mirrordash/venv_a mirrordash

  echo 'Installing MirrorDash from PyPI into golden venv...'
  uv pip install --python "$HOME/mirrordash/base_venv" mirrordash
EOF

  # Download launch.sh directly from GitHub
  curl -sSL "$GITHUB_RAW/scripts/launch.sh" \
      -o "$PI_HOME/mirrordash/launch.sh"
  chmod +x "$PI_HOME/mirrordash/launch.sh"
  chown "$PI_USER:$PI_USER" "$PI_HOME/mirrordash/launch.sh"
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
  systemctl daemon-reexec

  # Suppress splash, boot delay, Bluetooth, allocate gpu memory in config.txt
  if ! grep -q "disable_splash=1" /boot/firmware/config.txt; then
    cat << 'EOF' >> /boot/firmware/config.txt

# --- MirrorDash Hardware Hardening ---
disable_splash=1
boot_delay=0
gpu_mem=128
dtoverlay=disable-bt
EOF
  fi

  # Silence kernel logs in cmdline.txt: replace console=tty1 with console=tty3 and append quiet/splash options
  if [ -f /boot/firmware/cmdline.txt ]; then
    # Replace console=tty1 with console=tty3 if present
    sed -i 's/console=tty1/console=tty3/g' /boot/firmware/cmdline.txt
    # Ensure options are present
    for opt in "loglevel=3" "quiet" "splash" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles"; do
      if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
        sed -i "s/$/ $opt/" /boot/firmware/cmdline.txt
      fi
    done
  fi
}

step_plymouth_splash() {
  # Ensure the theme directory exists (especially on fresh Lite images)
  mkdir -p /usr/share/plymouth/themes/pix

  # Skip rebuilding initramfs if the theme is already configured (saves significant time)
  if [ -f /usr/share/plymouth/themes/pix/splash.png ] && [ "$(plymouth-set-default-theme)" = "pix" ]; then
    echo "Plymouth splash screen is already configured. Skipping rebuild."
    return 0
  fi

  # Download splash asset directly from GitHub
  curl -sSL "$GITHUB_RAW/mirrordash_core/static/splash.png" \
      | tee /usr/share/plymouth/themes/pix/splash.png > /dev/null
  # On Trixie, --rebuild-initrd is required for splash changes to take effect on boot
  plymouth-set-default-theme --rebuild-initrd pix
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

logger -t mirrordash-wifi "Starting network connectivity check..."
for i in {1..30}; do
    # Check if the system has any default gateway (Ethernet or configured Wi-Fi)
    if ip route show | grep -q "^default"; then
        logger -t mirrordash-wifi "Network online (default gateway detected). Exiting."
        exit 0
    fi
    sleep 1
done

logger -t mirrordash-wifi "No network connectivity detected after 30 seconds. Switching to setup hotspot..."
nmcli device disconnect "$INTERFACE" 2>/dev/null || true
nmcli device wifi hotspot ifname "$INTERFACE" ssid "$SSID" password "$PASSWORD"
if [ $? -eq 0 ]; then
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
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-wifi-check.sh
RemainAfterExit=yes
TimeoutStartSec=45

[Install]
WantedBy=multi-user.target
EOF
  systemctl enable mirrordash-wifi-fallback.service
}

step_systemd_service() {
  cat << 'EOF' > /etc/systemd/system/mirrordash.service
[Unit]
Description=MirrorDash Core App Backend
After=network-online.target systemd-time-wait-sync.service
Wants=network-online.target systemd-time-wait-sync.service
RequiresMountsFor=/home/pi/.mirrordash/data

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mirrordash
Environment="PATH=/home/pi/mirrordash/.venv/bin:/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="VIRTUAL_ENV=/home/pi/mirrordash/.venv"
Environment="WAYLAND_DISPLAY=wayland-1"
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

# --- Execution ---

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

echo "=========================================================="
echo " MirrorDash setup successfully completed!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="
