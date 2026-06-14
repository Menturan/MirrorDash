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

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash setup_appliance.sh)"
  exit 1
fi

# Ensure target disk has enough space (at least 8GB to fit bootfs + rootfs + storage)
ROOT_PART=$(findmnt -n -o SOURCE /)
# Try to get the parent disk using lsblk, with a robust sed fallback
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
  "$@"
  mark_step_completed "$step_name"
  echo ">>> Step $step_num completed."
  echo ""
}

# --- Step Functions ---

step_expanding_partition() {
  # Determine partition suffix and build target block device paths
  if [[ "$ROOT_DISK" =~ [0-9]$ ]]; then
    PART_SUFFIX="p"
  else
    PART_SUFFIX=""
  fi
  DATA_PART="${ROOT_DISK}${PART_SUFFIX}3"
  ROOT_PART_NAME="${ROOT_DISK}${PART_SUFFIX}2"

  # Expand root partition (p2) to 6GB and resize filesystem if p3/data partition doesn't exist
  if [ ! -b "$DATA_PART" ]; then
    printf "Yes\nIgnore\n" | parted "$ROOT_DISK" ---pretend-input-tty resizepart 2 6GB
    
    # Force kernel to sync partition table changes before resizing filesystem
    partprobe "$ROOT_DISK" || true
    udevadm settle || true
    sleep 2
    
    resize2fs "$ROOT_PART_NAME"

    # Create data partition (p3), format it
    printf "Ignore\n" | parted "$ROOT_DISK" ---pretend-input-tty mkpart primary ext4 6GB 100%
    mkfs.ext4 -F -L mirrordash-data "$DATA_PART"
  fi

  # Mount the new partition first, then create directory structure on it
  mkdir -p /storage
  if ! mountpoint -q /storage; then
    mount "$DATA_PART" /storage
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

  # Create the storage auto-expand script (runs on next boots)
  cat << 'EOF' > /usr/local/bin/mirrordash-expand.sh
#!/bin/bash
# MirrorDash storage partition auto-expand script
# Runs early on boot to expand partition 3 to fill the rest of the disk.

set -e

# Find the root partition and parent disk dynamically
ROOT_PART=$(findmnt -n -o SOURCE /)
PARENT_NAME=$(lsblk -no pkname "$ROOT_PART" 2>/dev/null | tr -d '[:space:]')
if [ -n "$PARENT_NAME" ]; then
  ROOT_DISK="/dev/$PARENT_NAME"
else
  if [[ "$ROOT_PART" =~ p[0-9]+$ ]]; then
    ROOT_DISK=$(echo "$ROOT_PART" | sed 's/p[0-9]\+$//')
  else
    ROOT_DISK=$(echo "$ROOT_PART" | sed 's/[0-9]\+$//')
  fi
fi

if [ ! -b "$ROOT_DISK" ]; then
  echo "Error: Could not resolve root disk." >&2
  exit 1
fi

if [[ "$ROOT_DISK" =~ [0-9]$ ]]; then
  PART_SUFFIX="p"
else
  PART_SUFFIX=""
fi
DATA_PART="${ROOT_DISK}${PART_SUFFIX}3"

if [ ! -b "$DATA_PART" ]; then
  echo "Error: Persistent partition $DATA_PART does not exist." >&2
  exit 1
fi

echo "Expanding storage partition 3 on $ROOT_DISK to 100%..."
# Run parted to resize partition 3. Pipe inputs to handle interactive warnings.
printf "Yes\nIgnore\n" | parted "$ROOT_DISK" ---pretend-input-tty resizepart 3 100%

# Force kernel to recognize partition table changes
partprobe "$ROOT_DISK" || true
udevadm settle || true

echo "Resizing ext4 filesystem on $DATA_PART..."
# Perform online resize of ext4 filesystem
resize2fs "$DATA_PART"

echo "Storage partition expansion complete."
EOF
  chmod +x /usr/local/bin/mirrordash-expand.sh

  # Create the systemd service file
  cat << 'EOF' > /etc/systemd/system/mirrordash-expand.service
[Unit]
Description=MirrorDash Auto-Expand Storage Partition
After=local-fs.target
Before=mirrordash.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mirrordash-expand.sh
RemainAfterExit=yes
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF

  # Enable the service
  systemctl enable mirrordash-expand.service
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
      parted \
      python3
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

  # Silence the tty1 autologin prompt and login banners
  if [ -f /etc/systemd/system/getty@tty1.service.d/autologin.conf ]; then
    if ! grep -q "\-\-noissue" /etc/systemd/system/getty@tty1.service.d/autologin.conf; then
      echo "Silencing tty1 getty autologin console messages..."
      sed -i 's/--autologin/--noissue --skip-login --autologin/g' /etc/systemd/system/getty@tty1.service.d/autologin.conf
    fi
  fi

  # Create hushlogin file to silence shell login banners/MOTD
  touch "$PI_HOME/.hushlogin"
  chown "$PI_USER:$PI_USER" "$PI_HOME/.hushlogin"
}

