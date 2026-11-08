#!/bin/bash
# What a damaged card and a power cut do (ARCHITECTURE #58), on real file systems in privileged Debian trixie
# containers, without an OS image: what the unit tests can only stub.
#   check.sh        the storage partition's repair, rescue and rebuild (mirrordash-hydrate.sh, storage-rescue)
#                   on a loop device with real e2fsprogs and mount
#   boot-writes.sh  every state a power cut can leave while the running slot's cmdline.txt or config.txt is
#                   written (dm-log-writes + replay.py), read as the firmware reads it
# Needs docker (and the kernel's dm-log-writes for the second, which says so when it's missing); loop devices
# are the host kernel's, so both detach every one they attached.
#   scripts/storage-check/run.sh
# CI runs it on every push (.github/workflows/test.yml).
set -eo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
docker run --rm --privileged -v "$repo":/repo:ro debian:trixie bash /repo/scripts/storage-check/check.sh
docker run --rm --privileged -v /lib/modules:/lib/modules:ro -v "$repo":/repo:ro debian:trixie \
    bash /repo/scripts/storage-check/boot-writes.sh
