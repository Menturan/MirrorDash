#!/bin/bash
# Builds the MirrorDash OS image (and the signed update bundle) from Raspberry Pi OS Lite.
# The base image is set up by configure_image.sh inside systemd-nspawn (a container, so nothing runs
# on the build machine itself). The result is an A/B card layout, see step_ab_updates in configure_image.sh.
# Run as root: sudo bash scripts/build_image.sh [build directory]. Needs RAUC_KEY for the bundle.

set -euo pipefail

info() { echo -e "\e[34m[INFO] $*\e[0m"; }
warn() { echo -e "\e[33m[WARN] $*\e[0m"; }
fail() { echo -e "\e[31m[ERROR] $*\e[0m"; exit 1; }

BUILD_DIR="${1:-$(pwd)/build_workspace}"
if [[ "$BUILD_DIR" != /* ]]; then BUILD_DIR="$(pwd)/$BUILD_DIR"; fi
REPOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$REPOS_DIR/pyproject.toml")
FINAL_IMAGE="mirrordash-os-v${VERSION}.img"
MOUNT_DIR="$BUILD_DIR/mnt"

# --- Fail-Safe Cleanup ---
cleanup() {
    echo; info "Cleaning up mounts and loops..."
    sync
    umount -R "$MOUNT_DIR" 2>/dev/null || true
    if [ -n "${PIECES_DIR:-}" ]; then umount "$PIECES_DIR/mnt" 2>/dev/null || true; fi
    if [ -n "${LOOP_DEV:-}" ]; then
        losetup -d "$LOOP_DEV" 2>/dev/null || true
        unset LOOP_DEV
    fi
}
trap cleanup EXIT ERR INT TERM

# --- Download & Decompress ---
info "Fetching Raspberry Pi OS..."
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
        info "Downloading base image..."
        wget -q -c -P "$DOWNLOAD_DIR" "$TARGET_URL"
    fi
    # A broken or changed download must not end up in the image (or in the CI cache)
    if ! echo "$TARGET_SHA256  $DOWNLOAD_DIR/$IMAGE_NAME" | sha256sum -c --quiet; then
        rm -f "$DOWNLOAD_DIR/$IMAGE_NAME"
        fail "The base image doesn't match its checksum; it was deleted. Run the build again."
    fi
    info "Decompressing base image..."
    xz -d -c "$DOWNLOAD_DIR/$IMAGE_NAME" > "${IMAGE_NAME%.xz}"
fi
cp "${IMAGE_NAME%.xz}" "$FINAL_IMAGE"

# --- Expand OS Partition for Build ---
info "Expanding rootfs for package installation..."
# 2 GB more room to install packages into. Only for the build: the root is packed into a
# squashfs at the end, and the final image is put together from scratch.
truncate -s +2G "$FINAL_IMAGE"
parted -s "$FINAL_IMAGE" resizepart 2 100%

# -P has the kernel read the partition table (already final: resized above, before attaching)
LOOP_DEV=$(losetup -Pf --show "$FINAL_IMAGE")
info "Waiting for loop device..."
udevadm settle

e2fsck -f -y "${LOOP_DEV}p2"
resize2fs "${LOOP_DEV}p2"

# --- Mount & Chroot ---
info "Mounting and copying repository..."
mount "${LOOP_DEV}p2" "$MOUNT_DIR"
mount "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"

mkdir -p "$MOUNT_DIR/opt/MirrorDash"
find "$REPOS_DIR" -mindepth 1 -maxdepth 1 -not -name ".*" -not -name "$(basename "$BUILD_DIR")" -exec cp -a -t "$MOUNT_DIR/opt/MirrorDash/" {} +

info "Executing configure_image.sh via systemd-nspawn..."
systemd-nspawn --setenv=BUILDING_IMAGE=1 --resolv-conf=copy-host -D "$MOUNT_DIR" /bin/bash -c "cd /opt/MirrorDash/scripts && bash ./configure_image.sh"

# The repo copy is only build input — never ship source or the dev config.json
rm -rf "$MOUNT_DIR/opt/MirrorDash"

# The boot files the image depends on, by Pi OS's names: kernel8/initramfs8 for Pi 3 and 4 (64-bit),
# kernel_2712/initramfs_2712 for Pi 5, and the Pi 3's firmware that goes on p1 too. A new Pi OS that
# renames one must stop the build, not ship a mirror that can't start.
for f in kernel8.img initramfs8 kernel_2712.img initramfs_2712 bootcode.bin start.elf fixup.dat; do
    if [ ! -s "$MOUNT_DIR/boot/firmware/$f" ]; then
        fail "$f is missing from the boot files (did Pi OS rename it?)."
    fi
done

# The read-only root needs overlayroot inside the initramfs; without it overlayroot= in cmdline.txt
# does nothing, and the squashfs root has no writable layer at all. Never ship that.
# The list is read in full first: `| grep -q` stops reading at the first match, the container is
# killed for it, and with pipefail that counted as a failure even when overlayroot was there.
# Both initramfs files: Pi 3/4 and Pi 5 each have their own.
for initramfs in initramfs8 initramfs_2712; do
    initramfs_files=$(systemd-nspawn -q -D "$MOUNT_DIR" lsinitramfs "/boot/firmware/$initramfs") || true
    if ! grep -q overlayroot <<< "$initramfs_files"; then
        fail "overlayroot is missing from $initramfs: the root would have no writable layer."
    fi
    # The root is a squashfs, a module in Pi OS's kernels: without it the root can't be mounted
    if ! grep -q kernel/fs/squashfs/squashfs.ko <<< "$initramfs_files"; then
        fail "squashfs is missing from $initramfs: the mirror couldn't mount its root."
    fi
    if ! grep -q plymouth/themes/mirrordash/mirrordash.script <<< "$initramfs_files"; then
        fail "The MirrorDash boot theme is missing from $initramfs."
    fi
done

# Without a Wi-Fi country the radio stays blocked: no hotspot, no way to set the mirror up.
if ! grep -q 'cfg80211.ieee80211_regdom=' "$MOUNT_DIR/boot/firmware/cmdline.txt"; then
    fail "No Wi-Fi country in cmdline.txt: the mirror couldn't start its setup hotspot."
fi

# HDMI brightness goes through ddcutil; without it the brightness setting does nothing on HDMI.
if ! systemd-nspawn -q -D "$MOUNT_DIR" test -x /usr/bin/ddcutil; then
    fail "ddcutil is missing: brightness would not work on HDMI screens."
fi

# A unit directive the image's systemd doesn't know or can't parse (renamed in a newer systemd, a typo) is only
# a warning at boot: the module sandbox, say, would start without it. systemd-analyze reports those as
# <file>:<line>; a program that only exists on a running mirror (/storage) is no such line.
unit_warnings=$(systemd-nspawn -q -D "$MOUNT_DIR" bash -c 'cd /etc/systemd/system && systemd-analyze verify --man=no cog-kiosk.service labwc-kiosk.service mirrordash*.service mirrordash*.timer 2>&1' || true)
if grep -E '\.(service|timer|path):[0-9]+: ' <<< "$unit_warnings"; then
    fail "The image's systemd ignores directives in the units above."
fi

# --- Build identity ---
# Which OS build this is: the admin page compares it with the newest bundle on GitHub (the bundle's
# file name ends in the same stamp) to offer an OS update. UTC date and time, so newer sorts higher.
OS_BUILD=$(date -u +%Y%m%d-%H%M)
echo "$OS_BUILD" > "$MOUNT_DIR/etc/mirrordash-os-build"
# The release CI builds it as (vX.Y.Z-osN, scripts/release.py option 3), for people: the admin page shows it.
if [ -n "${OS_TAG:-}" ]; then echo "$OS_TAG" > "$MOUNT_DIR/etc/mirrordash-os-version"; fi

# --- The pieces of the A/B card (see step_ab_updates in configure_image.sh) ---
# Every piece is made as a file first, then written into its place in the new image below.
#   autoboot.vfat  p1: bootcode.bin and autoboot.txt
#   boot.vfat      p2 (boot A): the boot partition as built, with root=...-05. Also the boot image
#                  in the update bundle (rauc-post-install points it at the right root on the mirror).
#   boot-b.vfat    p3 (boot B): the same with root=...-06. Slot B is empty until the first update,
#                  but the firmware still needs something bootable there.
#   root.sqfs      p5 (root A) and the root image in the bundle
# The disk identifier stays the base image's, so the PARTUUIDs in cmdline.txt are <id>-05 / -06.
info "Setup complete. Making the boot partitions..."
DISK_ID=$(sfdisk --disk-id "$FINAL_IMAGE")  # e.g. 0x27b94958; PARTUUIDs use it without 0x
PIECES_DIR="$BUILD_DIR/pieces"
mkdir -p "$PIECES_DIR/mnt"

# make_vfat FILE SIZE_MIB LABEL: an empty FAT file system in FILE, mounted at $PIECES_DIR/mnt
make_vfat() {
    rm -f "$1"
    mkfs.vfat -n "$3" -C "$1" $(( $2 * 1024 )) > /dev/null  # -C takes the size in KiB
    mount -o loop "$1" "$PIECES_DIR/mnt"
}

# p1: on a Pi 3 the chip's boot ROM loads bootcode.bin from the first partition, and bootcode.bin
# loads the GPU firmware start.elf (with fixup.dat) from it too. It is start.elf that reads
# autoboot.txt and then boots the chosen partition: with only bootcode.bin and autoboot.txt here, a
# Pi 3B stays black (tested October 2026). Pi 4 and 5 read autoboot.txt from their EEPROM bootloader.
# A system update renews them later from its own boot slot (update-p1, scripts/image/rootfs/usr/local/lib/mirrordash/update-p1).
make_vfat "$PIECES_DIR/autoboot.vfat" 32 AUTOBOOT
cp "$MOUNT_DIR/boot/firmware/"{bootcode.bin,start.elf,fixup.dat} "$PIECES_DIR/mnt/"
cat << 'EOF' > "$PIECES_DIR/mnt/autoboot.txt"
[all]
tryboot_a_b=1
boot_partition=2
[tryboot]
boot_partition=3
EOF
umount "$PIECES_DIR/mnt"

# p2/p3: 256 MiB each (the boot files take about 90 MiB, so there is room for newer kernels)
make_vfat "$PIECES_DIR/boot.vfat" 256 BOOT
cp -r "$MOUNT_DIR/boot/firmware/." "$PIECES_DIR/mnt/"
sed -i "s/root=PARTUUID=[^ ]*/root=PARTUUID=${DISK_ID#0x}-05/" "$PIECES_DIR/mnt/cmdline.txt"
umount "$PIECES_DIR/mnt"
cp "$PIECES_DIR/boot.vfat" "$PIECES_DIR/boot-b.vfat"
mount -o loop "$PIECES_DIR/boot-b.vfat" "$PIECES_DIR/mnt"
sed -i "s/root=PARTUUID=[^ ]*/root=PARTUUID=${DISK_ID#0x}-06/" "$PIECES_DIR/mnt/cmdline.txt"
umount "$PIECES_DIR/mnt"

# --- Root as squashfs ---
# The root is read-only on the mirror (overlayroot), so it ships compressed: about a quarter of the
# ext4 size, which leaves room for a second root (A/B OS updates) on an 8 GB card.
# boot/firmware stays in it as an empty mount point; the boot files are in their own partitions.
cp "$MOUNT_DIR/usr/share/mirrordash/packages.txt" "${FINAL_IMAGE%.img}.packages.txt"
info "Packing the root as squashfs..."
umount "$MOUNT_DIR/boot/firmware"
mksquashfs "$MOUNT_DIR" "$PIECES_DIR/root.sqfs" -comp zstd -noappend -quiet
cleanup
trap - EXIT ERR INT TERM

ROOT_SLOT_MIB=1536  # 1.5 GiB per root slot; the squashfs is about 0.75 GiB
if [ "$(stat -c %s "$PIECES_DIR/root.sqfs")" -gt $(( ROOT_SLOT_MIB * 1048576 )) ]; then
    fail "The root is bigger than a root slot (${ROOT_SLOT_MIB} MiB)."
fi

# --- The image: p1-p5 (the first boot adds p6 and p7, see mirrordash-repart.sh) ---
# Positions in 512-byte sectors; 2048 sectors = 1 MiB. p1 starts at 4 MiB like Pi OS's own images.
# p4 is an "extended" partition: MBR has room for only 4 partitions, so p5 and up live inside it,
# each with a 1 MiB gap in front for its small header (EBR). The image ends where p5 ends; the first
# boot grows p4 to the end of the card.
# After p1 comes 32 MiB of nothing, on purpose: room for a second copy of p1, so that p1 (the Pi 3
# firmware and autoboot.txt) can be renewed safely. update-p1 (scripts/image/rootfs/usr/local/lib/mirrordash/) writes the new copy
# into the empty half and then moves the MBR's p1 entry over to it in a single 512-byte write. The
# room has to be there from the start: a card's layout can't change after it has been flashed.
MIB=2048
P1_START=$(( 4 * MIB ));               P1_SIZE=$(( 32 * MIB ))
P2_START=$(( P1_START + 2 * P1_SIZE ));  P2_SIZE=$(( 256 * MIB ))  # 2 *: p1 and its empty twin
P3_START=$(( P2_START + P2_SIZE ));    P3_SIZE=$(( 256 * MIB ))
P4_START=$(( P3_START + P3_SIZE ))
P5_START=$(( P4_START + MIB ));        P5_SIZE=$(( ROOT_SLOT_MIB * MIB ))
IMAGE_END=$(( P5_START + P5_SIZE ))

info "Assembling the A/B image..."
rm -f "$FINAL_IMAGE"
truncate -s $(( IMAGE_END * 512 )) "$FINAL_IMAGE"
# type c = FAT32 (LBA), 5 = extended, 83 = Linux
sfdisk -q "$FINAL_IMAGE" << EOF
label: dos
label-id: $DISK_ID
start=$P1_START, size=$P1_SIZE, type=c
start=$P2_START, size=$P2_SIZE, type=c
start=$P3_START, size=$P3_SIZE, type=c
start=$P4_START, size=$(( IMAGE_END - P4_START )), type=5
start=$P5_START, size=$P5_SIZE, type=83
EOF

# write_at FILE START_SECTOR: copy FILE into the image at that position
write_at() {
    dd if="$1" of="$FINAL_IMAGE" bs=1M seek=$(( $2 * 512 )) oflag=seek_bytes conv=notrunc status=none
}
write_at "$PIECES_DIR/autoboot.vfat" "$P1_START"
write_at "$PIECES_DIR/boot.vfat" "$P2_START"
write_at "$PIECES_DIR/boot-b.vfat" "$P3_START"
write_at "$PIECES_DIR/root.sqfs" "$P5_START"

# --- The update bundle (RAUC) ---
# The same boot and root images as in the card image, signed: mirrors install only bundles signed
# with the key that belongs to scripts/rauc/cert.pem (in every image as /etc/rauc/ca.cert.pem).
# The key comes from the RAUC_KEY secret in CI; a local build without it makes no bundle.
# format=verity lets a mirror install straight from the download (streaming), checking every
# block against the signed hashes as it goes.
BUNDLE="mirrordash-os-v${VERSION}-${OS_BUILD}.raucb"
if [ -n "${RAUC_KEY:-}" ]; then
    info "Making the signed update bundle $BUNDLE..."
    mkdir -p "$PIECES_DIR/bundle"
    mv "$PIECES_DIR/root.sqfs" "$PIECES_DIR/bundle/rootfs.img"
    mv "$PIECES_DIR/boot.vfat" "$PIECES_DIR/bundle/boot.vfat"
    # Slot classes (rootfs, boot) as in /etc/rauc/system.conf on the mirror
    cat << EOF > "$PIECES_DIR/bundle/manifest.raucm"
[update]
compatible=mirrordash-rpi
version=${VERSION}-${OS_BUILD}

[bundle]
format=verity

[image.rootfs]
filename=rootfs.img

[image.boot]
filename=boot.vfat
EOF
    KEY_FILE=$(mktemp)
    trap 'rm -f "$KEY_FILE"' EXIT  # the signing key never stays on the disk, also when rauc fails
    printf '%s\n' "$RAUC_KEY" > "$KEY_FILE"
    rm -f "$BUNDLE"
    rauc bundle --cert="$REPOS_DIR/scripts/rauc/cert.pem" --key="$KEY_FILE" "$PIECES_DIR/bundle" "$BUNDLE"
    rm -f "$KEY_FILE"
    trap - EXIT
else
    warn "No RAUC_KEY: no update bundle (only the image)."
fi
rm -rf "$PIECES_DIR"

info "Compressing final image to XZ..."
xz -T0 -6 "$FINAL_IMAGE"
sha256sum "${FINAL_IMAGE}.xz" > "${FINAL_IMAGE}.xz.sha256"
echo -e "\e[32m[SUCCESS] Build Complete!\e[0m"
