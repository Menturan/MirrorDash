#!/bin/bash
# At every boot, as root, before MirrorDash starts: /storage mounted (repaired if it has to be), its
# folders there with the right owners, the venv and its Python, and the SSH host keys.
set -euo pipefail

# A folder root gives an owner and mode at every boot. Most lie in folders pi (or the module host) owns,
# where one could have been swapped for a symlink: chown and chmod would then act on whatever it points
# to (/etc/sudoers.d made pi's is root for pi). So a link there is removed first, never followed.
owned_folder() {  # owned_folder OWNER:GROUP MODE PATH
    local owner=$1 mode=$2 path=$3
    if [ -L "$path" ]; then rm -f "$path"; fi
    mkdir -p "$path"
    chown -h "$owner" "$path"
    chmod "$mode" "$path"
}

# Defensive mount check: if the partition exists but is not mounted (fstab's nofail lets the boot go
# on when its fsck wants a person, e.g. after a power cut mid-write), repair it and mount it
IMAGE=""; grep -qw 'rootfstype=squashfs' /proc/cmdline && IMAGE=1
# On an A/B image the partition is always the SD card's p7, found by its place and never by its label: with
# its superblock broken it has no label, and an old mirror's card in a USB reader carries the same one,
# which e2fsck -y and mkfs below must never touch (fstab names p7 too, configure_image.sh). A hand-made
# setup has it wherever repart put it, so by its label.
if [ -n "$IMAGE" ]; then DATA_PART=/dev/mmcblk0p7; else DATA_PART=/dev/disk/by-label/mirrordash-data; fi
# The device itself, not a link: mkfs below gives the partition a new UUID, and udev takes links away
# and back while it catches up
DATA_PART=$(readlink -f "$DATA_PART")
REPAIRED="" REBUILT="" FORCED_YES="" LOST="" ERRORS_LEFT="" rc=0 force=""
# The newest automatic and hand-made backup, copied into RAM off a partition e2fsck -p gave up on, before
# e2fsck -y writes to it: from a superblock copy with stale group descriptors -y can clear a whole
# file system's inodes, the backups' with them (seen on an emulated mirror, ARCHITECTURE #58)
RESCUE=/run/mirrordash-rescue
rescue_backups() {
    mkdir -p "$RESCUE"
    if ! mountpoint -q "$RESCUE" && ! mount -t tmpfs -o mode=700,size=50% tmpfs "$RESCUE"; then
        echo "Warning: no RAM for the rescue; e2fsck -y runs without the backups copied first" >&2
        return 0
    fi
    /usr/local/lib/mirrordash/storage-rescue "$DATA_PART" "$RESCUE" || echo "Warning: no backup could be rescued" >&2
}
# Whether /storage takes a write: a partition e2fsck -y left with errors can mount and still refuse every
# file in it ("Bad message": its root folder broken), and the kernel turns one read-only on an error it
# meets while mounted; mountpoint alone can't tell either
storage_works() {
    { mountpoint -q /storage && mkdir -p /storage/mirrordash && touch /storage/mirrordash/.write-test &&
        rm -f /storage/mirrordash/.write-test; } 2>/dev/null
}
# Mounted (by systemd, from fstab) but refusing writes: unmounted, so the check below can run. -f, since
# a file system the kernel didn't get to mark as having errors looks clean, and -p skips a clean one.
if [ -b "$DATA_PART" ] && mountpoint -q /storage && ! storage_works; then
    echo "Warning: /storage is mounted but takes no write. Checking it..."
    if umount /storage; then force=-f; fi
fi
if [ -b "$DATA_PART" ] && ! mountpoint -q /storage; then
    echo "Warning: /storage is not mounted but the partition exists. Checking and mounting it..."
    e2fsck ${force:+"$force"} -p "$DATA_PART" || rc=$?
    # 4 and up: -p wouldn't fix it alone. -y answers yes to everything: a few lost files beat a
    # mirror that has lost all of them.
    if [ "$rc" -ge 4 ]; then
        if [ -n "$IMAGE" ]; then rescue_backups; fi
        FORCED_YES=1
        rc=0; e2fsck -y "$DATA_PART" || rc=$?
    fi
    # 1-3: errors found and fixed. 4 and up (after -y): errors left, handled below
    if [ "$rc" -ge 1 ] && [ "$rc" -lt 4 ]; then REPAIRED=1; fi
    mount /storage || mount "$DATA_PART" /storage || true
fi

