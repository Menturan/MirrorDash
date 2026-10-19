#!/bin/bash
# MirrorDash MBR Partition Expander: runs at every boot, and only does something on the first one.
#
# Two layouts exist:
#   * An image from build_image.sh (A/B OS updates, see step_ab_updates):
#       p1 autoboot (Pi firmware + autoboot.txt), p2 boot A, p3 boot B, p4 extended partition,
#       p5 root A, p6 root B, p7 /storage. The image only holds p1-p5, so it stays small to flash;
#       this script adds p6 and p7 on the first boot. p4 is the "box" (extended partition) that MBR
#       needs to hold more than 4 partitions; p5, p6 and p7 live inside it.
#   * A Pi set up by running configure_image.sh by hand: p1 boot, p2 root, and this script adds
#     p3 /storage after it.
set -euo pipefail

# The root partition from the kernel's root= (PARTUUID=...): / is an overlay on a locked mirror
ROOT_PART=$(findfs "$(sed -n 's/\(^\|.* \)root=\([^ ]*\).*/\2/p' /proc/cmdline)")
ROOT_NAME=$(basename "$ROOT_PART")
DISK="/dev/$(lsblk -ndo PKNAME "$ROOT_PART")"
# The kernel's naming: a "p" before the number when the disk name ends in a digit (mmcblk0p3, sda3)
case "$DISK" in
    *[0-9]) PART_PREFIX="${DISK}p" ;;
    *)      PART_PREFIX="${DISK}" ;;
esac

# --- A/B image: add root B (p6, empty until the first OS update) and /storage (p7) ---
if [ -b "${PART_PREFIX}5" ]; then
    if [ -b "${PART_PREFIX}7" ]; then
        echo "A/B layout is complete."
        exit 0
    fi
    echo "First boot of an A/B image: adding root B (p6) and /storage (p7)..."
    # Only sfdisk touches this table, never parted: on a test card, parted rewrote the disk
    # identifier while growing p4. The identifier is the first half of every PARTUUID, and
    # cmdline.txt finds the root by it (root=PARTUUID=<identifier>-05), so the next boot would have
    # found no root. sfdisk keeps it. --no-reread / --no-tell-kernel: the card is in use (the root
    # is on it), so sfdisk only writes the table; partx below tells the kernel about p6 and p7.
    #
    # 1. Grow the extended partition (p4) to the end of the card, so p6 and p7 fit inside it.
    echo ',+' | sfdisk -q --no-reread --no-tell-kernel -N 4 "$DISK"
    # 2. Add p6 (root B, 1.5 GiB like root A) and p7 (/storage, the rest) inside it. Positions in
    #    512-byte sectors (/sys gives p5's start and size in them; 2048 sectors = 1 MiB). Each starts
    #    1 MiB after the previous one ends: every partition inside the extended one has a small
    #    header (an EBR) in front of it, and whole MiB keep the card's erase blocks aligned.
    P5_NAME=$(basename "${PART_PREFIX}5")
    P5_END=$(( $(cat "/sys/class/block/$P5_NAME/start") + $(cat "/sys/class/block/$P5_NAME/size") ))
    P6_START=$(( P5_END + 2048 ))
    P6_SIZE=$(( 1536 * 2048 ))
    P7_START=$(( P6_START + P6_SIZE + 2048 ))
    printf 'start=%s, size=%s, type=83\nstart=%s, type=83\n' "$P6_START" "$P6_SIZE" "$P7_START" |
        sfdisk -q --no-reread --no-tell-kernel --append "$DISK"
    # The card is in use (the root is on it), so the kernel can't reread the whole table:
    # partx -a adds just the new partitions. It complains about the ones that exist; that's fine.
    partx -a "$DISK" || true
    udevadm settle
    if [ ! -b "${PART_PREFIX}7" ]; then
        echo "Error: ${PART_PREFIX}7 did not appear after creating it" >&2
        exit 1
    fi
    echo "Formatting ${PART_PREFIX}7 as ext4 with label 'mirrordash-data'..."
    mkfs.ext4 -F -L mirrordash-data "${PART_PREFIX}7"
    udevadm settle
    exit 0
fi

# --- Hand-made setup: add /storage (p3) right after the root ---
PART_NUM=3
TARGET_PART="${PART_PREFIX}${PART_NUM}"

if [ ! -b "$TARGET_PART" ]; then
    echo "Persistent partition $TARGET_PART not found. Creating..."
    
    # Right after the root partition (start + size, in 512-byte sectors, from the kernel)
    START_SECTOR=$(( $(cat "/sys/class/block/$ROOT_NAME/start") + $(cat "/sys/class/block/$ROOT_NAME/size") ))
    echo "Creating partition $PART_NUM starting at ${START_SECTOR}s..."
    
    # Create partition using parted
    parted -s -a optimal "$DISK" -- mkpart primary ext4 "${START_SECTOR}s" 100%
    
    # Reload partition table and wait for udev to create the device node (using fallback for busy partition tables)
    partprobe "$DISK" || partx -a "$DISK" || true
    udevadm settle
    
    if [ -b "$TARGET_PART" ]; then
        echo "Formatting $TARGET_PART as ext4 with label 'mirrordash-data'..."
        mkfs.ext4 -F -L mirrordash-data "$TARGET_PART"
        # Ensure udev processes the new disk label symlink before exiting
        udevadm settle
    else
        echo "Error: Partition device $TARGET_PART did not appear after udevadm settle" >&2
        exit 1
    fi
else
    echo "Persistent partition $TARGET_PART already exists."
fi
