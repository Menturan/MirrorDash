#!/bin/bash
# Automated Golden Image Builder for MirrorDash
# Builds securely via systemd-nspawn.

set -euo pipefail

# --- Configuration ---
RPI_OS_URL_BASE="https://downloads.raspberrypi.com/raspios_lite_arm64/images/"
BUILD_DIR="${1:-$(pwd)/build_workspace}"
if [[ "$BUILD_DIR" != /* ]]; then
    BUILD_DIR="$(pwd)/$BUILD_DIR"
fi
REPOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(grep -m 1 '^version = ' "$REPOS_DIR/pyproject.toml" | cut -d'"' -f2 || echo "dev")
FINAL_IMAGE="mirrordash-os-v${VERSION}.img"
MOUNT_DIR="$BUILD_DIR/mnt"

# --- Fail-Safe Cleanup ---
cleanup() {
    echo -e "\n\e[34m[INFO] Cleaning up mounts and loops...\e[0m"
    sync
    umount -R "$MOUNT_DIR" 2>/dev/null || true
    if [ -n "${LOOP_DEV:-}" ]; then
        losetup -d "$LOOP_DEV" 2>/dev/null || true
    fi
}
trap cleanup EXIT ERR INT TERM

# --- Download & Decompress ---
echo -e "\e[34m[INFO] Fetching and verifying Raspberry Pi OS base image...\e[0m"
mkdir -p "$BUILD_DIR" "$MOUNT_DIR"
cd "$BUILD_DIR"

LATEST_DIR=$(curl -sSL "$RPI_OS_URL_BASE" | grep -oP 'href="\Kraspios_lite_arm64-[^/]+' | tail -n 1)
LATEST_URL="${RPI_OS_URL_BASE}${LATEST_DIR}/"
IMAGE_NAME=$(curl -sSL "$LATEST_URL" | grep -oP 'href="\K\d{4}-\d{2}-\d{2}-raspios-[^"]+\.img\.xz' | head -n 1)
DOWNLOAD_URL="${LATEST_URL}${IMAGE_NAME}"

if [ ! -f "$IMAGE_NAME" ]; then
    wget -q "$DOWNLOAD_URL"
    wget -q "${DOWNLOAD_URL}.sha256"
    if ! sha256sum -c "${IMAGE_NAME}.sha256" &>/dev/null; then
        echo -e "\e[31m[ERROR] Checksum failed!\e[0m"
        rm -f "$IMAGE_NAME"
        exit 1
    fi
fi

IMAGE_FILE="${IMAGE_NAME%.xz}"
if [ ! -f "$IMAGE_FILE" ]; then
    echo -e "\e[34m[INFO] Decompressing base image...\e[0m"
    xz -d -c "$IMAGE_NAME" > "$IMAGE_FILE"
fi

cp "$IMAGE_FILE" "$FINAL_IMAGE"

# --- Partitioning ---
echo -e "\e[34m[INFO] Expanding image and preparing partitions...\e[0m"
truncate -s +6G "$FINAL_IMAGE"
parted -s "$FINAL_IMAGE" resizepart 2 6GB
parted -s "$FINAL_IMAGE" mkpart primary ext4 6GB 100%

LOOP_DEV=$(losetup -Pf --show "$FINAL_IMAGE")
partprobe "$LOOP_DEV" || true
sleep 2

e2fsck -f -y "${LOOP_DEV}p2"
resize2fs "${LOOP_DEV}p2"
mkfs.ext4 -F -L mirrordash-data "${LOOP_DEV}p3"

# --- Mounting & Setup ---
echo -e "\e[34m[INFO] Mounting filesystems securely...\e[0m"
mount "${LOOP_DEV}p2" "$MOUNT_DIR"
mount "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"
mkdir -p "$MOUNT_DIR/storage"
mount "${LOOP_DEV}p3" "$MOUNT_DIR/storage"

echo -e "\e[34m[INFO] Copying repository to image...\e[0m"
mkdir -p "$MOUNT_DIR/opt/MirrorDash"
find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -not -name ".*" -not -name "$(basename "$BUILD_DIR")" -exec cp -a -t "$MOUNT_DIR/opt/MirrorDash/" {} +

echo -e "\e[34m[INFO] Running setup_appliance.sh via systemd-nspawn...\e[0m"
# systemd-nspawn automatically handles /dev, /proc, /sys and network securely!
systemd-nspawn -D "$MOUNT_DIR" --bind-ro=/etc/resolv.conf /bin/bash -c "cd /opt/MirrorDash/scripts && bash ./setup_appliance.sh"

echo -e "\e[34m[INFO] Build successful. Unmounting securely before compression...\e[0m"
sync
umount -R "$MOUNT_DIR" 2>/dev/null || true
if [ -n "${LOOP_DEV:-}" ]; then
    losetup -d "$LOOP_DEV" 2>/dev/null || true
    unset LOOP_DEV
fi

echo -e "\e[34m[INFO] Shrinking the final image with PiShrink...\e[0m"
pishrink.sh "$FINAL_IMAGE"

echo -e "\e[34m[INFO] Compressing with XZ (using all CPU cores)...\e[0m"
rm -f "${FINAL_IMAGE}.xz" "${FINAL_IMAGE}.xz.sha256"
xz -T0 -6 "$FINAL_IMAGE"

echo -e "\e[34m[INFO] Generating SHA256 checksum...\e[0m"
sha256sum "${FINAL_IMAGE}.xz" > "${FINAL_IMAGE}.xz.sha256"

echo -e "\e[32m[SUCCESS] Image is ready: ${FINAL_IMAGE}.xz\e[0m"
