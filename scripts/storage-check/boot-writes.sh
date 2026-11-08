#!/bin/bash
# Inside the container of run.sh: power cuts while the boot partition of the running slot is written. The
# real mirrordash-wifi-country and mirrordash-gpio-overlays run on a FAT32 partition behind dm-log-writes;
# replay.py rebuilds every state a cut can leave, and each is read as the firmware reads it (straight from
# the FAT, mtools), before and after fsck.fat -a (the mount's check). As the image ships (a country in
# cmdline.txt, an empty GPIO block) no state may be broken. On a card without them (the fallback: a new file
# moved over) a broken state is known (ARCHITECTURE #58) and must be found, or this check sees nothing.
set -uo pipefail
ROOTFS=/repo/scripts/image/rootfs
apt-get update -qq >/dev/null && apt-get install -y -qq dosfstools mtools dmsetup python3 kmod >/dev/null 2>&1
if ! modprobe dm-log-writes 2>/dev/null; then
    echo "Power-cut check SKIPPED: this kernel has no dm-log-writes"
    exit 0
fi
for i in $(seq 0 255); do [ -e /dev/loop$i ] || mknod /dev/loop$i b 7 $i; done
mkdir -p /mnt/boot /work
CMD="console=tty3 root=PARTUUID=abcd1234-05 rootfstype=squashfs rootwait overlayroot=tmpfs:recurse=0 panic=10"
LOOPS=()
cleanup() {
    umount -q /mnt/boot 2>/dev/null; dmsetup remove lw 2>/dev/null
    local l; for l in "${LOOPS[@]}"; do losetup -d "$l"; done; LOOPS=()
}
trap cleanup EXIT  # loop devices are the host's

# run_case NAME BASE_CMDLINE BLOCK(yes|no) FUNCTION: the number of broken states in BROKEN
run_case() {
    local name=$1 base_cmd=$2 block=$3 run=$4 data log
    rm -rf /work/* && truncate -s 256M /work/base.img && mkfs.vfat -F 32 /work/base.img >/dev/null
    echo "$base_cmd" > /tmp/cmdline.txt
    printf 'dtparam=audio=on\n[all]\n' > /tmp/config.txt
    if [ "$block" = yes ]; then  # the empty block the image build writes
        sed "s|CONFIG=/boot/firmware/config.txt|CONFIG=/tmp/config.txt|" "$ROOTFS/usr/local/bin/mirrordash-gpio-overlays" > /tmp/g.sh
        bash /tmp/g.sh
    fi
    mcopy -i /work/base.img /tmp/cmdline.txt /tmp/config.txt ::
    cp /work/base.img /work/disk.img
    truncate -s 64M /work/log.img  # zeros already: written through the page cache they could land on the log later
    data=$(losetup -f --show /work/disk.img); LOOPS+=("$data")
    log=$(losetup -f --show /work/log.img); LOOPS+=("$log")
    dmsetup create lw --noudevsync --table "0 $(blockdev --getsz "$data") log-writes $data $log" && dmsetup mknodes lw
    dmsetup message lw 0 mark start
    mount -t vfat -o defaults,noatime /dev/mapper/lw /mnt/boot
    "$run"
    umount /mnt/boot  # writeback and the unmount's own writes count: a cut can come then too
    blockdev --flushbufs /dev/mapper/lw
    dmsetup message lw 0 mark end
    blockdev --flushbufs /dev/mapper/lw
    dmsetup remove lw
    blockdev --flushbufs "$log"
    mkdir -p /work/states
    python3 /repo/scripts/storage-check/replay.py /work/base.img "$log" /work/states || { echo "$name: replay failed"; exit 1; }
    cleanup

    local s cmd conf c
    BROKEN=0
    for s in /work/states/*.img; do
        for pass in firmware fsck; do
            [ $pass = fsck ] && fsck.fat -a "$s" >/dev/null 2>&1
            cmd=$(mtype -i "$s" ::cmdline.txt 2>/dev/null)
            conf=$(mtype -i "$s" ::config.txt 2>/dev/null)
            case "$cmd" in "$base_cmd"|"${base_cmd% cfg80211*} cfg80211.ieee80211_regdom=SE") ;; *) c=bad ;; esac
            case "$conf" in "dtparam=audio=on"*) ;; *) c=bad ;; esac
            if [ "${c:-}" = bad ]; then
                BROKEN=$((BROKEN + 1)); c=""
                echo "    broken ($pass): $(cat "${s%.img}.txt"); cmdline.txt '${cmd:0:30}', config.txt '${conf:0:20}'"
                break
            fi
        done
    done
    echo "  $name: $BROKEN of $(find /work/states -name '*.img' | wc -l) power-cut states broken"
}
wifi_country() {
    sed "s|CMDLINE=/boot/firmware/cmdline.txt|CMDLINE=/mnt/boot/cmdline.txt|" "$ROOTFS/usr/local/bin/mirrordash-wifi-country" > /tmp/wc.sh
    bash /tmp/wc.sh SE
}
gpio() {
    sed "s|CONFIG=/boot/firmware/config.txt|CONFIG=/mnt/boot/config.txt|" "$ROOTFS/usr/local/bin/mirrordash-gpio-overlays" > /tmp/gp.sh
    bash /tmp/gp.sh button:17 dht11:4 light:0x23
}

fail=0
echo "As the image ships (written over in place): no state may be broken"
run_case "wifi-country" "$CMD cfg80211.ieee80211_regdom=00" yes wifi_country; [ "$BROKEN" = 0 ] || fail=1
run_case "gpio-overlays" "$CMD cfg80211.ieee80211_regdom=00" yes gpio; [ "$BROKEN" = 0 ] || fail=1
echo "A card without them (a new file moved over): the known broken state must be found"
run_case "wifi-country, first time" "$CMD" no wifi_country; [ "$BROKEN" -gt 0 ] || { echo "  the check found nothing"; fail=1; }
run_case "gpio-overlays, first time" "$CMD cfg80211.ieee80211_regdom=00" no gpio; [ "$BROKEN" -gt 0 ] || { echo "  the check found nothing"; fail=1; }
if [ "$fail" = 0 ]; then echo "Power-cut checks passed"; else exit 1; fi
