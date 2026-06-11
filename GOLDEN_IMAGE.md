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
   With the SD card still inserted in your workstation, open the boot partition (labeled `boot/firmware` or `boot`) and locate `cmdline.txt`.
2. **Disable the resize script**:
   Delete `init=/usr/lib/raspi-config/init_resize.sh` from the single line of boot arguments in `cmdline.txt`. Save and close the file.
3. **Eject and insert**:
   Eject the SD card from your workstation, insert it into the Raspberry Pi, and power it on.

### 1.4 Boot, Update & Install Packages

> [!TIP]
> **Fast-Track Scripted Setup (Recommended)**:
> You can configure the entire appliance automatically in a single command (which runs Steps 1.4 through 6 inclusive). Ensure your Pi is connected to the internet, and run:
> ```bash
> curl -sSL https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/setup_appliance.sh | sudo bash
> ```
> After the script finishes, you can reboot the Pi to verify the system, then skip directly to **[Section 7: Failsafe Locking & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)**.

If you prefer to perform the setup manually step-by-step, run the unified system update and installation chain:

```bash
sudo apt update && sudo apt full-upgrade -y && \
sudo apt install -y --no-install-recommends \
    labwc \
    chromium-browser \
    wlr-randr \
    git \
    plymouth \
    plymouth-themes && \
sudo apt autoclean -y && sudo apt autoremove -y
```

**Package rationale:**

| Package | Purpose |
|---------|---------|
| `labwc` | Minimal wlroots-based Wayland compositor (~5 MB RSS). Replaces Xorg + Openbox. |
| `chromium-browser` | Kiosk display browser with native Wayland support via `--ozone-platform=wayland`. |
| `wlr-randr` | Display output control (rotation, resolution, power on/off) under Wayland. Replaces `xrandr`. |
| `git` | Required for uv and development utilities. |
| `plymouth` | Boot animation manager used to render the startup splash screen. |
| `plymouth-themes` | Standard theme definitions (e.g. spinner, glow) for Plymouth. |

> [!NOTE]
> **NetworkManager** is the default network backend on Trixie — no separate install is needed. **log2ram** is not installed because Trixie configures `systemd-journald` as **volatile by default** (logs go to RAM and are lost on reboot), which already eliminates the primary SD card write source.

### 1.5 Console Auto-Login & Kiosk Autostart Setup

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
chmod +x /home/pi/.config/labwc/autostart
```

> [!NOTE]
> The `sed` commands at the top are a **crash recovery guard**. After a hard power loss (pulling the plug), Chromium would normally show a "pages didn't load correctly" restore dialog on the next boot. These commands silently clear that state before launch, ensuring unattended kiosk recovery.

---

## 2. Environment & Application Setup

### 2.1 Install uv & Deploy Application

Initialize the deployment working directory, install `uv` (modern Python package manager), configure a standalone virtual environment locked to Python 3.14, and deploy the application package in a single command chain:

```bash
# Install uv, source environment, and install mirrordash core
curl -LsSf https://astral.sh/uv/install.sh | sh && \
source $HOME/.local/bin/env && \
mkdir -p /home/pi/mirrordash && cd /home/pi/mirrordash && \
uv venv --python 3.14 && \
uv pip install mirrordash
```

### 2.2 Passwordless Sudo for Application Commands

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

## 3. Storage Strategy & Directory Contract

To protect the physical SD card from high-frequency write cycles and ensure immunity to sudden power loss corruption, the filesystem operates in a strict hybrid mode. The SD card is partitioned into three regions:

| Partition | Mount | Filesystem | Purpose |
|-----------|-------|------------|---------|
| `mmcblk0p1` | `/boot/firmware` | FAT32 | Boot partition (kernel, firmware, config.txt) |
| `mmcblk0p2` | `/` | ext4 | Root filesystem (protected by OverlayFS in production) |
| `mmcblk0p3` | `/storage` | ext4 | **Persistent data partition** (writable, survives OverlayFS) |

> [!IMPORTANT]
> In Trixie, `raspi-config nonint enable_overlayfs` makes the **entire root filesystem read-only** with a tmpfs upper layer. All writes to `/` (including `/home`, `/etc`, `/var`) are absorbed by RAM and **lost on reboot**. User configuration, module data, and any state that must persist across reboots **must** reside on a separate physical partition that is not covered by the overlay.

### 3.1 Create the Persistent Data Partition

### 3.1 Expand Root & Create Persistent Partition

Because the automatic partition expansion script was disabled in Section 1.3, your root partition (`/dev/mmcblk0p2`) starts at only 3.5GB in size. To expand it to `6GB` and prepare the persistent storage layout directories, run the unified command chain:

```bash
# Expand root to 6GB, create partition 3, format it, and initialize directories
sudo parted /dev/mmcblk0 resizepart 2 6GB && \
sudo resize2fs /dev/mmcblk0p2 && \
sudo parted -s /dev/mmcblk0 mkpart primary ext4 6GB 100% && \
sudo mkfs.ext4 -F -L mirrordash-data /dev/mmcblk0p3 && \
sudo mkdir -p /storage /storage/mirrordash/data && \
mkdir -p /home/pi/.mirrordash/cache /home/pi/.mirrordash/data
```

> [!TIP]
> **Frozen System Immutability**: Keeping the Python virtual environment (`.venv`) and installed packages on the read-only root partition is a critical security design. It ensures that the core codebase is immune to runtime corruption, malware persistence, or accidental changes across reboots. 
> 
> By expanding the root partition to **6GB**, you allocate ample space (around 4.1GB of free space after standard packages) for future modules and large package dependencies (like NumPy, Pillow, or Home Assistant libraries). All high-volume user data (configs, module databases, and caches) resides on partition 3, preventing root disk space depletion.

### 3.2 Update `/etc/fstab` & Mount

Append the storage layout mounts to `/etc/fstab` and mount all filesystems in one step:

```bash
# Append MirrorDash storage layout mounts to /etc/fstab
sudo tee -a /etc/fstab << 'EOF'

