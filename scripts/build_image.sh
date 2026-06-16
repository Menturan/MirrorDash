#!/bin/bash
# Automated Golden Image Builder for MirrorDash
# Builds on native ARM (GitHub Actions ubuntu-24.04-arm64) or any Debian/Ubuntu ARM workstation.

set -e

# --- Configuration ---
RPI_OS_URL_BASE="https://downloads.raspberrypi.com/raspios_lite_arm64/images/"
BUILD_DIR="${1:-$(pwd)/build_workspace}"
if [[ "$BUILD_DIR" != /* ]]; then
    BUILD_DIR="$(pwd)/$BUILD_DIR"
fi
REPOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(grep -m 1 '^version = ' "$REPOS_DIR/pyproject.toml" | cut -d'"' -f2)
if [ -n "$VERSION" ]; then
    FINAL_IMAGE="mirrordash-os-v${VERSION}.img"
else
    FINAL_IMAGE="mirrordash-os-final.img"
fi
MOUNT_DIR="$BUILD_DIR/mnt"

# --- Functions ---
error_exit() {
    echo -e "\e[31m[ERROR] $1\e[0m" >&2
    exit 1
}

info() {
    echo -e "\e[34m[INFO] $1\e[0m"
}

run_timed_step() {
    local step_name="$1"
    local step_func="$2"
    info "Starting step: $step_name..."
    local start_time=$SECONDS
    $step_func
    local end_time=$SECONDS
    local duration=$((end_time - start_time))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))
    info "Completed step: $step_name in ${minutes}m ${seconds}s"
}

check_dependencies() {
    info "Checking host dependencies..."
    local deps=("parted" "xz" "losetup" "e2fsck" "resize2fs" "curl" "grep" "wget" "sha256sum" "pigz")
    local missing=()
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &>/dev/null; then
            missing+=("$dep")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        error_exit "Missing dependencies: ${missing[*]}. Please install them (e.g., sudo apt install parted xz-utils e2fsprogs pigz curl wget)."
    fi

    if [ "$EUID" -ne 0 ]; then
        error_exit "This script must be run as root (or with sudo) to mount filesystems and create loop devices."
    fi
}

download_pishrink() {
    if ! command -v pishrink.sh &>/dev/null; then
        info "Downloading pishrink.sh to /usr/local/bin..."
        wget -qO /usr/local/bin/pishrink.sh https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh
        chmod +x /usr/local/bin/pishrink.sh
    fi
}

download_base_image() {
    info "Fetching latest Raspberry Pi OS Lite (64-bit) URL..."

    local DOWNLOADS_DIR="$BUILD_DIR/downloads"
    mkdir -p "$DOWNLOADS_DIR"

    local LATEST_DIR
    LATEST_DIR=$(curl -sSL "$RPI_OS_URL_BASE" | grep -oP 'href="\Kraspios_lite_arm64-[^/]+' | tail -n 1)

    if [ -z "$LATEST_DIR" ]; then
        error_exit "Could not determine latest Raspberry Pi OS directory."
    fi

    local LATEST_URL="${RPI_OS_URL_BASE}${LATEST_DIR}/"
    local IMAGE_NAME
    IMAGE_NAME=$(curl -sSL "$LATEST_URL" | grep -oP 'href="\K\d{4}-\d{2}-\d{2}-raspios-[^"]+\.img\.xz' | head -n 1)

    if [ -z "$IMAGE_NAME" ]; then
        error_exit "Could not find .img.xz file in $LATEST_URL"
    fi

    local DOWNLOAD_URL="${LATEST_URL}${IMAGE_NAME}"
    local IMAGE_FILE="${IMAGE_NAME%.xz}"

    (
        cd "$DOWNLOADS_DIR"

        local NEED_DOWNLOAD=true
        if [ -f "$IMAGE_NAME" ]; then
            info "Found cached base image: $IMAGE_NAME. Verifying checksum..."
            rm -f "${IMAGE_NAME}.sha256"
            wget -q "${DOWNLOAD_URL}.sha256"

            if sha256sum -c "${IMAGE_NAME}.sha256" &>/dev/null; then
                info "Cached image is valid! Skipping download step."
                NEED_DOWNLOAD=false
            else
                info "Cached image is corrupted or outdated. Redownloading..."
                rm -f "$IMAGE_NAME"
            fi
        fi

        if [ "$NEED_DOWNLOAD" = true ]; then
            info "Downloading $DOWNLOAD_URL (will resume if partial)..."
            wget -c "$DOWNLOAD_URL"

            info "Downloading checksum..."
            rm -f "${IMAGE_NAME}.sha256"
            wget -q "${DOWNLOAD_URL}.sha256"

            info "Verifying SHA256 checksum..."
            if ! sha256sum -c "${IMAGE_NAME}.sha256" &>/dev/null; then
                echo "Checksum verification failed! Deleting corrupted download..."
                rm -f "$IMAGE_NAME" "${IMAGE_NAME}.sha256"
                error_exit "SHA256 checksum verification failed for $IMAGE_NAME"
            fi
            info "SHA256 checksum verification passed."
        fi
    )

    (
        cd "$BUILD_DIR"
        if [ ! -f "$IMAGE_FILE" ]; then
            info "Decompressing image from downloads folder..."
            rm -f "${IMAGE_FILE}.tmp"
            xz -d -c "downloads/$IMAGE_NAME" > "${IMAGE_FILE}.tmp"
            mv "${IMAGE_FILE}.tmp" "$IMAGE_FILE"
        else
            info "Decompressed image already exists in workspace."
        fi

        cp "$IMAGE_FILE" "$FINAL_IMAGE"
    )
}

resize_partition() {
    info "Expanding image file to add 6GB headroom..."
    local img_path="$BUILD_DIR/$FINAL_IMAGE"

    truncate -s +6G "$img_path"

    info "Resizing partition table..."
    parted -s "$img_path" resizepart 2 6GB
    parted -s "$img_path" mkpart primary ext4 6GB 100%

    info "Setting up loop device..."
    # -P gör att Linux automatiskt hittar och skapar p1, p2 och p3 åt oss
    LOOP_DEV=$(losetup -Pf --show "$img_path")
    if [ -z "$LOOP_DEV" ]; then
        error_exit "Failed to create loop device."
    fi

    partprobe "$LOOP_DEV" || true
    local max_wait=10
    local wait_count=0
    while [ ! -b "${LOOP_DEV}p2" ]; do
        sleep 1
        wait_count=$((wait_count + 1))
        if [ "$wait_count" -ge "$max_wait" ]; then
            error_exit "Timeout waiting for partition devices on $LOOP_DEV."
        fi
    done

    info "Resizing ext4 filesystem on ${LOOP_DEV}p2..."
    e2fsck -f -y "${LOOP_DEV}p2"
    resize2fs "${LOOP_DEV}p2"

    info "Creating ext4 filesystem on ${LOOP_DEV}p3..."
    mkfs.ext4 -F -L mirrordash-data "${LOOP_DEV}p3"
}

mount_image() {
    info "Mounting image filesystems..."
    mkdir -p "$MOUNT_DIR"

    # Vi använder bara de färdiga loop-partitionerna direkt, helt utan krångel
    mount -o noatime,commit=600 "${LOOP_DEV}p2" "$MOUNT_DIR"
    mount -o noatime "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"
    mkdir -p "$MOUNT_DIR/storage"
    mount -o noatime,commit=600 "${LOOP_DEV}p3" "$MOUNT_DIR/storage"

    info "Binding host virtual filesystems for appliance setup..."
    mount --bind /dev "$MOUNT_DIR/dev"
    mount --bind /sys "$MOUNT_DIR/sys"
    mount --bind /proc "$MOUNT_DIR/proc"
    mount --bind /dev/pts "$MOUNT_DIR/dev/pts"
}

enable_overlayfs() {
    info "Configuring OverlayFS in image..."
    # Ensure initramfs scripts directory exists
    mkdir -p "$MOUNT_DIR/etc/initramfs-tools/scripts"

    # Write the overlay mount script
    cat > "$MOUNT_DIR/etc/initramfs-tools/scripts/overlay" << 'INITSCRIPT'
# Local filesystem mounting                     -*- shell-script -*-
. /scripts/local
local_mount_root()
{
        local_top
        local_device_setup "${ROOT}" "root file system"
        ROOT="${DEV}"
        if [ -z "${ROOTFSTYPE}" ] || [ "${ROOTFSTYPE}" = auto ]; then
                FSTYPE=$(get_fstype "${ROOT}")
        else
                FSTYPE=${ROOTFSTYPE}
        fi
        local_premount
        mkdir /upper /lower
        if [ "${ROOTFSTYPE}" != "unknown" ]; then
                mount ${roflag} -t ${FSTYPE} ${ROOTFLAGS} ${ROOT} /lower
        else
                mount ${roflag} ${ROOTFLAGS} ${ROOT} /lower
        fi
        mount -t tmpfs tmpfs /upper
        mkdir /upper/data /upper/work
        mount -t overlay -o lowerdir=/lower,upperdir=/upper/data,workdir=/upper/work overlay ${rootmnt}
}
INITSCRIPT
    chmod +x "$MOUNT_DIR/etc/initramfs-tools/scripts/overlay"

    # Rebuild initramfs natively (ARM, no QEMU — pigz works correctly)
    chroot "$MOUNT_DIR" /bin/bash -c "update-initramfs -u -k all"

    # Enable overlay boot parameters
    if ! grep -q "boot=overlay" "$MOUNT_DIR/boot/firmware/cmdline.txt" ; then
        sed -i 's/^/boot=overlay /' "$MOUNT_DIR/boot/firmware/cmdline.txt"
    fi
    sed -i -e "s/\(.*\/boot.*\)defaults\(.*\)/\1defaults,ro\2/" "$MOUNT_DIR/etc/fstab"
    if ! grep -q "auto_initramfs=1" "$MOUNT_DIR/boot/firmware/config.txt" ; then
        echo "auto_initramfs=1" >> "$MOUNT_DIR/boot/firmware/config.txt"
    fi
}

setup_storage_offline() {
    info "Performing offline storage setup (bypassing step 1)..."

    info "Ensuring 'pi' user exists in image..."
    chroot "$MOUNT_DIR" /bin/bash -c "if ! id pi &>/dev/null; then useradd -m -s /bin/bash -G sudo,video,render,plugdev,games,users,input,netdev,gpio,i2c,spi pi && echo 'pi:raspberry' | chpasswd; fi"

    mkdir -p "$MOUNT_DIR/storage/mirrordash/data" "$MOUNT_DIR/storage/mirrordash/venv_a" "$MOUNT_DIR/storage/mirrordash/venv_b"
    chroot "$MOUNT_DIR" /bin/bash -c "chown -R pi:pi /storage"

    mkdir -p "$MOUNT_DIR/home/pi/.mirrordash/cache" "$MOUNT_DIR/home/pi/.mirrordash/data"
    chroot "$MOUNT_DIR" /bin/bash -c "chown -R pi:pi /home/pi/.mirrordash"

    if ! grep -q "LABEL=mirrordash-data" "$MOUNT_DIR/etc/fstab"; then
        cat << 'EOF' >> "$MOUNT_DIR/etc/fstab"

# --- MirrorDash Storage ---
# Persistent data partition (survives OverlayFS)
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60,nofail,x-systemd.device-timeout=5  0  2

# Bind-mount persistent data into the application's expected path
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind,nofail,x-systemd.device-timeout=5  0  0

# Volatile module cache in RAM (100 MB)
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF
    fi

    # Route NetworkManager WiFi profiles to the persistent partition so they survive OverlayFS reboots
    if [ ! -L "$MOUNT_DIR/etc/NetworkManager/system-connections" ]; then
      if [ -d "$MOUNT_DIR/etc/NetworkManager/system-connections" ]; then
        mkdir -p "$MOUNT_DIR/storage/mirrordash/system-connections"
        cp -a "$MOUNT_DIR/etc/NetworkManager/system-connections/"* "$MOUNT_DIR/storage/mirrordash/system-connections/" 2>/dev/null || true
        rm -rf "$MOUNT_DIR/etc/NetworkManager/system-connections"
      fi
      mkdir -p "$MOUNT_DIR/storage/mirrordash/system-connections"
      ln -s /storage/mirrordash/system-connections "$MOUNT_DIR/etc/NetworkManager/system-connections"
    fi

    # Create the systemd service file
    cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/mirrordash-expand.service"
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

    # And the script
    cat << 'EOF' > "$MOUNT_DIR/usr/local/bin/mirrordash-expand.sh"
#!/bin/bash
# MirrorDash storage partition auto-expand script
# Runs early on boot to expand partition 3 to fill the rest of the disk.

set -e

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
printf "Yes\nIgnore\n" | parted "$ROOT_DISK" ---pretend-input-tty resizepart 3 100%
partprobe "$ROOT_DISK" || true
udevadm settle || true

echo "Resizing ext4 filesystem on $DATA_PART..."
resize2fs "$DATA_PART"
echo "Storage partition expansion complete."
EOF
    chmod +x "$MOUNT_DIR/usr/local/bin/mirrordash-expand.sh"

    # Enable service via chroot (creates symlinks, does not require systemd to be running)
    chroot "$MOUNT_DIR" /bin/bash -c "systemctl enable mirrordash-expand.service"

    # Mark the expanding_partition step as complete so setup_appliance skips it
    mkdir -p "$MOUNT_DIR/var/lib"
    echo "expanding_partition" > "$MOUNT_DIR/var/lib/mirrordash-setup-state"
}

safe_umount() {
    local target="$1"
    if mountpoint -q "$target" 2>/dev/null; then
        sync
        umount "$target" || umount -l "$target" || true
    fi
}

run_appliance_setup() {
    info "Copying repository to image..."
    mkdir -p "$MOUNT_DIR/opt/MirrorDash"
    find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -not -name ".*" -not -name "build_*" -not -name "$(basename "$BUILD_DIR")" -exec cp -r -t "$MOUNT_DIR/opt/MirrorDash/" {} +

    info "Executing setup_appliance.sh inside image..."
    chroot "$MOUNT_DIR" /bin/bash -c "cd /opt/MirrorDash/scripts && export NONINTERACTIVE=1 && bash ./setup_appliance.sh"

    info "Executing finalize_appliance.sh inside image..."
    sed -i 's/^poweroff/#poweroff disabled in build/g' "$MOUNT_DIR/usr/local/bin/mirrordash-finalize.sh"
    chroot "$MOUNT_DIR" /bin/bash -c "mirrordash-finalize.sh --yes"
}

cleanup_and_unmount() {
    set +e
    info "Cleaning up..."
    if [ -n "$MOUNT_DIR" ] && [ "$MOUNT_DIR" != "/" ] && [ -d "$MOUNT_DIR" ]; then
        rm -rf "$MOUNT_DIR/opt/MirrorDash"

        info "Unmounting filesystems..."
        safe_umount "$MOUNT_DIR/dev/pts"
        safe_umount "$MOUNT_DIR/dev"
        safe_umount "$MOUNT_DIR/sys"
        safe_umount "$MOUNT_DIR/proc"
        safe_umount "$MOUNT_DIR/storage"
        safe_umount "$MOUNT_DIR/boot/firmware"
        safe_umount "$MOUNT_DIR"
    fi

    # Koppla bort loop-enheten i slutet av hela skriptet
    if [ -n "$LOOP_DEV" ]; then
        info "Running zerofree on Root partition to maximize XZ compression..."
        zerofree -v "${LOOP_DEV}p2" || true
        info "Detaching loop device..."
        losetup -d "$LOOP_DEV"
    fi
    set -e
}

shrink_and_compress() {
    info "Shrinking and compressing the final image..."

    rm -f "$BUILD_DIR/${FINAL_IMAGE}.xz" "$BUILD_DIR/${FINAL_IMAGE}.xz.sha256"

    # Kör PiShrink UTAN -z. Vi vill bara krympa partitionen, inte komprimera än.
    pishrink.sh "$BUILD_DIR/$FINAL_IMAGE"

    info "Compressing with XZ (using all 4 CPU cores)..."
    # -T0 säger åt xz att använda alla processorkärnor för maximal hastighet
    # -6 ger en optimal balans mellan extrem komprimering och tidsåtgång
    xz -T0 -6 "$BUILD_DIR/$FINAL_IMAGE"

    info "Generating SHA256 checksum..."
    (
        cd "$BUILD_DIR"
        sha256sum "${FINAL_IMAGE}.xz" > "${FINAL_IMAGE}.xz.sha256"
    )

    info "Build complete. Final image is at $BUILD_DIR/${FINAL_IMAGE}.xz"
    info "SHA256 checksum generated at $BUILD_DIR/${FINAL_IMAGE}.xz.sha256"
}

# --- Main Flow ---
START_TIME_TOTAL=$SECONDS
check_dependencies

trap cleanup_and_unmount EXIT

run_timed_step "Download PiShrink" download_pishrink
run_timed_step "Download Base Image" download_base_image
run_timed_step "Resize Partition" resize_partition
run_timed_step "Mount Image" mount_image
run_timed_step "Enable OverlayFS" enable_overlayfs
run_timed_step "Setup Storage Offline" setup_storage_offline
run_timed_step "Run Appliance Setup" run_appliance_setup

trap - EXIT
cleanup_and_unmount

run_timed_step "Shrink and Compress Image" shrink_and_compress

END_TIME_TOTAL=$SECONDS
DURATION_TOTAL=$((END_TIME_TOTAL - START_TIME_TOTAL))
minutes=$((DURATION_TOTAL / 60))
seconds=$((DURATION_TOTAL % 60))
info "Build script completed in ${minutes}m ${seconds}s."
