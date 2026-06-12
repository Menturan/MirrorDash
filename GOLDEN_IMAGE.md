# MirrorDash Golden Image Construction Blueprint

This guide details the step-by-step procedure to build, configure, harden, and compress the production **MirrorDash** operating system image ("The Appliance") for deployment on Raspberry Pi hardware (Zero 2 W, Pi 3, Pi 4, Pi 5).

> [!IMPORTANT]
> This guide targets **Raspberry Pi OS Lite (64-bit)** based on **Debian Trixie** (Debian 13). Trixie introduced significant changes from Bookworm: Wayland/labwc replaces X11/Openbox as the default display stack, NetworkManager with Netplan replaces dhcpcd, cloud-init replaces firstrun.sh, systemd-journald is volatile by default, and passwordless sudo is disabled by default. Every section of this document accounts for these changes.

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

### 1.4 First Boot: Expand Root & Create Persistent Partition

Because the automatic partition expansion script was disabled in Section 1.3, your root partition (`/dev/mmcblk0p2`) starts at only 3.5GB in size. To expand it to `6GB` and prepare the persistent storage layout directories, run the unified command chain:

```bash
# Expand root to 6GB, create partition 3, format it, and initialize directories
printf "Yes\nIgnore\n" | sudo parted /dev/mmcblk0 ---pretend-input-tty resizepart 2 6GB && \
sudo resize2fs /dev/mmcblk0p2 && \
printf "Ignore\n" | sudo parted /dev/mmcblk0 ---pretend-input-tty mkpart primary ext4 6GB 100% && \
sudo mkfs.ext4 -F -L mirrordash-data /dev/mmcblk0p3 && \
sudo mkdir -p /storage && \
sudo mount /dev/mmcblk0p3 /storage && \
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

> [!TIP]
> **Fast-Track Scripted Setup (Recommended)**:
> You can configure the entire appliance automatically in a single command (which runs Steps 1.4 through 6 inclusive). Ensure your Pi is connected to the internet, and run:
> ```bash
> curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/setup_appliance.sh | sudo bash
> ```
> After the script finishes, you can reboot the Pi to verify the system, then skip directly to **[Section 7: Failsafe Locking & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)**.

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
    pix-plym-splash && \
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

Configure `getty` for passwordless console autologin, prepare the `.bash_profile` Wayland hook, and create the labwc compositor auto-start layout file in one step:

```bash
# 1. Enable console auto-login B2
sudo raspi-config nonint do_boot_behaviour B2

# 2. Append auto-launch hook for Wayland on tty1 login
if ! grep -q "exec labwc" /home/pi/.bash_profile 2>/dev/null; then
  echo '[[ -z $WAYLAND_DISPLAY && $XDG_VTNR -eq 1 ]] && exec labwc' >> /home/pi/.bash_profile
fi

# 3. Create labwc configuration folder and autostart kiosk rules
mkdir -p /home/pi/.config/labwc
cat << 'EOF' > /home/pi/.config/labwc/autostart
# --- MirrorDash Kiosk Autostart ---

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
uv venv --python 3.14 /storage/mirrordash/venv_a && \
uv venv --python 3.14 /home/pi/mirrordash/base_venv

# 5. Install mirrordash from PyPI into both virtual environments
# Note: uv is a standalone binary at ~/.local/bin/uv — it is NOT inside the venv.
uv pip install --python .venv mirrordash && \
uv pip install --python /home/pi/mirrordash/base_venv mirrordash

# 6. Download the boot launcher script directly from GitHub
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/launch.sh \
    -o /home/pi/mirrordash/launch.sh && \
chmod +x /home/pi/mirrordash/launch.sh
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
for opt in "loglevel=3" "quiet" "splash" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles"; do
  if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
    sudo sed -i "s/$/ $opt/" /boot/firmware/cmdline.txt
  fi
done

# 4. Download MirrorDash Plymouth splash asset, rebuild initramfs, and enable NTP sync guard
# On Trixie, --rebuild-initrd is required for splash changes to take effect on boot.
sudo mkdir -p /usr/share/plymouth/themes/pix && \
curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/splash.png \
    | sudo tee /usr/share/plymouth/themes/pix/splash.png > /dev/null && \
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
sudo nmcli device disconnect "$INTERFACE" 2>/dev/null || true
sudo nmcli device wifi hotspot ifname "$INTERFACE" ssid "$SSID" password "$PASSWORD"

if [ $? -eq 0 ]; then
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

### 7.1 Pre-Lock Verification

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

# Verify sudoers configuration
sudo -n mount -o remount,rw /   # Should succeed without password prompt
sudo -n mount -o remount,ro /
```

### 7.2 Lock Root & Finalize

Purge your development WiFi connection profile, disable the SSH service, set the default base system timezone to UTC, enable OverlayFS, and power down the Pi in one clean execution sequence:

```bash
# 1. Clean up local testing networks
sudo nmcli connection delete "YourTestingWiFiSSID" 2>/dev/null || true

# 2. Disable SSH, set system timezone, enable OverlayFS, and poweroff
sudo systemctl disable ssh && \
sudo timedatectl set-timezone UTC && \
sudo raspi-config nonint enable_overlayfs && \
sudo poweroff
```

### 7.3 Clone & Shrink the Image (Workstation)

Insert the SD card into a Linux workstation, extract the raw block image using `dd`, and minimize it using `pishrink.sh` in one step:

> [!CAUTION]
> **Identify your SD card device carefully before running `dd`.** Running `dd` on the wrong device will irrecoverably overwrite that disk. Verify your SD card's device path first with `lsblk` or `sudo fdisk -l` and substitute `/dev/sdX` below with the correct device (e.g. `/dev/sdb`). **Never use `/dev/sda`** unless you are absolutely certain that is your SD card and not your system drive.

> [!IMPORTANT]
> **Do NOT use PiShrink's `-a` (auto-expand) flag.** The `-a` flag does not exist in standard PiShrink releases. More critically, PiShrink targets the **last partition** of the image for shrinking — which is our `/storage` partition (`mmcblk0p3`), not the rootfs. Using auto-expand logic on our custom 3-partition layout will attempt to resize the wrong partition and **corrupt the image**. Always use `-z` only (gzip compression, no auto-expand). Our partitions are already sized correctly and require no expansion on first boot.

```bash
# 0. Identify the correct device — substitute /dev/sdX with your actual SD card device
lsblk

# Extract the image
sudo dd if=/dev/sdX of=mirrordash-raw.img bs=4M status=progress

# Download PiShrink, shrink only the rootfs, and compress
# -z: gzip compress the output. Do NOT add -a (auto-expand): our 3-partition layout
# is already correctly sized and PiShrink would target the wrong (last) partition.
wget -N https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh && \
chmod +x pishrink.sh && \
sudo ./pishrink.sh -z mirrordash-raw.img mirrordash-final.img
```

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