# --- MirrorDash Storage ---
# Persistent data partition (survives OverlayFS)
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60  0  2

# Bind-mount persistent data into the application's expected path
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind  0  0

# Volatile module cache in RAM (100 MB)
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF

# Mount and check partition details
sudo mount -a && df -h | grep -E "storage|mirrordash"
```

### 3.4 Logging

Trixie configures `systemd-journald` as **volatile by default** — logs are stored only in RAM (`/run/log/journal`) and are lost on reboot. This is the desired behavior for a production appliance: zero SD card wear from logging, with no additional packages needed.

If persistent logs are needed for debugging during development, they can be temporarily enabled via:

```bash
sudo raspi-config   # Advanced Options → Logging → Persistent
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

# 3. Silence kernel log prints on console by appending parameters to cmdline.txt
if ! grep -q "console=tty3" /boot/firmware/cmdline.txt; then
  sudo sed -i 's/$/ console=tty3 loglevel=3 quiet splash/' /boot/firmware/cmdline.txt
fi

# 4. Install MirrorDash Plymouth splash asset, set pix theme, and enable NTP sync guard
sudo cp static/splash.png /usr/share/plymouth/themes/pix/splash.png && \
sudo plymouth-set-default-theme pix -R && \
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
    IP=$(ip -4 addr show dev "$INTERFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
    if [ ! -z "$IP" ]; then
        logger -t mirrordash-wifi "Network online. IP: $IP. Exiting."
        exit 0
    fi
    sleep 1
done

logger -t mirrordash-wifi "No IP assigned to $INTERFACE after 30 seconds. Switching to setup hotspot..."
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
ExecStart=/home/pi/mirrordash/.venv/bin/uvicorn mirrordash_core.main:app --host 0.0.0.0 --port 8000
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

```bash
# Extract the image, download pishrink, and shrink/compress it
sudo dd if=/dev/sdX of=mirrordash-raw.img bs=4M status=progress && \
wget -N https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh && \
chmod +x pishrink.sh && \
sudo ./pishrink.sh -z -a mirrordash-raw.img mirrordash-final.img
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
| Installed packages | `/home/pi/mirrordash/.venv/` | OverlayFS upper (RAM) | ❌ No |
| SSH toggle state | `/etc/systemd/system/` | OverlayFS upper (RAM) | ❌ No |
| System password | `/etc/shadow` | OverlayFS upper (RAM) | ❌ No |
| Timezone | `/etc/localtime` | OverlayFS upper (RAM) | ❌ No |

> [!IMPORTANT]
> **SSH, password, and timezone changes made via the admin UI are ephemeral.** They take effect immediately but revert on reboot. This is an intentional security property: even if an attacker enables SSH and sets a known password, a power cycle restores the locked-down state. For persistent SSH access during development, disable OverlayFS first via `sudo raspi-config`.

> [!WARNING]
> **Module installation and updates** (`uv pip install`) write to the venv directory on the OverlayFS upper layer. These changes are **lost on reboot**. To permanently install modules, temporarily disable OverlayFS, perform the installation, and re-enable it. A future release may automate this via the admin UI.
