#!/bin/bash
set -euo pipefail

# Defensive mount check: if the partition exists but is not mounted (fstab's nofail lets the boot go
# on when its fsck wants a person, e.g. after a power cut mid-write), repair it and mount it
DATA_PART=/dev/disk/by-label/mirrordash-data
REPAIRED=""
if [ -b "$DATA_PART" ] && ! mountpoint -q /storage; then
    echo "Warning: /storage is not mounted but the partition exists. Checking and mounting it..."
    rc=0; e2fsck -p "$DATA_PART" || rc=$?
    # 4 and up: -p wouldn't fix it alone. -y answers yes to everything: a few lost files beat a
    # mirror that has lost all of them.
    if [ "$rc" -ge 4 ]; then rc=0; e2fsck -y "$DATA_PART" || rc=$?; fi
    [ "$rc" -ne 0 ] && REPAIRED=1
    mount /storage || mount "$DATA_PART" /storage || true
fi
# On an image (#5) / is a RAM overlay: without /storage everything below would land in RAM, the
# mirror would look new and forget whatever is saved at the next restart. Stop instead: the app
# (Requires= this unit) doesn't start and the screen's loading page says so after a few minutes.
if grep -qw 'rootfstype=squashfs' /proc/cmdline && ! mountpoint -q /storage; then
    echo "Error: /storage could not be mounted; not starting MirrorDash on a RAM-only /storage." >&2
    exit 1
fi

# Ensure parent directory and subdirectories exist on mounted /storage
mkdir -p /storage/mirrordash/data
mkdir -p /storage/mirrordash/system-connections
chown pi:pi /storage/mirrordash
chown pi:pi /storage/mirrordash/data
# The core's data (every setting and key, the logins, the Matter token) is pi's alone; the modules,
# as their own users, have a folder of their own (mirrordash-modules.service, ARCHITECTURE #41), which
# pi reaches through their group (backups)
chmod 700 /storage/mirrordash/data
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
    mkdir -p "$MODULES/data" "$MODULES/cache"
    chown mirrordash-mod:mirrordash-mod "$MODULES/data" "$MODULES/cache"
    chmod 2771 "$MODULES" "$MODULES/data" "$MODULES/cache"
fi
chown root:root /storage/mirrordash/system-connections
chmod 700 /storage/mirrordash/system-connections

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
mkdir -p /storage/mirrordash/ssh/etc/ssh
ssh-keygen -A -f /storage/mirrordash/ssh

# RAUC (A/B OS updates) keeps its state here: the root forgets everything at reboot
mkdir -p /storage/rauc

# Saved logs from failed updates (mirrordash's save-log); the app lists and deletes them
mkdir -p /storage/mirrordash/logs
chown pi:pi /storage/mirrordash/logs

# The repair above happened before /storage was there to save its log in
if [ -n "$REPAIRED" ] && [ -x /usr/local/lib/mirrordash/save-log ]; then
    /usr/local/lib/mirrordash/save-log storage-repair -b -n 1000 >/dev/null || true
fi
