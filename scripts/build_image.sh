#!/bin/bash
# Automated Golden Image Builder for MirrorDash
# Builds securely via systemd-nspawn and shrinks to minimal size.

set -euo pipefail

# --- Configuration ---
RPI_OS_URL_BASE="https://downloads.raspberrypi.com/raspios_lite_arm64/images/"
BUILD_DIR="${1:-$(pwd)/build_workspace}"
if [[ "$BUILD_DIR" != /* ]]; then BUILD_DIR="$(pwd)/$BUILD_DIR"; fi
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
        unset LOOP_DEV
    fi
}
trap cleanup EXIT ERR INT TERM

# --- Download & Decompress ---
echo -e "\e[34m[INFO] Fetching Raspberry Pi OS...\e[0m"
mkdir -p "$BUILD_DIR" "$MOUNT_DIR"
cd "$BUILD_DIR"

LATEST_DIR=$(curl -sSL "$RPI_OS_URL_BASE" | grep -oP 'href="\Kraspios_lite_arm64-[^/]+' | tail -n 1)
LATEST_URL="${RPI_OS_URL_BASE}${LATEST_DIR}/"
IMAGE_NAME=$(curl -sSL "$LATEST_URL" | grep -oP 'href="\K\d{4}-\d{2}-\d{2}-raspios-[^"]+\.img\.xz' | head -n 1)

if [ ! -f "$IMAGE_NAME" ]; then
    wget -q "${LATEST_URL}${IMAGE_NAME}"
    xz -d -c "$IMAGE_NAME" > "${IMAGE_NAME%.xz}"
fi
cp "${IMAGE_NAME%.xz}" "$FINAL_IMAGE"

# --- Expand OS Partition for Build ---
echo -e "\e[34m[INFO] Expanding rootfs for package installation...\e[0m"
# Vi lägger bara till 2GB (p3 skapas inte här, utan på första booten av Pi:en)
truncate -s +2G "$FINAL_IMAGE"
parted -s "$FINAL_IMAGE" resizepart 2 100%

LOOP_DEV=$(losetup -Pf --show "$FINAL_IMAGE")
partprobe "$LOOP_DEV" || true

echo -e "\e[34m[INFO] Waiting for loop device...\e[0m"
for i in {1..15}; do
    if [ -b "${LOOP_DEV}p2" ]; then break; fi
    sleep 1
    if [ "$i" -eq 15 ]; then exit 1; fi
done

e2fsck -f -y "${LOOP_DEV}p2"
resize2fs "${LOOP_DEV}p2"

# --- Mount & Chroot ---
echo -e "\e[34m[INFO] Mounting and copying repository...\e[0m"
mount "${LOOP_DEV}p2" "$MOUNT_DIR"
mount "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"

mkdir -p "$MOUNT_DIR/opt/MirrorDash"
find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -not -name ".*" -not -name "$(basename "$BUILD_DIR")" -exec cp -a -t "$MOUNT_DIR/opt/MirrorDash/" {} +

echo -e "\e[34m[INFO] Executing setup_appliance.sh via systemd-nspawn...\e[0m"
systemd-nspawn --setenv=BUILDING_IMAGE=1 --bind-ro=/etc/resolv.conf -D "$MOUNT_DIR" /bin/bash -c "cd /opt/MirrorDash/scripts && bash ./setup_appliance.sh"

# --- Unmount & Shrink ---
echo -e "\e[34m[INFO] Setup complete. Unmounting...\e[0m"
cleanup
trap - EXIT ERR INT TERM

echo -e "\e[34m[INFO] Shrinking OS partition with PiShrink...\e[0m"
pishrink.sh "$FINAL_IMAGE"

echo -e "\e[34m[INFO] Compressing final image to XZ...\e[0m"
xz -T0 -6 "$FINAL_IMAGE"
sha256sum "${FINAL_IMAGE}.xz" > "${FINAL_IMAGE}.xz.sha256"
echo -e "\e[32m[SUCCESS] Build Complete!\e[0m"
