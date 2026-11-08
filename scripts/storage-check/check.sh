#!/bin/bash
# Inside the container of run.sh: the real mirrordash-hydrate.sh and storage-rescue against a real ext4 on a
# loop device. Only the label's link (no udev here) and /proc/cmdline point at the test's own; e2fsck and
# mount are swapped for ones that fail where a case needs it. Exit 1 when a check fails.
set -uo pipefail
ROOTFS=/repo/scripts/image/rootfs
apt-get update -qq >/dev/null && apt-get install -y -qq openssh-client e2fsprogs >/dev/null 2>&1
useradd -m pi 2>/dev/null; mkdir -p /home/pi/mirrordash/base_venv /usr/local/lib/mirrordash /storage /fake
install -m 755 "$ROOTFS/usr/local/lib/mirrordash/storage-rescue" "$ROOTFS/usr/local/lib/mirrordash/save-log" /usr/local/lib/mirrordash/
printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/udevadm && chmod 755 /usr/local/bin/udevadm
echo "root=PARTUUID=abcd-05 rootfstype=squashfs" > /tmp/cmdline
# No udev in a container: the loop device nodes the kernel hands out must exist already
for i in $(seq 0 255); do [ -e /dev/loop$i ] || mknod /dev/loop$i b 7 $i; done

LOOP="" n=0 fail=0
unmount_all() {
    umount -q /storage/mirrordash/modules /etc/NetworkManager/system-connections /run/mirrordash-rescue /storage /mnt 2>/dev/null
    return 0
}
# The loop devices are the host's: never leave one behind (hydrate's own modules image detaches on unmount)
trap 'unmount_all; [ -n "$LOOP" ] && losetup -d "$LOOP"' EXIT
check() { if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; fail=1; fi; }
same() { [ "$(sha256sum < "$1" | cut -d' ' -f1)" = "$(cat "$2")" ]; }

# A new 300 MB storage partition with settings, two automatic backups and a hand-made one
new_disk() {
    unmount_all
    if [ -n "$LOOP" ]; then losetup -d "$LOOP"; fi
    rm -f /fake/* /run/mirrordash-storage-rebuilt  # a new start
    n=$((n + 1)); truncate -s 300M /disk$n.img
    mkfs.ext4 -q -F -b 4096 -L mirrordash-data /disk$n.img && LOOP=$(losetup -f --show /disk$n.img) &&
        mount "$LOOP" /storage || { echo "setup failed"; exit 2; }
    local b=/storage/mirrordash/data/backups
    mkdir -p $b
    echo '{"settings": "mine"}' > /storage/mirrordash/data/config.json
    head -c 200000 /dev/urandom > $b/mirrordash_autobackup_20261101_033000.mirror
    head -c 300000 /dev/urandom > $b/mirrordash_autobackup_20261108_033000.mirror
    head -c 100000 /dev/urandom > $b/mirrordash_backup_20261102_120000.mirror
    sha256sum < $b/mirrordash_autobackup_20261108_033000.mirror | cut -d' ' -f1 > /want-auto
    sha256sum < $b/mirrordash_backup_20261102_120000.mirror | cut -d' ' -f1 > /want-hand
    umount /storage
}
zero_superblock() { dd if=/dev/zero of="$LOOP" bs=1024 seek=1 count=1 conv=notrunc status=none; }
fake() { printf '#!/bin/sh\n%s\n' "$2" > "/fake/$1"; chmod 755 "/fake/$1"; }
hydrate() {
    sed -e "s|/dev/disk/by-label/mirrordash-data|$LOOP|" -e "s|/proc/cmdline|/tmp/cmdline|g" \
        "$ROOTFS/usr/local/bin/mirrordash-hydrate.sh" > /tmp/hydrate.sh
    echo "LABEL=mirrordash-data /storage ext4 defaults,noatime,nofail 0 2" > /etc/fstab
    PATH="/fake:$PATH" bash /tmp/hydrate.sh > /tmp/hydrate.log 2>&1; echo $? > /tmp/rc
}
# shellcheck disable=SC2034  # used inside the eval strings of check
backups=/storage/mirrordash/data/backups

echo "A healthy partition that just wasn't mounted"
new_disk; hydrate
check "mounted, settings kept" '[ "$(cat /tmp/rc)" = 0 ] && grep -q mine /storage/mirrordash/data/config.json'

echo "The first superblock gone: e2fsck repairs it from a copy"
new_disk; zero_superblock; hydrate
check "repaired, settings kept, not formatted" '[ "$(cat /tmp/rc)" = 0 ] && grep -q mine /storage/mirrordash/data/config.json'
check "no storage-rebuilt log, no mark for the bell" '! ls /storage/mirrordash/logs/storage-rebuilt-* >/dev/null 2>&1 && [ ! -e /run/mirrordash-storage-rebuilt ]'

echo "e2fsck gives up (8), the backups still read: rescued, made anew, put back"
new_disk; zero_superblock; fake e2fsck 'exit 8'; hydrate
check "hydrate succeeds" '[ "$(cat /tmp/rc)" = 0 ]'
check "made anew (old settings gone), labelled" '[ ! -e /storage/mirrordash/data/config.json ] && [ "$(e2label "$LOOP")" = mirrordash-data ]'
check "newest automatic backup back, whole" 'same $backups/mirrordash_autobackup_20261108_033000.mirror /want-auto'
check "newest hand-made backup back, whole" 'same $backups/mirrordash_backup_20261102_120000.mirror /want-hand'
check "nothing older rescued" '[ "$(ls $backups | wc -l)" = 2 ]'
check "only pi may read them (0600)" '[ "$(stat -c %U:%a $backups/mirrordash_autobackup_20261108_033000.mirror)" = pi:600 ]'
check "storage-rebuilt log saved, no storage-repair" 'ls /storage/mirrordash/logs/storage-rebuilt-* >/dev/null 2>&1 && ! ls /storage/mirrordash/logs/storage-repair-* >/dev/null 2>&1'
check "the rescue's RAM is given back" '! mountpoint -q /run/mirrordash-rescue'
check "this start's mark for the bell's first look" '[ -e /run/mirrordash-storage-rebuilt ]'
check "starts as a new mirror (venv seeded)" '[ "$(readlink /storage/mirrordash/venv)" = venv_a ]'

echo "e2fsck fixed it (1) but the mount fails: never formatted"
new_disk; fake e2fsck 'exit 1'; fake mount 'case "$*" in *storage*) exit 32 ;; esac; exec /usr/bin/mount "$@"'; hydrate
rm -f /fake/mount
check "hydrate stops" '[ "$(cat /tmp/rc)" = 1 ]'
check "settings kept" 'mount "$LOOP" /mnt && grep -q mine /mnt/mirrordash/data/config.json'

echo "mkfs.ext4 with one -F refuses a partition in use"
check "refused, data kept" '! mkfs.ext4 -q -F -L x "$LOOP" </dev/null >/dev/null 2>&1 && grep -q mine /mnt/mirrordash/data/config.json'

if [ "$fail" = 0 ]; then echo "All storage checks passed"; else echo "--- the last hydrate's output"; cat /tmp/hydrate.log; exit 1; fi