# Rescued backups back on the card (pi's, 0600) where the partition lacks them or e2fsck changed them.
# A backup that can't go back (a full card) is only warned about: the mirror still starts.
put_back_backups() {
    owned_folder pi:pi 755 /storage/mirrordash
    owned_folder pi:pi 700 /storage/mirrordash/data
    # The app's data dir: ~/.mirrordash is /storage/mirrordash/data, and the app keeps its config and
    # backups in ~/.mirrordash/data (config.py get_config_path, backup/service.py BACKUPS_DIR)
    owned_folder pi:pi 700 /storage/mirrordash/data/data
    owned_folder pi:pi 755 /storage/mirrordash/data/data/backups
    local backup target part
    for backup in "$RESCUE"/*.mirror; do
        [ -f "$backup" ] || continue
        target=/storage/mirrordash/data/data/backups/${backup##*/}
        cmp -s "$backup" "$target" && continue
        # Under a new name first (install creates it, never writing through a link pi put there), on the
        # card, then renamed over: a power cut never leaves half a backup under a backup's name
        part=$(mktemp -u "$target.XXXXXX")
        if install -m 600 "$backup" "$part" && chown -h pi:pi "$part" && sync "$part" && mv -f "$part" "$target"; then
            echo "Rescued backup put back: ${backup##*/}"
        else
            rm -f "$part"
            echo "Warning: a rescued backup couldn't be put back: ${backup##*/}" >&2
        fi
    done
    sync /storage/mirrordash/data/data/backups || true
}
# A partition e2fsck -y couldn't repair (errors left, 4 and up) and that doesn't mount or doesn't work: it is
# made anew, and the backups rescued above go back at once (ARCHITECTURE #58). The mirror then starts as a
# new one and the bell says to restore the backup. Never for a partition that e2fsck found fine or fixed, so
# a mount that fails for another reason never costs the owner anything; never while systemd still mounts
# it either (the unit is ordered after storage.mount), and mkfs.ext4 (one -F) refuses a partition in use.
rebuild_storage() {
    echo "Error: /storage can't be repaired. Making the partition anew..." >&2
    if mountpoint -q /storage; then umount /storage || return 1; fi
    mkfs.ext4 -F -L mirrordash-data "$DATA_PART" || return 1
    udevadm settle || true
    # From here the partition is empty and the backups are only in RAM: one more try after udev caught up
    mount /storage || mount "$DATA_PART" /storage || { udevadm settle; mount -t ext4 "$DATA_PART" /storage; } ||
        return 1
    # Back on the card before anything else can fail: RAM forgets them at the next start
    put_back_backups || true
    sync
    # For the bell's first look: this start rebuilt it (/run is new at every start; the clock can't tell,
    # as it still shows the image's date this early)
    touch /run/mirrordash-storage-rebuilt
    # This start's log so far (what was rescued) becomes a red notice on the bell (features/notifications/watch.py)
    if [ -x /usr/local/lib/mirrordash/save-log ]; then
        /usr/local/lib/mirrordash/save-log storage-rebuilt -b -n 1000 >/dev/null || true
    fi
}
if [ -n "$IMAGE" ] && [ "$rc" -ge 4 ] && [ -b "$DATA_PART" ] && ! storage_works; then
    if ! mountpoint -q /storage; then
        udevadm settle || true  # once more first, after the card's pending events: one that was slow to answer
        mount "$DATA_PART" /storage || true
    fi
    if ! storage_works; then rebuild_storage && REBUILT=1 || true; fi
fi
if [ -z "$REBUILT" ] && storage_works; then
    # Repaired after all (it works): what e2fsck may have cleared of the rescued backups goes back
    if compgen -G "$RESCUE/*.mirror" > /dev/null; then put_back_backups || true; fi
    # e2fsck -y "repaired" it by clearing what it couldn't read, the settings with it: the mirror starts as
    # a new one, and the bell (whose own file went too) must say so on its first look
    if [ -n "$FORCED_YES" ] && [ ! -e /storage/mirrordash/data/data/config.json ] && [ ! -e /storage/mirrordash/data/data/config.json.bak ]; then
        LOST=1
        touch /run/mirrordash-storage-rebuilt
    fi
    # Errors -y couldn't fix, but it works: kept (the owner's settings are in it), and the card is suspect
    if [ "$rc" -ge 4 ]; then ERRORS_LEFT=1; fi
fi
# Given back once the backups are on a card that works; otherwise kept until the next start, for a person
# to copy them off by hand (the app doesn't start)
if mountpoint -q "$RESCUE"; then
    if storage_works; then umount "$RESCUE" || true; else echo "Rescued backups kept in RAM at $RESCUE" >&2; fi