step_setting_up_wayland() {
  # Launch labwc on tty1 login
  # Overwrite completely to ensure idempotency and clear terminal text (agetty)
  cat << 'EOF' > "$PI_HOME/.bash_profile"
# --- MirrorDash Wayland Autostart ---
if [[ -z $WAYLAND_DISPLAY && $XDG_VTNR -eq 1 ]]; then
  printf "\033c"
  exec labwc
fi
EOF
  chown "$PI_USER:$PI_USER" "$PI_HOME/.bash_profile"

  # Create a custom invisible cursor theme for the kiosk user to hide the mouse cursor safely
  echo "Setting up local transparent cursor theme (invisible)..."
  mkdir -p "$PI_HOME/.local/share/icons/invisible/cursors"
  
  # Create index.theme so applications recognize it as a valid theme
  cat << 'EOF' > "$PI_HOME/.local/share/icons/invisible/index.theme"
[Icon Theme]
Name=invisible
Comment=Invisible cursor theme
EOF

  # Write a valid, 32x32 transparent XCursor file
  python3 -c "import struct; data = struct.pack('<4sIII', b'Xcur', 16, 0x00010000, 1) + struct.pack('<III', 0xfffd0002, 32, 28) + struct.pack('<IIIIIIIII', 36, 0xfffd0002, 32, 1, 32, 32, 0, 0, 0) + b'\x00'*(32*32*4); open('$PI_HOME/.local/share/icons/invisible/cursors/default', 'wb').write(data)"
  
  # Create standard cursor symlinks pointing to the transparent default cursor
  ln -sf default "$PI_HOME/.local/share/icons/invisible/cursors/left_ptr"
  ln -sf default "$PI_HOME/.local/share/icons/invisible/cursors/pointer"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.local"

  # Configure labwc to use the invisible cursor theme
  mkdir -p "$PI_HOME/.config/labwc"
  echo "XCURSOR_THEME=invisible" > "$PI_HOME/.config/labwc/environment"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config"

  # Create labwc autostart
  cat << 'EOF' > "$PI_HOME/.config/labwc/autostart"
# --- MirrorDash Kiosk Autostart ---

# Hide mouse cursor natively at Wayland startup
labwc-msg HideCursor 2>/dev/null || true

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
  # Opens the local loading.html instantly and checks for FastAPI server status
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
      file:///home/pi/mirrordash/loading.html
      
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
  uv venv --allow-existing --python 3.14 /storage/mirrordash/venv_a

  echo 'Creating golden backup virtual environment base_venv...'
  uv venv --allow-existing --python 3.14 "$HOME/mirrordash/base_venv"

  echo 'Installing MirrorDash from PyPI into primary venv...'
  uv pip install --python /storage/mirrordash/venv_a mirrordash

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
    for opt in "loglevel=0" "quiet" "splash" "systemd.show_status=false" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles" "logo.nologo"; do
      if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
        sed -i "s/$/ $opt/" /boot/firmware/cmdline.txt
      fi
    done
  fi
}

