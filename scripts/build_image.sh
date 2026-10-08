#!/bin/bash
# Automated Golden Image Builder for MirrorDash
# Builds securely via systemd-nspawn and shrinks to minimal size.

set -euo pipefail


BUILD_DIR="${1:-$(pwd)/build_workspace}"
if [[ "$BUILD_DIR" != /* ]]; then BUILD_DIR="$(pwd)/$BUILD_DIR"; fi
REPOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$REPOS_DIR/pyproject.toml")
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
DOWNLOAD_DIR="$BUILD_DIR/downloads"
mkdir -p "$BUILD_DIR" "$MOUNT_DIR" "$DOWNLOAD_DIR"
cd "$BUILD_DIR"

# Pinned base image so a given tag always builds the same OS. Bump deliberately
# (current: curl -sLI -o /dev/null -w '%{url_effective}' https://downloads.raspberrypi.org/raspios_lite_arm64_latest).
# Bump both together; the checksum is in "$TARGET_URL.sha256". A BASE_IMAGE_URL override must bring its own.
TARGET_URL="${BASE_IMAGE_URL:-https://downloads.raspberrypi.org/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz}"
TARGET_SHA256="${BASE_IMAGE_SHA256:-483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843}"
IMAGE_NAME=$(basename "$TARGET_URL")

if [ ! -f "${IMAGE_NAME%.xz}" ]; then
    if [ ! -f "$DOWNLOAD_DIR/$IMAGE_NAME" ]; then
        echo -e "\e[34m[INFO] Downloading base image...\e[0m"
        wget -q -c -P "$DOWNLOAD_DIR" "$TARGET_URL"
    fi
    # A broken or changed download must not end up in the image (or in the CI cache)
    if ! echo "$TARGET_SHA256  $DOWNLOAD_DIR/$IMAGE_NAME" | sha256sum -c --quiet; then
        rm -f "$DOWNLOAD_DIR/$IMAGE_NAME"
        echo -e "\e[31m[ERROR] The base image doesn't match its checksum; it was deleted. Run the build again.\e[0m"
        exit 1
    fi
    echo -e "\e[34m[INFO] Decompressing base image...\e[0m"
    xz -d -c "$DOWNLOAD_DIR/$IMAGE_NAME" > "${IMAGE_NAME%.xz}"
fi
cp "${IMAGE_NAME%.xz}" "$FINAL_IMAGE"

# --- Expand OS Partition for Build ---
echo -e "\e[34m[INFO] Expanding rootfs for package installation...\e[0m"
# Vi lägger bara till 2GB (p3 skapas inte här, utan på första booten av Pi:en)
truncate -s +2G "$FINAL_IMAGE"
parted -s "$FINAL_IMAGE" resizepart 2 100%

# -P has the kernel read the partition table (already final: resized above, before attaching)
LOOP_DEV=$(losetup -Pf --show "$FINAL_IMAGE")
echo -e "\e[34m[INFO] Waiting for loop device...\e[0m"
udevadm settle

e2fsck -f -y "${LOOP_DEV}p2"
resize2fs "${LOOP_DEV}p2"

# --- Mount & Chroot ---
echo -e "\e[34m[INFO] Mounting and copying repository...\e[0m"
mount "${LOOP_DEV}p2" "$MOUNT_DIR"
mount "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"

mkdir -p "$MOUNT_DIR/opt/MirrorDash"
find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -not -name ".*" -not -name "$(basename "$BUILD_DIR")" -exec cp -a -t "$MOUNT_DIR/opt/MirrorDash/" {} +

echo -e "\e[34m[INFO] Executing setup_appliance.sh via systemd-nspawn...\e[0m"
systemd-nspawn --setenv=BUILDING_IMAGE=1 --resolv-conf=copy-host -D "$MOUNT_DIR" /bin/bash -c "cd /opt/MirrorDash/scripts && bash ./setup_appliance.sh"

# The repo copy is only build input — never ship source or the dev config.json
rm -rf "$MOUNT_DIR/opt/MirrorDash"

# The read-only root needs overlayroot inside the initramfs; without it the first-boot lock sets
# overlayroot=tmpfs and nothing happens, and the SD card stays writable. Never ship that.
# The list is read in full first: `| grep -q` stops reading at the first match, the container is
# killed for it, and with pipefail that counted as a failure even when overlayroot was there.
initramfs_files=$(systemd-nspawn -q -D "$MOUNT_DIR" lsinitramfs /boot/firmware/initramfs8) || true
if ! grep -q overlayroot <<< "$initramfs_files"; then
    echo -e "\e[31m[ERROR] overlayroot is missing from the initramfs: the image would never become read-only.\e[0m"
    exit 1
fi
if ! grep -q plymouth/themes/mirrordash/mirrordash.script <<< "$initramfs_files"; then
    echo -e "\e[31m[ERROR] The MirrorDash boot theme is missing from the initramfs.\e[0m"
    exit 1
fi

# Without a Wi-Fi country the radio stays blocked: no hotspot, no way to set the mirror up.
if ! grep -q 'cfg80211.ieee80211_regdom=' "$MOUNT_DIR/boot/firmware/cmdline.txt"; then
    echo -e "\e[31m[ERROR] No Wi-Fi country in cmdline.txt: the mirror couldn't start its setup hotspot.\e[0m"
    exit 1
fi

# HDMI brightness goes through ddcutil; without it the brightness setting does nothing on HDMI.
if ! systemd-nspawn -q -D "$MOUNT_DIR" test -x /usr/bin/ddcutil; then
    echo -e "\e[31m[ERROR] ddcutil is missing: brightness would not work on HDMI screens.\e[0m"
    exit 1
fi

# --- Unmount & Shrink ---
echo -e "\e[34m[INFO] Setup complete. Unmounting...\e[0m"
cleanup
trap - EXIT ERR INT TERM

echo -e "\e[34m[INFO] Shrinking OS partition with PiShrink...\e[0m"
pishrink.sh -s "$FINAL_IMAGE"

echo -e "\e[34m[INFO] Compressing final image to XZ...\e[0m"
xz -T0 -6 "$FINAL_IMAGE"
sha256sum "${FINAL_IMAGE}.xz" > "${FINAL_IMAGE}.xz.sha256"
echo -e "\e[32m[SUCCESS] Build Complete!\e[0m"
