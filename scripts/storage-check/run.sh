#!/bin/bash
# The storage partition's repair, rescue and rebuild (mirrordash-hydrate.sh + storage-rescue, ARCHITECTURE
# #58) on a real loop device with real e2fsprogs and mount, in a privileged Debian trixie container, without
# an OS image: what the unit tests can only stub. check.sh says what each case shows. Needs docker; loop
# devices are the host kernel's, so check.sh detaches every one it attached.
#   scripts/storage-check/run.sh
# CI runs it on every push (.github/workflows/test.yml).
set -eo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
docker run --rm --privileged -v "$repo":/repo:ro debian:trixie bash /repo/scripts/storage-check/check.sh
