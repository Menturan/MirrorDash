#!/bin/bash
# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# MirrorDash Automatic Appliance Setup Script
# Configure a fresh Debian Trixie (Raspberry Pi OS) installation into a production MirrorDash kiosk.

set -e

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash setup_appliance.sh)"
  exit 1
fi

PI_USER="pi"
PI_HOME="/home/$PI_USER"

echo "=== 1. Updating and Installing APT Packages ==="
apt update
apt install -y --no-install-recommends \
    labwc \
    chromium-browser \
    wlr-randr \
    git \
    plymouth \
    plymouth-themes \
    parted
apt autoclean -y
apt autoremove -y

echo "=== 2. Configuring Console Auto-Login (B2) ==="
raspi-config nonint do_boot_behaviour B2

echo "=== 3. Setting up Wayland Auto-launch & Kiosk Config ==="
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
sed -i 's/"exit_type":"[^"]\+"/"exit_type":"Normal"/' /home/pi/.config/chromium/Default/Preferences 2>/dev/null

# Launch Chromium in kiosk mode with native Wayland rendering
chromium-browser \
    --kiosk \
    --ozone-platform=wayland \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --enable-features=OverlayScrollbar \
    http://localhost:8000 &
EOF
chmod +x "$PI_HOME/.config/labwc/autostart"
chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config"

echo "=== 4. Expanding Partition & Setting up Persistent Storage ==="
# Expand root partition (p2) to 6GB and resize filesystem
parted /dev/mmcblk0 resizepart 2 6GB || true
resize2fs /dev/mmcblk0p2 || true

# Create p3, format it
parted -s /dev/mmcblk0 mkpart primary ext4 6GB 100% || true
mkfs.ext4 -F -L mirrordash-data /dev/mmcblk0p3 || true

# Setup storage directory structures
mkdir -p /storage
mkdir -p /storage/mirrordash/data
mkdir -p /storage/mirrordash/venv_a
mkdir -p /storage/mirrordash/venv_b

# Ensure correct permissions on storage
chown -R "$PI_USER:$PI_USER" /storage

mkdir -p "$PI_HOME/.mirrordash/cache"
mkdir -p "$PI_HOME/.mirrordash/data"
chown -R "$PI_USER:$PI_USER" "$PI_HOME/.mirrordash"

# Update fstab
if ! grep -q "LABEL=mirrordash-data" /etc/fstab; then
  cat << 'EOF' >> /etc/fstab

# --- MirrorDash Storage ---
# Persistent data partition (survives OverlayFS)
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60  0  2

# Bind-mount persistent data into the application's expected path
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind  0  0

# Volatile module cache in RAM (100 MB)
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF
fi

# Try mounting new mounts
mount -a || true

echo "=== 5. Installing uv & MirrorDash App ==="
# Run uv install as the pi user
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

# Set up app directory and symlinks
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "mkdir -p \$HOME/mirrordash"

# Copy the source files of the app if they aren't already there (assuming the script is run from inside the repo)
if [ -d "mirrordash_core" ]; then
  echo "Copying MirrorDash repository files to $PI_HOME/mirrordash..."
  cp -r . "$PI_HOME/mirrordash/"
  chown -R "$PI_USER:$PI_USER" "$PI_HOME/mirrordash"
fi

# Setup symlink structures
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "ln -sfT venv_a /storage/mirrordash/venv"
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "ln -sfT /storage/mirrordash/venv \$HOME/mirrordash/.venv"

# Create base_venv (Golden Copy) and active venv_a
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "
  source \$HOME/.local/bin/env
  cd \$HOME/mirrordash
  
  echo 'Creating primary virtual environment in venv_a...'
  uv venv --python 3.14 /storage/mirrordash/venv_a
  
  echo 'Creating golden backup virtual environment base_venv...'
  uv venv --python 3.14 base_venv
"

# Install MirrorDash in both virtual environments
sudo -u "$PI_USER" -i env HOME="$PI_HOME" bash -c "
  source \$HOME/.local/bin/env
  cd \$HOME/mirrordash
  
  echo 'Installing MirrorDash into primary virtual environment...'
  .venv/bin/uv pip install -e .
  
  echo 'Installing MirrorDash into golden virtual environment...'
  base_venv/bin/uv pip install -e .
  
  if [ -d 'modules/mirrordash-clock' ]; then
    echo 'Installing mirrordash-clock module...'
    .venv/bin/uv pip install -e modules/mirrordash-clock
    base_venv/bin/uv pip install -e modules/mirrordash-clock
  fi
"

# Copy launch script and make executable
if [ -f "$PI_HOME/mirrordash/scripts/launch.sh" ]; then
  cp "$PI_HOME/mirrordash/scripts/launch.sh" "$PI_HOME/mirrordash/launch.sh"
  chmod +x "$PI_HOME/mirrordash/launch.sh"
  chown "$PI_USER:$PI_USER" "$PI_HOME/mirrordash/launch.sh"
fi

echo "=== 6. Setting up Passwordless Sudo ==="
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

echo "=== 7. Enabling Watchdog & Optimizing Boot ==="
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

# Silence kernel logs in cmdline.txt
if ! grep -q "console=tty3" /boot/firmware/cmdline.txt; then
  sed -i 's/$/ console=tty3 loglevel=3 quiet splash/' /boot/firmware/cmdline.txt
fi

echo "=== 8. Configuring Plymouth Splash Screen ==="
if [ -f "static/splash.png" ]; then
  cp static/splash.png /usr/share/plymouth/themes/pix/splash.png
elif [ -f "$PI_HOME/mirrordash/static/splash.png" ]; then
  cp "$PI_HOME/mirrordash/static/splash.png" /usr/share/plymouth/themes/pix/splash.png
fi
plymouth-set-default-theme pix -R || true

echo "=== 9. Enabling systemd-time-wait-sync ==="
systemctl enable systemd-time-wait-sync.service

echo "=== 10. Creating WiFi Fallback Captive Portal Script & Service ==="
cat << 'EOF' > /usr/local/bin/mirrordash-wifi-check.sh
#!/bin/bash
INTERFACE="wlan0"
SSID="MirrorDash-Setup"
PASSWORD="mirrordash"

logger -t mirrordash-wifi "Starting network connectivity check..."
for i in {1..30}; do
    IP=$(ip -4 addr show dev "$INTERFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
    if [ ! -z "$IP" ]; then
        logger -t mirrordash-wifi "Network online. IP: $IP. Exiting."
        exit 0
    fi
    sleep 1
done

logger -t mirrordash-wifi "No IP assigned to $INTERFACE after 30 seconds. Switching to setup hotspot..."
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

echo "=== 11. Creating MirrorDash Background Service ==="
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

echo "=========================================================="
echo " MirrorDash setup successfully completed!"
echo " Recommended: Reboot the Raspberry Pi to test components."
echo " Run: sudo reboot"
echo "=========================================================="
