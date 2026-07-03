# MirrorDash Golden Image Construction Blueprint

This guide details the step-by-step procedure to build, configure, harden, and compress the production **MirrorDash** operating system image ("The Appliance") for deployment on Raspberry Pi hardware (Zero 2 W, Pi 3, Pi 4, Pi 5).

> [!IMPORTANT]
> This guide targets **Raspberry Pi OS Lite (64-bit)** based on **Debian Trixie** (Debian 13). Trixie introduced significant changes from Bookworm: Wayland/labwc replaces X11/Openbox as the default display stack, NetworkManager with Netplan replaces dhcpcd, cloud-init replaces firstrun.sh, systemd-journald is volatile by default, and passwordless sudo is disabled by default. Every section of this document accounts for these changes.

## Table of Contents

- [Build Tracks: Automated vs Manual](#build-tracks-automated-vs-manual)
- [Track A: Automated Build (Recommended)](#track-a-automated-build-recommended)
- [Track B: Manual Build (Reference Guide)](#track-b-manual-build-reference-guide)
- [1. Operating System Initialization](#1-operating-system-initialization)
- [2. System Configuration & Package Installation](#2-system-configuration--package-installation)
- [3. Environment & Application Setup](#3-environment--application-setup)
- [4. Hardware Hardening & Boot Splashes](#4-hardware-hardening--boot-splashes)
- [5. Captive Portal (WiFi-Fallback State Machine)](#5-captive-portal-wifi-fallback-state-machine)
- [6. MirrorDash Core Service Daemon](#6-mirrordash-core-service-daemon)
- [7. Failsafe Locking (OverlayFS) & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)
- [Appendix A: Persistence Model](#appendix-a-persistence-model)

---

## Build Tracks: Automated vs Manual

This document provides **two build tracks** for producing the MirrorDash Golden Image:

| | **Track A: Automated** | **Track B: Manual** |
|:---|:---|:---|
| **What it is** | GitHub Actions workflow building on real ARM64 hardware | Step-by-step guide for building directly on a Pi or ARM workstation |
| **When to use** | **Primary/release path** — every OS image release should use this | **Reference and fallback** — for offline builds, debugging, or understanding the internals |
| **QEMU** | Not used (real ARM runners) | Not used (native ARM only) |
| **Output** | `.img.gz` + `.sha256` uploaded as GitHub Release assets | `.img.gz` on local disk, ready for flashing |
| **Start here** | [Track A ↓](#track-a-automated-build-recommended) | [Track B ↓](#track-b-manual-build-reference-guide) |

> [!TIP]
> **For releases and production builds, use Track A (Automated).** Track B exists so you can inspect exactly what the automated pipeline does under the hood, build offline without GitHub, or debug image issues by understanding each step individually.

---

## Track A: Automated Build (Recommended)

The complete production-ready SD card image is built automatically on real ARM hardware via GitHub Actions whenever a tag matching `v*-os*` (e.g. `v0.2.4-os1`) is pushed. This eliminates QEMU emulation bugs and produces a locked, compressed `.img.gz` ready for flashing.

### Triggering a Build

1. Ensure the Core App version is already bumped and the `vX.Y.Z` release exists (the OS image tracks the Core App version).
2. Create a new GitHub Release with a tag like `v0.2.4-os1`.
3. The **Build OS Image** workflow starts automatically on `ubuntu-24.04-arm64` runners.

### What the Workflow Does

1. **Free disk space** on the runner (`EisBear/free-disk-space-ubuntu-runners@v1`).
2. **Checkout** the repository.
3. **Install minimal dependencies**: `parted`, `xz-utils`, `e2fsprogs`, `pigz`, `wget`, `curl`, plus `pishrink.sh`.
4. **Run `scripts/build_image.sh`** natively on ARM — no emulation. Produces `build_workspace/mirrordash-os-vX.Y.Z.img.gz` + `.sha256`.
5. **Upload** both files as GitHub Release assets.

### Requirements

- A GitHub repository with Actions enabled.
- The `build-os-image.yml` workflow file present in `.github/workflows/`.
- No local workstation dependencies needed — everything runs in the cloud.

---

## Track B: Manual Build (Reference Guide)

The sections below (1–7) document the full manual build process for building the image directly on ARM hardware. Use this track when:

- Building offline (no GitHub access)
- Debugging image issues step-by-step
- Understanding what the automated pipeline does internally

> [!IMPORTANT]
> **QEMU emulation is no longer supported.** These manual steps require a native ARM workstation (e.g. Raspberry Pi running Ubuntu, or an Ubuntu ARM server). Do not attempt on x86_64.

### Prerequisites for Manual Build

```bash
sudo bash scripts/build_image.sh [/path/to/large/drive]
```

**Requirements:**
- ARM Linux workstation (aarch64)
- Host dependencies: `parted`, `xz-utils`, `e2fsprogs`, `pigz`, `wget`, `curl`
- Root (`sudo`) privileges to mount loop devices

The script runs `setup_appliance.sh` inside the mounted image to apply all the steps documented in Sections 1–6 automatically. Proceed to **[Section 7: Failsafe Locking & Image Finalization](#7-failsafe-locking-overlayfs--image-finalization)** after the script completes, or read the manual sections below to understand each step in detail.

> [!NOTE]
> **Automated vs. Manual Service Enablement:**
> In the automated `setup_appliance.sh` script (run inside systemd-nspawn during the image build), all service enablement commands use `systemctl --root=/ enable <service>` instead of a plain `systemctl enable`. This enables the services in offline mode directly on the filesystem root without needing systemd running as PID 1 or D-Bus being online, which avoids container build failures while guaranteeing that `nginx`, `avahi-daemon`, `seatd`, and MirrorDash kiosk services start automatically on the booted Pi.

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

### 1.4 Dynamic Partition Expansion & Storage Layout Setup

Instead of resizing partitions manually, MirrorDash utilizes a custom, deterministic systemd service (**`mirrordash-repart.service`**) executing **`parted`** on early boot (during sysinit) to create partition 3 (`mirrordash-data`) occupying the remaining disk space.

1. **Patch OS firstboot to skip manual resize**:
   Prevent the Raspberry Pi OS `firstboot` script from resizing the root partition to 100% (allowing space for the data partition):
   ```bash
   if [ -f /usr/lib/raspberrypi-sys-mods/firstboot ]; then
     sudo sed -i '2i do_resize() { return 0; }' /usr/lib/raspberrypi-sys-mods/firstboot
   fi
   ```

2. **Configure MBR Partition expander script**:
   Write the partitioning logic to `/usr/local/bin/mirrordash-repart.sh`:
   ```bash
   sudo tee /usr/local/bin/mirrordash-repart.sh << 'EOF'
   #!/bin/bash
   set -euo pipefail
   ROOT_PART=$(findmnt -n -o SOURCE /)
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
       END_SECTOR=$(parted -s "$DISK" unit s print | awk '/^[[:space:]]*2/ {print $3}' | tr -d 's')
       if [ -z "$END_SECTOR" ]; then
           exit 1
       fi
       START_SECTOR=$((END_SECTOR + 1))
       parted -s "$DISK" -- align optimal mkpart primary ext4 "${START_SECTOR}s" 100%
       partprobe "$DISK"
       udevadm settle
       if [ -b "$TARGET_PART" ]; then
           mkfs.ext4 -F -L mirrordash-data "$TARGET_PART"
       else
           exit 1
       fi
   fi
   EOF
   sudo chmod +x /usr/local/bin/mirrordash-repart.sh
   ```

3. **Configure the repart service**:
   Create the systemd service `/etc/systemd/system/mirrordash-repart.service`:
   ```bash
   sudo tee /etc/systemd/system/mirrordash-repart.service << 'EOF'
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
   sudo systemctl enable mirrordash-repart.service
   ```

4. **Symlink NetworkManager System Connections**:
   Redirect the NetworkManager connection directory to the persistent partition so that Wi-Fi connection configurations survive OverlayFS:
   ```bash
   sudo rm -rf /etc/NetworkManager/system-connections
   sudo ln -s /storage/mirrordash/system-connections /etc/NetworkManager/system-connections
   ```

### 1.5 Update `/etc/fstab` & Initialize Mounts

Update `/etc/fstab` to mount the `mirrordash-data` partition automatically on boot.

```bash
# Create mount point and register persistent partition in /etc/fstab
sudo mkdir -p /storage
sudo tee -a /etc/fstab << 'EOF'

# --- MirrorDash Persistent Storage ---
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,nofail,x-systemd.device-timeout=15s  0  2
EOF
```

If you are running the setup on a live Pi, you can trigger partition generation and mount the directory immediately:
```bash
# Run the repart script manually and mount /storage
sudo /usr/local/bin/mirrordash-repart.sh
sudo systemctl daemon-reload
sudo mount /storage
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
    seatd \
    cog \
    wlr-randr \
    avahi-daemon \
    nginx \
    plymouth \
    pix-plym-splash \
    parted \
    python3 \
    git && \
sudo apt autoclean -y && sudo apt autoremove -y
```

**Package rationale:**

| Package | Purpose |
|---------|---------|
| `labwc` | Minimal wlroots-based Wayland compositor (~5 MB RSS). Replaces Xorg + Openbox. |
| `seatd` | Seat management daemon required for running Wayland compositors (like labwc) as non-root users. |
| `cog` | Minimal, high-performance WebKit-based browser for embedded/kiosk systems. Runs natively under Wayland. |
| `wlr-randr` | Display output control (rotation, resolution, power on/off) under Wayland. Replaces `xrandr`. |
| `avahi-daemon` | mDNS/DNS-SD responder (Bonjour/Zeroconf). Advertises the device as `mirrordash.local` on the local network so users never need to type an IP address. |
| `nginx` | Lightweight reverse proxy. Listens on port 80 so the mirror is reachable at `http://mirrordash.local` with no port number, then forwards traffic to uvicorn on `localhost:8000`. |
| `plymouth` | Boot animation manager used to render the startup splash screen. |
| `pix-plym-splash` | The Raspberry Pi-specific "pix" desktop Plymouth theme package required for the customized startup splash screen. |
| `parted` | Partition manipulation tool. Required to expand the root and data partitions early on boot. |
| `python3` | Python 3 runtime interpreter. Required for running transparent cursor generation and local scripts. |
| `git` | Distributed version control system. Required by `uv` to pull and install modules directly from GitHub. |

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

### 2.4 Console Auto-Login & Kiosk Autostart Setup (Wayland & Cog Kiosk Services)

Configure the system to boot to the graphical target, set up user credentials for headless boot, and define the systemd kiosk services to launch the **labwc** Wayland compositor and the **Cog** WebKit kiosk browser automatically:

```bash
# 1. Set the default system target to graphical
sudo ln -fs /lib/systemd/system/graphical.target /etc/systemd/system/default.target

# 2. Provision headless user credentials for Debian Trixie first-boot
# (Creates 'pi' user with default password 'raspberry')
echo "pi:$(echo 'raspberry' | openssl passwd -6 -stdin)" | sudo tee /boot/firmware/userconf.txt > /dev/null

# 3. Silence MOTD and Last login text on user login
touch /home/pi/.hushlogin

# 4. Configure labwc window manager preferences to disable right-click menus
mkdir -p /home/pi/.config/labwc
cat << 'EOF' > /home/pi/.config/labwc/rc.xml
<?xml version="1.0"?>
<labwc_config>
  <mouse>
    <context name="Root">
      <mousebind button="Right" action="Press">
        <action name="None" />
      </mousebind>
    </context>
  </mouse>
</labwc_config>
EOF

# 5. Hide mouse cursor at compositor level via a transparent X11 cursor theme
# XCURSOR_THEME=empty tells labwc to use a custom theme containing only a
# 1-pixel transparent cursor. This is the correct, deterministic approach:
# no timing dependencies, no extra tools, works from the very first frame.
# 'left_ptr' is the default cursor; all other common cursor types are symlinked
# to it so that no compositor fallback to a system cursor can occur.
mkdir -p /home/pi/.icons/empty/cursors
echo "WGN1chAAAAAAAAEAAQAAAAIA/f8gAAAAHAAAACQAAAACAP3/IAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA=" | base64 -d > /home/pi/.icons/empty/cursors/left_ptr
for c in default pointer hand hand1 hand2 wait watch text xterm cross crosshair help question_arrow; do
  ln -sf left_ptr "/home/pi/.icons/empty/cursors/$c"
done
cat << 'EOF' > /home/pi/.icons/empty/index.theme
[Icon Theme]
Name=empty
EOF
echo "XCURSOR_THEME=empty" > /home/pi/.config/labwc/environment
chown -R pi:pi /home/pi/.config /home/pi/.icons

# 6. Configure seatd permissions for unprivileged Wayland access and enable the service
# (Note: We configure seatd to use the 'video' group since 'pi' is always in 'video' by default, avoiding first-boot group reset issues)
sudo mkdir -p /etc/systemd/system/seatd.service.d
sudo tee /etc/systemd/system/seatd.service.d/group.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/sbin/seatd -g video
EOF
sudo systemctl daemon-reload
sudo systemctl enable seatd.service

# 7. Create the systemd service for Labwc Wayland Kiosk
sudo tee /etc/systemd/system/labwc-kiosk.service << 'EOF'
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
WantedBy=graphical.target
EOF

# 8. Create the systemd service for Cog Kiosk Browser
sudo tee /etc/systemd/system/cog-kiosk.service << 'EOF'
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
ExecStart=/usr/bin/cog -P wl --bg-color=black file:///home/pi/mirrordash/loading.html
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF

# 9. Set up an hourly OS safeguard to purge browser cache from the RAM overlay
sudo tee /etc/cron.hourly/mirrordash-cache-purge << 'EOF'
#!/bin/sh
# Aggressively clear the WebKit browser cache to prevent RAM overlay exhaustion
rm -rf /home/pi/.cache/wpe/* 2>/dev/null || true
rm -rf /home/pi/.cache/cog/* 2>/dev/null || true
EOF
sudo chmod +x /etc/cron.hourly/mirrordash-cache-purge

# 10. Enable the systemd kiosk services and mask the default tty1 getty and autologin services
sudo systemctl enable labwc-kiosk.service cog-kiosk.service
sudo systemctl mask getty@tty1.service autologin@tty1.service
```

> [!NOTE]
> Cog is a lightweight WebKit-based browser designed specifically for embedded kiosk environments. Running both `labwc` and `cog` as systemd services is the canonical DevOps approach. It eliminates fragile terminal startup hooks, captures all crash logs in `journalctl`, and handles automatic service recovery.

### 2.5 Volatile Logging Strategy

Trixie configures `systemd-journald` as **volatile by default** — logs are stored only in RAM (`/run/log/journal`) and are lost on reboot. This is the desired behavior for a production appliance: zero SD card wear from logging, with no additional packages needed.

If persistent logs are needed for debugging during development, they can be temporarily enabled via:

```bash
sudo raspi-config   # Advanced Options → Logging → Persistent
```

---

## 3. Environment & Application Setup

### 3.1 Install uv & Deploy Application

Initialize the deployment working directory, install `uv` (modern Python package manager), configure the persistent A/B virtual environment layout, and deploy the application.

In production, the active virtual environment resides on the persistent `/storage` partition. During installation, we install the **Golden Copy** (`base_venv`) directly onto the read-only root partition. On the first boot, a storage hydration daemon automatically initializes the active writeable virtual environment (`venv_a`) by copying `base_venv`.

```bash
# 1. Download and install uv binary globally
sudo curl -sSLf https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz | sudo tar -xz -C /usr/local/bin --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
sudo chmod +x /usr/local/bin/uv

# 2. Create the application directory
mkdir -p /home/pi/mirrordash

# 3. Setup symlinks to the persistent partition
# Note: ~/.mirrordash is a symlink pointing to /storage/mirrordash/data
ln -sfT /storage/mirrordash/venv /home/pi/mirrordash/.venv
ln -sfT /storage/mirrordash/data /home/pi/.mirrordash

# 4. Create base_venv (Golden Copy) on the root filesystem
cd /home/pi/mirrordash
rm -rf base_venv
uv venv --allow-existing --python 3.14 base_venv

# 5. Install mirrordash and mirrordash-clock from PyPI into the Golden Copy
uv pip install --python base_venv mirrordash mirrordash-clock

# 6. Download the launcher script and loading HTML page
curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/scripts/launch.sh -o /home/pi/mirrordash/launch.sh
curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/loading.html -o /home/pi/mirrordash/loading.html
chmod +x /home/pi/mirrordash/launch.sh

# 7. Ensure correct file ownership for kiosk user
sudo chown -R pi:pi /home/pi/mirrordash
```

### 3.1b First-Boot Storage Hydration Setup

To ensure the persistent storage partition is properly initialized with a functional virtual environment on the first boot, configure the storage hydration service:

1. **Write hydration script**:
   Create `/usr/local/bin/mirrordash-hydrate.sh` to copy `base_venv` to the persistent `/storage/mirrordash/venv_a` directory on boot if it is missing:
   ```bash
   sudo tee /usr/local/bin/mirrordash-hydrate.sh << 'EOF'
   #!/bin/bash
   set -euo pipefail

   # Ensure parent directory and subdirectories exist on mounted /storage
   mkdir -p /storage/mirrordash/data
   mkdir -p /storage/mirrordash/system-connections
   chown -R pi:pi /storage/mirrordash
   chown root:root /storage/mirrordash/system-connections
   chmod 700 /storage/mirrordash/system-connections

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
   sudo chmod +x /usr/local/bin/mirrordash-hydrate.sh
   ```

2. **Configure systemd storage hydration service**:
   Define and enable `/etc/systemd/system/mirrordash-storage-init.service` (ordered before NetworkManager so connections exist prior to startup):
   ```bash
   sudo tee /etc/systemd/system/mirrordash-storage-init.service << 'EOF'
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
   sudo systemctl enable mirrordash-storage-init.service
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

# 2. Append visual boot suppression and Bluetooth disabling to config.txt
sudo tee -a /boot/firmware/config.txt << 'EOF'

# --- MirrorDash Hardware Hardening ---
disable_splash=1
boot_delay=0
dtoverlay=disable-bt
EOF

# 3. Silence kernel log prints and redirect console to tty3 in cmdline.txt
sudo sed -i 's/console=tty1/console=tty3/g' /boot/firmware/cmdline.txt
for opt in "loglevel=0" "quiet" "splash" "systemd.show_status=false" "vt.global_cursor_default=0" "plymouth.ignore-serial-consoles" "logo.nologo"; do
  if ! grep -q "$opt" /boot/firmware/cmdline.txt; then
    sudo sed -i "1s/$/ $opt/" /boot/firmware/cmdline.txt
  fi
done

# 4. Create custom 'mirrordash' Plymouth theme, patch for separate shutdown splash, and set theme
sudo mkdir -p /usr/share/plymouth/themes/mirrordash
sudo cp -r /usr/share/plymouth/themes/pix/* /usr/share/plymouth/themes/mirrordash/ || true

if [ -f /usr/share/plymouth/themes/mirrordash/pix.plymouth ]; then
  sudo mv /usr/share/plymouth/themes/mirrordash/pix.plymouth /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
  sudo sed -i 's/Name=Raspberry Pi/Name=MirrorDash/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
  sudo sed -i 's/\/usr\/share\/plymouth\/themes\/pix/\/usr\/share\/plymouth\/themes\/mirrordash/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
  sudo sed -i 's/pix\.script/mirrordash.script/g' /usr/share/plymouth/themes/mirrordash/mirrordash.plymouth
  sudo mv /usr/share/plymouth/themes/mirrordash/pix.script /usr/share/plymouth/themes/mirrordash/mirrordash.script
fi

# Apply clean script modifications to comments and separate shutdown splash image
if [ -f /usr/share/plymouth/themes/mirrordash/mirrordash.script ]; then
  sudo sed -i 's/^[[:space:]]*Plymouth\.SetMessageFunction/# Plymouth.SetMessageFunction/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
  sudo sed -i 's/^[[:space:]]*Plymouth\.SetUpdateStatusFunction/# Plymouth.SetUpdateStatusFunction/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
  
  if ! grep -q 'Plymouth\.GetMode() == "shutdown"' /usr/share/plymouth/themes/mirrordash/mirrordash.script; then
    sudo sed -i -E 's/([a-zA-Z0-9_]+)[[:space:]]*=[[:space:]]*Image[[:space:]]*\("splash.png"\);/if (Plymouth.GetMode() == "shutdown") { \1 = Image("shutdown.png"); } else { \1 = Image("splash.png"); }/g' /usr/share/plymouth/themes/mirrordash/mirrordash.script
  fi
fi

# Download/write custom splash and shutdown splash assets
sudo curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/splash.png -o /usr/share/plymouth/themes/mirrordash/splash.png
sudo curl -sSLf https://raw.githubusercontent.com/Menturan/MirrorDash/master/mirrordash_core/static/shutdown.png -o /usr/share/plymouth/themes/mirrordash/shutdown.png

# Register the new default theme
sudo plymouth-set-default-theme mirrordash

# 5. Enable timezone NTP sync wait service
sudo systemctl enable systemd-time-wait-sync.service

# 6. Rebuild initramfs to apply changes
sudo update-initramfs -u
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
nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode AP
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
sudo chmod +x /usr/local/bin/mirrordash-wifi-check.sh

# 2. Write and enable the systemd fallback service
sudo tee /etc/systemd/system/mirrordash-wifi-fallback.service << 'EOF'
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

# 2. Enable the daemon
sudo systemctl enable mirrordash.service
```

> [!NOTE]
> `Requires=mirrordash-storage-init.service` and `After=mirrordash-storage-init.service` ensure the virtual environment is fully hydrated and symlinks are set up before starting the application. `RequiresMountsFor=` ensures the persistent data partition is mounted. `Environment="WAYLAND_DISPLAY=wayland-0"` matches the compositor service.

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

# Verify MirrorDash services are enabled and getty is masked
sudo systemctl is-enabled mirrordash-storage-init.service
sudo systemctl is-enabled labwc-kiosk.service cog-kiosk.service
sudo systemctl is-enabled mirrordash.service
sudo systemctl is-enabled mirrordash-wifi-fallback.service
sudo systemctl is-enabled systemd-time-wait-sync.service
sudo systemctl is-enabled getty@tty1.service # Should output 'masked'

# Verify MirrorDash starts correctly
sudo systemctl start mirrordash.service
curl -s http://localhost:8000/health

# Verify sudoers configuration (should not prompt for a password)
sudo -n mount -o remount,rw /   # Should succeed without password prompt
sudo -n mount -o remount,ro /   # May fail with "mount point is busy" on a live system, but must not prompt for a password
```

### 7.3 Lock Root & Finalize (Manual)

Perform a final system cleanup (prune package caches, clear temporary files, truncate logs, and strip command history), purge your development Wi-Fi connection profiles, disable the SSH service, set the default base system timezone to UTC, enable OverlayFS, and reboot the Pi in one clean execution sequence:

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

# 3. Clean up all wireless networks failsafely, enable OverlayFS, and reboot
# Note: These commands are chained with '&&' in a single sequence so they run to completion even after your Wi-Fi/SSH connection drops.
for uuid in $(nmcli --fields UUID,TYPE connection show | awk '$2 ~ /wifi|802-11-wireless/ {print $1}'); do sudo nmcli connection delete "$uuid" 2>/dev/null || true; done && \
sudo rm -rf /etc/NetworkManager/system-connections/* && \
sudo raspi-config nonint enable_overlayfs && \
sync && \
sudo reboot
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
| Application config | `~/.mirrordash/data/config.json` | Persistent partition (via symlink) | ✅ Yes |
| Module persistent data | `~/.mirrordash/data/<module>/` | Persistent partition (via symlink) | ✅ Yes |
| Module cache | `~/.mirrordash/cache/<module>/` | Persistent partition (via symlink) | ✅ Yes |
| System logs | `/run/log/journal/` | RAM (volatile journald) | ❌ No |
| Installed packages | `/storage/mirrordash/venv` | Persistent partition | ✅ Yes |
| SSH toggle state | `/etc/systemd/system/` | Saved in `config.json` & re-applied at boot | ✅ Yes |
| System password | `/etc/shadow` | Saved in `pi_password.hash` & re-applied at boot | ✅ Yes |
| Timezone | `/etc/localtime` | Saved in `config.json` & re-applied at boot | ✅ Yes |

> [!IMPORTANT]
> **A/B updates and settings persistence** are fully automated. SSH toggle states, the user password hash, and the selected timezone are persistently stored on the `/storage` partition and dynamically applied at boot by the `module_loader` system service.
> 
> **Failsafe Rollback System**: If an update or module installation corrupts the virtual environment and causes startup crashes, the launcher (`launch.sh`) automatically rolls back to the stable copy (`venv_old`) or boots from the read-only Golden Copy (`base_venv` in Safe Mode) and displays warning alerts in the Admin and Kiosk UIs.
