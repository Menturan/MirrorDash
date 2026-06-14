# MirrorDash Golden Image Construction Blueprint

This guide details the step-by-step procedure to build, configure, harden, and compress the production **MirrorDash** operating system image ("The Appliance") for deployment on Raspberry Pi hardware (Zero 2 W, Pi 3, Pi 4, Pi 5).

> [!IMPORTANT]
> This guide targets **Raspberry Pi OS Lite (64-bit)** based on **Debian Trixie** (Debian 13). Trixie introduced significant changes from Bookworm: Wayland/labwc replaces X11/Openbox as the default display stack, NetworkManager with Netplan replaces dhcpcd, cloud-init replaces firstrun.sh, systemd-journald is volatile by default, and passwordless sudo is disabled by default. Every section of this document accounts for these changes.

## Table of Contents

- [Automated Build Pipeline (Recommended)](#automated-build-pipeline-recommended)
- [1. Operating System Initialization](#1-operating-system-initialization)
- [2. System Configuration & Package Installation](#2-system-configuration--package-installation)
- [3. Environment & Application Setup](#3-environment--application-setup)
- [4. Hardware Hardening & Boot Splashes](#4-hardware-hardening--boot-splashes)
- [5. Captive Portal (WiFi-Fallback State Machine)](#5-captive-portal-wifi-fallback-state-machine)
- [6. MirrorDash Core Service Daemon](#6-mirrordash-core-service-daemon)
- [7. Failsafe Locking (OverlayFS) & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)
- [Appendix A: Persistence Model](#appendix-a-persistence-model)

---

## Automated Build Pipeline (Recommended)

You can build the complete, production-ready, locked SD card image from scratch on a Linux workstation without ever needing a physical Raspberry Pi. The automated build script downloads the latest base OS, resizes partitions, uses QEMU to emulate the ARM environment, runs the complete configuration, and shrinks the final image.

Run the build script on your Debian/Ubuntu workstation. You can optionally provide an output directory as an argument if you want to build on a larger external drive:

```bash
sudo bash scripts/build_image.sh [/path/to/large/drive]
```

**Requirements:**
- A Debian/Ubuntu Linux workstation.
- Host dependencies: `qemu-user-static`, `parted`, `xz-utils`, `curl`, `wget`, `e2fsprogs`, `coreutils`, `sha256sum`.
- Root (`sudo`) privileges to mount loop devices and chroot.

The script will automatically generate a compressed, deployment-ready `mirrordash-final.img.gz` in the provided output directory (or a local `build_workspace/` directory by default).

If you use the automated build pipeline, you do **not** need to follow the manual steps below. The manual steps are provided for reference, debugging, and alternative hardware configurations.

---

## 1. Operating System Initialization

### 1.1 Flash OS

Use Raspberry Pi Imager to flash **Raspberry Pi OS Lite (64-bit)** (Debian Trixie) onto a high-quality, high-endurance SD card (e.g., Samsung PRO Endurance or SanDisk MAX Endurance).

### 1.2 Pre-configuration (OS Customization Settings)

After selecting the OS and storage media in Raspberry Pi Imager, click **Next** and choose **Edit Settings** to open the OS Customization dialog:

- **General Tab**:
  - Check **Set hostname** and enter `mirrordash`.
  - Check **Set username and password**, setting username to `pi` and entering a secure password.
  - Check **Configure wireless LAN** and enter your local network credentials (used for development and staging only — these will be purged before image finalization).
  - Check **Set locale settings**, selecting your local timezone and keyboard layout.
- **Services Tab**:
  - Check **Enable SSH** and select **Use password authentication** to allow remote command-line access during setup.
- Click **Save** and then **Yes** to write these settings to the card.

> [!NOTE]
> Trixie uses **cloud-init** for first-boot provisioning. The Imager generates `user-data`, `network-config`, and `meta-data` files on the boot partition. Network profiles created here are managed by **Netplan** (YAML in `/etc/netplan/`) backed by **NetworkManager**.

### 1.3 Failsafe Partitioning Preparation (Workstation)

Before ejecting the SD card from your workstation and booting the Pi for the first time, you must prevent the automatic root partition expansion script from running. This leaves the remaining SD card space unallocated so you can easily create the persistent partition later:

1. **Open cmdline.txt**:
   With the SD card still inserted in your workstation, open the boot partition (labeled `bootfs`) and locate `cmdline.txt`.
2. **Disable the resize parameter**:
   Delete the standalone `resize` parameter from the single line of boot arguments in `cmdline.txt`. Save and close the file.

   > [!NOTE]
   > On the latest Raspberry Pi OS (Trixie), the auto-resize is controlled by the standalone `resize` parameter in `cmdline.txt`. The older `init=/usr/lib/raspi-config/init_resize.sh` form is a Bookworm-era artifact and will **not** appear on Trixie images.
3. **Eject and insert**:
   Eject the SD card from your workstation, insert it into the Raspberry Pi, and power it on.

> [!TIP]
> **Fast-Track Scripted Setup (Recommended)**:
> You can configure the entire appliance automatically in a single command (which runs all configuration steps from Section 1.4 through Section 6 inclusive). Ensure your Pi is connected to the internet, and run:
> ```bash
> curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/setup_appliance.sh | sudo bash
> ```
> After the script finishes, you can reboot the Pi to verify the system, then skip directly to **[Section 7: Failsafe Locking & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)**.

### 1.4 First Boot: Expand Root & Create Persistent Partition

Because the automatic partition expansion script was disabled in Section 1.3, your root partition starts at only 3.5GB in size. To expand it to `6GB` and prepare the persistent storage layout directories dynamically on any boot drive (SD card, USB SSD, or NVMe), run the unified command chain:

```bash
# 1. Identify the system block device and partitions dynamically
ROOT_PART=$(findmnt -n -o SOURCE /)
ROOT_DISK=$(echo "$ROOT_PART" | sed 's/p[0-9]\+$//; s/[0-9]\+$//')
if [[ "$ROOT_DISK" =~ [0-9]$ ]]; then PART_SUFFIX="p"; else PART_SUFFIX=""; fi
DATA_PART="${ROOT_DISK}${PART_SUFFIX}3"
ROOT_PART_NAME="${ROOT_DISK}${PART_SUFFIX}2"

# 2. Expand root to 6GB, create partition 3, format it, and initialize directories
printf "Yes\nIgnore\n" | sudo parted "$ROOT_DISK" ---pretend-input-tty resizepart 2 6GB && \
sudo resize2fs "$ROOT_PART_NAME" && \
printf "Ignore\n" | sudo parted "$ROOT_DISK" ---pretend-input-tty mkpart primary ext4 6GB 100% && \
sudo mkfs.ext4 -F -L mirrordash-data "$DATA_PART" && \
sudo mkdir -p /storage && \
sudo mount "$DATA_PART" /storage && \
sudo mkdir -p /storage/mirrordash/data /storage/mirrordash/venv_a /storage/mirrordash/venv_b && \
sudo chown -R pi:pi /storage && \
mkdir -p /home/pi/.mirrordash/cache /home/pi/.mirrordash/data
```

### 1.5 Update `/etc/fstab` & Mount

Append the storage layout mounts to `/etc/fstab` and mount all filesystems in one step:

```bash
# Append MirrorDash storage layout mounts to /etc/fstab
sudo tee -a /etc/fstab << 'EOF'

# --- MirrorDash Storage ---
# Persistent data partition (survives OverlayFS)
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60,nofail,x-systemd.device-timeout=5  0  2

# Bind-mount persistent data into the application's expected path
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind,nofail,x-systemd.device-timeout=5  0  0

# Volatile module cache in RAM (100 MB)
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF

# Mount and check partition details
sudo mount -a && df -h | grep -E "storage|mirrordash"
```

---

## 2. System Configuration & Package Installation

With the disk space expanded and the persistent storage mounted, you can safely update the system, install dependencies, and configure the services.

### 2.1 Update & Install Packages

If you prefer to perform the setup manually step-by-step, run the unified system update and installation chain:

```bash
sudo apt update && sudo apt full-upgrade -y && \
sudo apt install -y --no-install-recommends \
    labwc \
    chromium \
    wlr-randr \
    avahi-daemon \
    nginx \
    plymouth \
    plymouth-themes \
    pix-plym-splash \
    parted \
    python3 && \
sudo apt autoclean -y && sudo apt autoremove -y
```

**Package rationale:**

| Package | Purpose |
|---------|---------|
| `labwc` | Minimal wlroots-based Wayland compositor (~5 MB RSS). Replaces Xorg + Openbox. |
| `chromium` | Kiosk display browser with native Wayland support via `--ozone-platform=wayland`. The legacy `chromium-browser` package is deprecated on Trixie/Debian 13. |
| `wlr-randr` | Display output control (rotation, resolution, power on/off) under Wayland. Replaces `xrandr`. |
| `avahi-daemon` | mDNS/DNS-SD responder (Bonjour/Zeroconf). Advertises the device as `mirrordash.local` on the local network so users never need to type an IP address. |
| `nginx` | Lightweight reverse proxy. Listens on port 80 so the mirror is reachable at `http://mirrordash.local` with no port number, then forwards traffic to uvicorn on `localhost:8000`. |
| `plymouth` | Boot animation manager used to render the startup splash screen. |
| `plymouth-themes` | Standard theme definitions (e.g. spinner, glow) for Plymouth. |
| `pix-plym-splash` | The Raspberry Pi-specific "pix" desktop Plymouth theme package required for the customized startup splash screen. |
| `parted` | Partition manipulation tool. Required to expand the root and data partitions early on boot. |
| `python3` | Python 3 runtime interpreter. Required for running transparent cursor generation and local scripts. |

> [!NOTE]
> **NetworkManager** is the default network backend on Trixie — no separate install is needed. **log2ram** is not installed because Trixie configures `systemd-journald` as **volatile by default** (logs go to RAM and are lost on reboot), which already eliminates the primary SD card write source.

### 2.2 Hostname & mDNS Setup

Set the device hostname to `mirrordash` so it is reachable at **`mirrordash.local`** on the local network from any device (Mac, Linux, Windows 10+) — no IP address needed.

```bash
# Set the hostname
sudo hostnamectl set-hostname mirrordash

# Reflect the new hostname in /etc/hosts
sudo sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts

# Enable and start the mDNS daemon
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

> [!NOTE]
> `avahi-daemon` broadcasts the device hostname via mDNS (Bonjour/Zeroconf) on the local subnet. After completing the nginx step below, your mirror will be reachable at `http://mirrordash.local` from any browser on the same WiFi network — no IP lookup or port number required. The `.local` resolution works natively on macOS and Linux. On Windows 10/11, it requires Bonjour (bundled with iTunes) or is handled automatically by the mDNS client built into Windows 10 1903+.

### 2.3 nginx Reverse Proxy

Install nginx as a reverse proxy so the mirror is reachable at **`http://mirrordash.local`** (port 80, no port number) instead of `http://mirrordash.local:8000`.

```bash
# Remove the default nginx site and write the MirrorDash proxy config
sudo rm -f /etc/nginx/sites-enabled/default
sudo tee /etc/nginx/sites-available/mirrordash << 'EOF'
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
sudo ln -sf /etc/nginx/sites-available/mirrordash /etc/nginx/sites-enabled/mirrordash
sudo nginx -t && sudo systemctl enable nginx && sudo systemctl restart nginx
```

> [!NOTE]
> The `/ws` location block is critical — WebSocket connections require `Upgrade` and `Connection` headers to be forwarded. Without this block, the real-time module updates on the mirror display will fail. The `proxy_read_timeout 86400` prevents nginx from closing idle WebSocket connections after 60 seconds.

### 2.4 Console Auto-Login & Kiosk Autostart Setup

Configure `getty` for passwordless console autologin, prepare the `.bash_profile` Wayland hook, globally disable the mouse cursor in standard system themes, and create the labwc compositor auto-start layout file:

```bash
# 1. Enable console auto-login B2
sudo raspi-config nonint do_boot_behaviour B2

# 2. Silence tty1 console getty auto-login prompt and banner messages
if [ -f /etc/systemd/system/getty@tty1.service.d/autologin.conf ]; then
  sudo sed -i 's/--autologin/--noissue --skip-login --autologin/g' /etc/systemd/system/getty@tty1.service.d/autologin.conf
fi

# 3. Silence shell login banners (MOTD and Last login text)
touch /home/pi/.hushlogin

# 4. Append auto-launch hook for Wayland on tty1 login
if ! grep -q "exec labwc" /home/pi/.bash_profile 2>/dev/null; then
  echo '[[ -z $WAYLAND_DISPLAY && $XDG_VTNR -eq 1 ]] && exec labwc' >> /home/pi/.bash_profile
fi

# 5. Create a local transparent cursor theme for the kiosk user to hide the mouse cursor
mkdir -p /home/pi/.local/share/icons/invisible/cursors

# Create index.theme so applications recognize it as a valid theme
cat << 'EOF' > /home/pi/.local/share/icons/invisible/index.theme
[Icon Theme]
Name=invisible
Comment=Invisible cursor theme
EOF

# Write a valid, 32x32 transparent XCursor file
python3 -c "import struct; data = struct.pack('<4sIII', b'Xcur', 16, 0x00010000, 1) + struct.pack('<III', 0xfffd0002, 32, 28) + struct.pack('<IIIIIIIII', 36, 0xfffd0002, 32, 1, 32, 32, 0, 0, 0) + b'\x00'*(32*32*4); open('/home/pi/.local/share/icons/invisible/cursors/default', 'wb').write(data)"
ln -sf default /home/pi/.local/share/icons/invisible/cursors/left_ptr
ln -sf default /home/pi/.local/share/icons/invisible/cursors/pointer
chown -R pi:pi /home/pi/.local

# 6. Configure labwc to use the invisible cursor theme
mkdir -p /home/pi/.config/labwc
echo "XCURSOR_THEME=invisible" > /home/pi/.config/labwc/environment

# 7. Create labwc configuration folder and autostart kiosk rules
cat << 'EOF' > /home/pi/.config/labwc/autostart
# --- MirrorDash Kiosk Autostart ---

# Hide mouse cursor natively at Wayland startup
labwc-msg HideCursor 2>/dev/null || true

# Prevent Chromium "didn't shut down correctly" restore prompt after power loss
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' /home/pi/.config/chromium/'Local State' 2>/dev/null
sed -i 's/"exit_type":"[^"]\+"/"exit_type":"Normal"/' /home/pi/.config/chromium/Default/Preferences 2>/dev/null

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
chmod +x /home/pi/.config/labwc/autostart
```

> [!NOTE]
> The `sed` commands at the top are a **crash recovery guard**. After a hard power loss (pulling the plug), Chromium would normally show a "pages didn't load correctly" restore dialog on the next boot. These commands silently clear that state before launch, ensuring unattended kiosk recovery.

### 2.5 Volatile Logging Strategy

Trixie configures `systemd-journald` as **volatile by default** — logs are stored only in RAM (`/run/log/journal`) and are lost on reboot. This is the desired behavior for a production appliance: zero SD card wear from logging, with no additional packages needed.

If persistent logs are needed for debugging during development, they can be temporarily enabled via:

```bash
sudo raspi-config   # Advanced Options → Logging → Persistent
```

---

## 3. Environment & Application Setup

### 3.1 Install uv & Deploy Application

Initialize the deployment working directory, install `uv` (modern Python package manager), configure the persistent A/B virtual environment layout, and deploy the application:

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh && \
export PATH="$HOME/.local/bin:$PATH"

# 2. Create app directory
mkdir -p /home/pi/mirrordash && cd /home/pi/mirrordash

# 3. Setup symlinks to the persistent partition
ln -sfT venv_a /storage/mirrordash/venv && \
ln -sfT /storage/mirrordash/venv /home/pi/mirrordash/.venv

# 4. Create base_venv (Golden Copy) and active venv_a
uv venv --allow-existing --python 3.14 /storage/mirrordash/venv_a && \
uv venv --allow-existing --python 3.14 /home/pi/mirrordash/base_venv

# 5. Install mirrordash from PyPI into both virtual environments
# Note: uv is a standalone binary at ~/.local/bin/uv — it is NOT inside the venv.
uv pip install --python .venv mirrordash && \
uv pip install --python /home/pi/mirrordash/base_venv mirrordash

# 6. Download the auxiliary scripts and HTML assets directly from GitHub
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/launch.sh \
    -o /home/pi/mirrordash/launch.sh && \
chmod +x /home/pi/mirrordash/launch.sh && \
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/loading.html \
    -o /home/pi/mirrordash/loading.html

# 7. Ensure correct file ownership
sudo chown -R pi:pi /home/pi/mirrordash
```

### 3.2 Passwordless Sudo for Application Commands

> [!IMPORTANT]
> As of April 2026, Raspberry Pi OS Trixie **disables passwordless sudo by default**. The MirrorDash backend runs as the `pi` user and invokes `sudo` for system administration tasks (filesystem remounting, SSH control, timezone changes, network management, display backlight control). Without passwordless sudo for these specific commands, the application will **deadlock** waiting for a password prompt that never comes.

Create the sudoers drop-in configuration file, set standard permissions (`0440`), and validate its syntax in one block:

```bash
sudo tee /etc/sudoers.d/mirrordash << 'EOF'
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
sudo chmod 440 /etc/sudoers.d/mirrordash
sudo visudo -cf /etc/sudoers.d/mirrordash
```

---

## 4. Hardware Hardening & Boot Splashes

Configure the watchdog daemon, optimize the boot files for fast silent booting, copy the Plymouth splash screen asset, and enable timezone/NTP sync guards in one combined step:

```bash
# 1. Enable the hardware watchdog
sudo sed -i 's/#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=14s/' /etc/systemd/system.conf && \
sudo systemctl daemon-reexec

# 2. Append visual boot suppression and GPU allocations to config.txt
sudo tee -a /boot/firmware/config.txt << 'EOF'

# --- MirrorDash Hardware Hardening ---
disable_splash=1
boot_delay=0
gpu_mem=128
dtoverlay=disable-bt
EOF

# 3. Silence kernel log prints and redirect console to tty3 in cmdline.txt
sudo sed -i 's/console=tty1/console=tty3/g' /boot/firmware/cmdline.txt
for opt in "loglevel=0" "quiet" "splash" "systemd.show_status=false" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles" "logo.nologo"; do
  if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
    sudo sed -i "s/$/ $opt/" /boot/firmware/cmdline.txt
  fi
done

# 4. Download MirrorDash Plymouth splash assets, disable theme status messages, patch for separate shutdown splash, rebuild initramfs, and enable NTP sync guard
# On Trixie, --rebuild-initrd is required for splash changes to take effect on boot.
sudo mkdir -p /usr/share/plymouth/themes/pix && \
curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/splash.png \
    | sudo tee /usr/share/plymouth/themes/pix/splash.png > /dev/null && \
curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/shutdown.png \
    | sudo tee /usr/share/plymouth/themes/pix/shutdown.png > /dev/null && \
( [ ! -f /usr/share/plymouth/themes/pix/pix.script ] || ( \
  sudo sed -i 's/^[[:space:]]*Plymouth\.SetMessageFunction/# Plymouth.SetMessageFunction/g' /usr/share/plymouth/themes/pix/pix.script && \
  sudo sed -i 's/^[[:space:]]*Plymouth\.SetUpdateStatusFunction/# Plymouth.SetUpdateStatusFunction/g' /usr/share/plymouth/themes/pix/pix.script && \
  sudo sed -i -E 's/([a-zA-Z0-9_]+)[[:space:]]*=[[:space:]]*Image[[:space:]]*\("splash.png"\);/if (Plymouth.GetMode() == "shutdown") { \1 = Image("shutdown.png"); } else { \1 = Image("splash.png"); }/g' /usr/share/plymouth/themes/pix/pix.script \
) ) && \
sudo plymouth-set-default-theme --rebuild-initrd pix && \
sudo systemctl enable systemd-time-wait-sync.service
```

> [!TIP]
> **Splash Screen Preview**:
> You can preview your splash screen without a reboot by running:
> ```bash
> sudo plymouthd && sudo plymouth --show-splash
> # To dismiss the preview:
> sudo plymouth quit
> ```


---

## 5. Captive Portal (WiFi-Fallback State Machine)

When the appliance boots and fails to lease a valid IP address within 30 seconds, it launches an autonomous Access Point (AP) setup hotspot to handle reconfiguration over a local captive network.

Write the fallback connectivity watchdog script, set executable permissions, and define/enable the fallback service in one copy-paste block:

```bash
# 1. Write connectivity watchdog script
sudo tee /usr/local/bin/mirrordash-wifi-check.sh << 'EOF'
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
sudo nmcli connection delete "$SSID" 2>/dev/null || true

# Add and configure a custom hotspot profile with PMF disabled to avoid Broadcom firmware bugs
sudo nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode ap
sudo nmcli connection modify "$SSID" wifi-sec.key-mgmt wpa-psk
sudo nmcli connection modify "$SSID" wifi-sec.psk "$PASSWORD"
sudo nmcli connection modify "$SSID" wifi-sec.pmf 1
sudo nmcli connection modify "$SSID" ipv4.method shared

# Attempt to bring the connection up
if sudo nmcli connection up "$SSID"; then
    logger -t mirrordash-wifi "Hotspot '$SSID' started successfully."
else
    logger -t mirrordash-wifi "Failed to start hotspot."
fi
EOF
sudo chmod +x /usr/local/bin/mirrordash-wifi-check.sh

# 2. Write and enable the systemd fallback service
sudo tee /etc/systemd/system/mirrordash-wifi-fallback.service << 'EOF'
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
sudo systemctl enable mirrordash-wifi-fallback.service
```

---

## 6. MirrorDash Core Service Daemon

Create the primary background service manager unit and enable it to run at system startup:

```bash
# 1. Write the primary application service file
sudo tee /etc/systemd/system/mirrordash.service << 'EOF'
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

# 2. Enable the daemon
sudo systemctl enable mirrordash.service
```

> [!NOTE]
> `RequiresMountsFor=` ensures the persistent data partition is mounted before the service starts. The `Environment=` directives expose the venv's `PATH` to subprocess calls (e.g., `uv pip install` during module installation).

---

## 7. Failsafe Locking (OverlayFS) & Image Finalization

To finalize the Golden Image deployment, the environment must be stripped of development artifacts and locked into a write-protected template.

> [!WARNING]
> Ensure **all** configurations, packages, system services, and baseline settings are fully tested **before** executing this section. Once OverlayFS is enabled, the root filesystem is permanently read-only (disable via `raspi-config` to make further changes).

### 7.1 Fast-Track Finalization Script (Recommended)

To verify the setup, purge development artifacts and Wi-Fi profiles, lock the root filesystem with OverlayFS, and power down the appliance in a single robust step, run the unified MirrorDash finalization utility.

Depending on how you are currently connected to the Pi, select one of the following methods:

#### Method A: If connected directly (via keyboard & monitor on tty2)
Run the script interactively. It will verify your services, present a warning, and request confirmation before proceeding. You can execute the script directly from GitHub on the fly:
```bash
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/finalize_appliance.sh | sudo bash
```

> [!TIP]
> If you used the automated setup script (`setup_appliance.sh`), the utility is already pre-installed locally. You can alternatively run it with:
> ```bash
> sudo mirrordash-finalize.sh
> ```

#### Method B: If connected remotely (via SSH)
Because purging the Wi-Fi credentials will immediately disconnect your SSH session and terminate standard shell commands, you **must** run the script in a detached background unit using `systemd-run` and pass the `-y`/`--yes` argument to bypass interactive prompts.

You can download and run the script on the fly from GitHub:
```bash
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/finalize_appliance.sh -o /tmp/finalize.sh && \
chmod +x /tmp/finalize.sh && \
sudo systemd-run --description="MirrorDash Finalize" /tmp/finalize.sh -y
```

> [!TIP]
> If you used the automated setup script (`setup_appliance.sh`), the utility is already pre-installed locally. You can alternatively run:
> ```bash
> sudo systemd-run --description="MirrorDash Finalize" mirrordash-finalize.sh -y
> ```

> [!NOTE]
> Since the finalization script automatically handles verification, system cleanup, Wi-Fi purging, OverlayFS locking, and powering down the Pi, you can skip the manual steps in **[Section 7.2](#72-pre-lock-verification-manual)** and **[Section 7.3](#73-lock-root--finalize-manual)** and proceed directly to **[Section 7.4: Clone & Shrink the Image (Workstation)](#74-clone--shrink-the-image-workstation)**.

### 7.2 Pre-Lock Verification (Manual)

Verify that all critical services are functional and the persistent partition is mounted:

```bash
# Verify persistent storage
mount | grep /storage
ls -la /home/pi/.mirrordash/data/

# Verify MirrorDash starts correctly
sudo systemctl start mirrordash.service
curl -s http://localhost:8000/health

# Verify WiFi fallback service is enabled
sudo systemctl is-enabled mirrordash-wifi-fallback.service

# Verify time-sync service is enabled
sudo systemctl is-enabled systemd-time-wait-sync.service

# Verify sudoers configuration (should not prompt for a password)
sudo -n mount -o remount,rw /   # Should succeed without password prompt
sudo -n mount -o remount,ro /   # May fail with "mount point is busy" on a live system, but must not prompt for a password
```

### 7.3 Lock Root & Finalize (Manual)

Perform a final system cleanup (prune package caches, clear temporary files, truncate logs, and strip command history), purge your development Wi-Fi connection profiles, disable the SSH service, set the default base system timezone to UTC, enable OverlayFS, and power down the Pi in one clean execution sequence:

```bash
# 1. Perform final system cleanup and package pruning
sudo apt-get clean && \
sudo apt-get autoremove -y && \
sudo rm -rf /tmp/* /var/tmp/* /root/.cache /home/pi/.cache && \
sudo find /var/log -type f -exec truncate -s 0 {} \; && \
sudo journalctl --vacuum-time=1s 2>/dev/null || true && \
rm -f ~/.bash_history && history -c

# 2. Disable SSH and set default system timezone to UTC
sudo systemctl disable ssh && \
sudo timedatectl set-timezone UTC

# 3. Clean up all wireless networks failsafely, enable OverlayFS, and power down
# Note: These commands are chained with '&&' in a single sequence so they run to completion even after your Wi-Fi/SSH connection drops.
for uuid in $(nmcli --fields UUID,TYPE connection show | awk '$2 ~ /wifi|802-11-wireless/ {print $1}'); do sudo nmcli connection delete "$uuid" 2>/dev/null || true; done && \
sudo rm -rf /etc/NetworkManager/system-connections/* && \
sudo raspi-config nonint enable_overlayfs && \
sudo poweroff
```

---

### 7.4 Clone & Shrink the Image (Workstation)

Insert the SD card of the finalized MirrorDash appliance into a Linux workstation, and run the failsafe extraction script to clone, shrink, and compress the image:

> [!TIP]
> The MirrorDash repository includes a wrapper script `scripts/extract_golden_image.sh` that scans connected block devices, identifies the MirrorDash SD card by its label (`mirrordash-data`), prints device details, and prompts for confirmation to prevent accidental drive overwrites.

Run the extraction script on your workstation (you can optionally pass an output directory as an argument to write files to a drive with more space, or leave it blank to be prompted interactively):

```bash
# Run the extraction wrapper script as root (optionally passing output path)
sudo bash scripts/extract_golden_image.sh [/path/to/large/drive]
```

The script will automatically:
1. Scan and detect the correct SD card block device node.
2. Confirm the selection with you interactively to avoid wiping your workstation drives.
3. Perform a safe raw block extraction (`dd`) into a temporary file `mirrordash-raw.img`.
4. Download the latest `pishrink.sh` utility.
5. Shrink partition 3 (`mirrordash-data`) to its minimum size and truncate the image.
6. Gzip-compress the final image into `mirrordash-final.img.gz`.

The compiled `mirrordash-final.img.gz` is a fully optimized, failsafe, locked golden image ready for deployment.

---

## Appendix A: Persistence Model

This table documents which data survives a reboot under the OverlayFS-locked production image:

| Data | Location | Storage | Survives Reboot? |
|------|----------|---------|-----------------|
| Application config | `~/.mirrordash/data/config.json` | Persistent partition (bind mount) | ✅ Yes |
| Module persistent data | `~/.mirrordash/data/<module>/` | Persistent partition (bind mount) | ✅ Yes |
| Module cache | `~/.mirrordash/cache/<module>/` | tmpfs (RAM) | ❌ No |
| System logs | `/run/log/journal/` | RAM (volatile journald) | ❌ No |
| Installed packages | `/storage/mirrordash/venv` | Persistent partition via symlink | ✅ Yes |
| SSH toggle state | `/etc/systemd/system/` | Saved in `config.json` & re-applied at boot | ✅ Yes |
| System password | `/etc/shadow` | Saved in `pi_password.hash` & re-applied at boot | ✅ Yes |
| Timezone | `/etc/localtime` | Saved in `config.json` & re-applied at boot | ✅ Yes |

> [!IMPORTANT]
> **A/B updates and settings persistence** are fully automated. SSH toggle states, the user password hash, and the selected timezone are persistently stored on the `/storage` partition and dynamically applied at boot by the `module_loader` system service.
> 
> **Failsafe Rollback System**: If an update or module installation corrupts the virtual environment and causes startup crashes, the launcher (`launch.sh`) automatically rolls back to the stable copy (`venv_old`) or boots from the read-only Golden Copy (`base_venv` in Safe Mode) and displays warning alerts in the Admin and Kiosk UIs.