fi

# On an image (#5) / is a RAM overlay: without /storage everything below would land in RAM, the
# mirror would look new and forget whatever is saved at the next restart. Stop instead: the app
# (Requires= this unit) doesn't start and the screen's loading page says so after a few minutes.
if grep -qw 'rootfstype=squashfs' /proc/cmdline && ! mountpoint -q /storage; then
    echo "Error: /storage could not be mounted; not starting MirrorDash on a RAM-only /storage." >&2
    exit 1
fi

owned_folder pi:pi 755 /storage/mirrordash
# The core's data (every setting and key, the logins, the Matter token) is pi's alone; the modules,
# as their own users, have a folder of their own (mirrordash-modules.service, ARCHITECTURE #41), which
# pi reaches through their group (backups)
owned_folder pi:pi 700 /storage/mirrordash/data
if [ -L /storage/mirrordash/modules ]; then rm -f /storage/mirrordash/modules; fi
mkdir -p /storage/mirrordash/modules
# On an image the modules' folder is a file system of its own, in a sparse file on /storage: a module
# that fills it can't fill the card (ARCHITECTURE #41). A quarter of /storage, at most 8 GB, made once,
# with what the folder held moved in; discard gives back the space of what is deleted in it.
MODULES=/storage/mirrordash/modules
MODULES_IMG=/storage/mirrordash/modules.img
if grep -qw 'rootfstype=squashfs' /proc/cmdline && ! mountpoint -q "$MODULES"; then
    if [ ! -f "$MODULES_IMG" ]; then
        echo "Making the modules' own file system..."
        size=$(( $(df --output=size -k /storage | tail -1) / 4 ))
        [ "$size" -gt 8388608 ] && size=8388608
        rm -f "$MODULES_IMG.tmp"
        truncate -s "${size}K" "$MODULES_IMG.tmp"
        mkfs.ext4 -q -F -m 0 -L md-modules "$MODULES_IMG.tmp"
        mkdir -p /run/mirrordash-modules-fs
        mount -o loop "$MODULES_IMG.tmp" /run/mirrordash-modules-fs
        cp -a "$MODULES/." /run/mirrordash-modules-fs/
        umount /run/mirrordash-modules-fs
        mv "$MODULES_IMG.tmp" "$MODULES_IMG"
        find "$MODULES" -mindepth 1 -delete
    fi
    rc=0; e2fsck -p "$MODULES_IMG" >/dev/null || rc=$?
    if [ "$rc" -ge 4 ]; then e2fsck -y "$MODULES_IMG" >/dev/null || true; fi
    # Without it the modules still run, in the folder on /storage, without the limit
    mount -o loop,discard,nosuid,nodev "$MODULES_IMG" "$MODULES" || echo "Warning: the modules' file system didn't mount" >&2
fi
if id mirrordash-mod >/dev/null 2>&1; then
    [ "$(stat -c %U /storage/mirrordash/modules)" = mirrordash-mod ] || chown -R mirrordash-mod:mirrordash-mod /storage/mirrordash/modules
    # Each module instance runs as a user of its own (features/modules/forkserver.py), in no group of the
    # folder: it may pass through these (o+x), not list them, to its own data folder and the site. Set
    # here, as root: the host's sandbox refuses a setgid mode (RestrictSUIDSGID).
    chmod 2771 "$MODULES"  # a link there was removed above, before it was mounted on
    owned_folder mirrordash-mod:mirrordash-mod 2771 "$MODULES/data"
    owned_folder mirrordash-mod:mirrordash-mod 2771 "$MODULES/cache"
fi
owned_folder root:root 700 /storage/mirrordash/system-connections

# Bind mount the NetworkManager connections directory to bypass symlink security restrictions
if ! mountpoint -q /etc/NetworkManager/system-connections; then
    mkdir -p /etc/NetworkManager/system-connections
    mount --bind /storage/mirrordash/system-connections /etc/NetworkManager/system-connections
fi

# Clean up active venv if it is a real directory instead of a symlink
if [ -e /storage/mirrordash/venv ] && [ ! -L /storage/mirrordash/venv ]; then
    rm -rf /storage/mirrordash/venv
fi