step_plymouth_splash() {
  # Ensure the theme directory exists (especially on fresh Lite images)
  mkdir -p /usr/share/plymouth/themes/pix

  # Silence the Plymouth theme status messages and patch for separate shutdown splash
  if [ -f /usr/share/plymouth/themes/pix/pix.script ]; then
    local patched=false
    if grep -q "^[[:space:]]*Plymouth\.SetMessageFunction" /usr/share/plymouth/themes/pix/pix.script || \
       grep -q "^[[:space:]]*Plymouth\.SetUpdateStatusFunction" /usr/share/plymouth/themes/pix/pix.script; then
      echo "Silencing Plymouth message callbacks in pix.script..."
      sed -i 's/^[[:space:]]*Plymouth\.SetMessageFunction/# Plymouth.SetMessageFunction/g' /usr/share/plymouth/themes/pix/pix.script
      sed -i 's/^[[:space:]]*Plymouth\.SetUpdateStatusFunction/# Plymouth.SetUpdateStatusFunction/g' /usr/share/plymouth/themes/pix/pix.script
      patched=true
    fi
    if ! grep -q 'Plymouth\.GetMode() == "shutdown"' /usr/share/plymouth/themes/pix/pix.script; then
      echo "Patching pix.script to support separate shutdown splash image..."
      sed -i -E 's/([a-zA-Z0-9_]+)[[:space:]]*=[[:space:]]*Image[[:space:]]*\("splash.png"\);/if (Plymouth.GetMode() == "shutdown") { \1 = Image("shutdown.png"); } else { \1 = Image("splash.png"); }/g' /usr/share/plymouth/themes/pix/pix.script
      patched=true
    fi
    if [ "$patched" = true ]; then
      # Force rebuild by removing sentinel
      rm -f /usr/share/plymouth/themes/pix/.mirrordash_configured
    fi
  fi

  # Skip rebuilding initramfs if our custom theme is already configured (saves significant time)
  if [ -f /usr/share/plymouth/themes/pix/.mirrordash_configured ] && [ "$(plymouth-set-default-theme)" = "pix" ]; then
    echo "Plymouth splash screen is already configured. Skipping rebuild."
    return 0
  fi

  # Download or copy splash and shutdown images
  if [ -f "/opt/MirrorDash/mirrordash_core/static/splash.png" ]; then
    echo "Copying splash and shutdown images from local repository..."
    cp "/opt/MirrorDash/mirrordash_core/static/splash.png" /usr/share/plymouth/themes/pix/splash.png
    cp "/opt/MirrorDash/mirrordash_core/static/shutdown.png" /usr/share/plymouth/themes/pix/shutdown.png
  else
    echo "Downloading splash and shutdown images from GitHub..."
    # Use atomic temp file downloads to prevent corrupt empty files on network failure
    curl -sSLf "$GITHUB_RAW/mirrordash_core/static/splash.png" -o /tmp/splash.png
    mv /tmp/splash.png /usr/share/plymouth/themes/pix/splash.png

    curl -sSLf "$GITHUB_RAW/mirrordash_core/static/shutdown.png" -o /tmp/shutdown.png
    mv /tmp/shutdown.png /usr/share/plymouth/themes/pix/shutdown.png
  fi

  # On Trixie, --rebuild-initrd is required for splash changes to take effect on boot
  plymouth-set-default-theme --rebuild-initrd pix

  # Mark as configured
  touch /usr/share/plymouth/themes/pix/.mirrordash_configured
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
# Purge any existing MirrorDash-Setup profiles
nmcli connection delete "$SSID" 2>/dev/null || true

# Add and configure a custom hotspot profile with PMF disabled to avoid Broadcom firmware bugs
nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode ap
nmcli connection modify "$SSID" wifi-sec.key-mgmt wpa-psk
nmcli connection modify "$SSID" wifi-sec.psk "$PASSWORD"
nmcli connection modify "$SSID" wifi-sec.pmf 1
nmcli connection modify "$SSID" ipv4.method shared

# Attempt to bring the connection up
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
After=network.target
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

step_system_cleanup() {
  echo "Pruning package manager caches..."
  apt-get clean
  apt-get autoremove -y

  echo "Pruning temporary directories and system caches..."
  rm -rf /tmp/* /var/tmp/*
  rm -rf /root/.cache /home/pi/.cache

  echo "Truncating system logs and journals..."
  find /var/log -type f -exec truncate -s 0 {} \;
  journalctl --vacuum-time=1s 2>/dev/null || true

  echo "Clearing bash and execution histories..."
  rm -f /root/.bash_history /home/pi/.bash_history
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
run_step "14" "finalize_script" "Creating Appliance Finalization Script" step_finalize_script
run_step "15" "system_cleanup" "Performing System Cleanup" step_system_cleanup

echo "=========================================================="
echo " MirrorDash setup successfully completed!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="