# Seed venv_a only when the active link is missing or dangling (first boot / wiped storage).
# A valid link is owned by the A/B updater and may point at venv_b — never reset it.
if [ ! -d /storage/mirrordash/venv ]; then
    if [ ! -d "/storage/mirrordash/venv_a" ]; then
        echo "Hydrating /storage with golden base_venv..."
        rm -rf /storage/mirrordash/venv_a.tmp
        cp -a /home/pi/mirrordash/base_venv /storage/mirrordash/venv_a.tmp
        mv /storage/mirrordash/venv_a.tmp /storage/mirrordash/venv_a
        chown -R pi:pi /storage/mirrordash/venv_a
    fi
    ln -sfT venv_a /storage/mirrordash/venv
fi

# The app's Python lives on /storage with the app (ARCHITECTURE #16), so an OS update never changes it
# under the venvs: it used to be the root's, and a root with another Python version left every venv
# unable to start, which threw away the modules. A venv that still uses the root's Python (one seeded
# from base_venv above, or from before this) gets a copy of that Python here, once, and is pointed at
# it: its bin/python link and the home line of its pyvenv.cfg are the only places that name it.
# A newer Python comes with a MirrorDash update that needs it (uv python install, features/updates).
PY_ROOT=${MIRRORDASH_PY_ROOT:-/home/pi/.local/share/uv/python}
PY_STORAGE=${MIRRORDASH_PY_STORAGE:-/storage/mirrordash/python}  # overridable for tests/test_configure_image.py
VENVS=${MIRRORDASH_VENVS:-/storage/mirrordash}
for venv in "$VENVS"/venv_a "$VENVS"/venv_b "$VENVS"/venv_old "$VENVS"/venv_failed; do
    cfg="$venv/pyvenv.cfg"
    [ -f "$cfg" ] && grep -q "^home = $PY_ROOT/" "$cfg" || continue
    # e.g. cpython-3.14-linux-aarch64-gnu, uv's link to the full version (cpython-3.14.8-...)
    name=$(sed -n "s|^home = $PY_ROOT/\([^/]*\)/bin\$|\1|p" "$cfg")
    if [ -n "$name" ] && [ ! -e "$PY_STORAGE/$name" ]; then
        real=$(readlink -f "$PY_ROOT/$name") || continue
        mkdir -p "$PY_STORAGE"
        # Under another name first: a power cut mid-copy must not leave a half Python that looks done
        rm -rf "$PY_STORAGE/.copy"
        cp -a "$real" "$PY_STORAGE/.copy" && mv -T "$PY_STORAGE/.copy" "$PY_STORAGE/${real##*/}"
        [ "$real" = "$PY_ROOT/$name" ] || ln -sfn "${real##*/}" "$PY_STORAGE/$name"
    fi
    [ -e "$PY_STORAGE/$name" ] || continue  # the copy failed (a full card?): keep the root's for now
    sed -i "s|^home = $PY_ROOT/|home = $PY_STORAGE/|" "$cfg"
    for link in "$venv"/bin/python*; do
        target=$(readlink "$link") || continue
        case "$target" in "$PY_ROOT"/*) ln -sfn "$PY_STORAGE/${target#"$PY_ROOT"/}" "$link" ;; esac
    done
done
[ -d "$PY_STORAGE" ] && chown -R pi:pi "$PY_STORAGE"
chown -h pi:pi /storage/mirrordash /storage/mirrordash/venv  # -h: pi controls the venv link

# SSH host keys live here: every mirror boots the same root image, and changes to it are gone at reboot.
# Only the missing ones are made (the first boot).
# root's own folder, so pi can't swap the keys in it for others
owned_folder root:root 755 /storage/mirrordash/ssh
mkdir -p /storage/mirrordash/ssh/etc/ssh
ssh-keygen -A -f /storage/mirrordash/ssh

# RAUC (A/B OS updates) keeps its state here: the root forgets everything at reboot
mkdir -p /storage/rauc

# Saved logs from failed updates (mirrordash's save-log); the app lists and deletes them
owned_folder pi:pi 755 /storage/mirrordash/logs

# The repair above happened before /storage was there to save its log in (a rebuild saved its own)
save_log() { [ ! -x /usr/local/lib/mirrordash/save-log ] || /usr/local/lib/mirrordash/save-log "$1" -b -n 1000 >/dev/null || true; }
if [ -n "$LOST" ]; then
    save_log storage-lost
elif [ -n "$REPAIRED" ] && [ -z "$REBUILT" ]; then
    save_log storage-repair
fi
# Once, not at every start (-y meets the same errors each time): the log stays among the newest five until
# five others push it out. ponytail: then it is told again, which for a failing card is no harm.
if [ -n "$ERRORS_LEFT" ] && ! compgen -G "/storage/mirrordash/logs/storage-errors-*" > /dev/null; then
    save_log storage-errors
fi
