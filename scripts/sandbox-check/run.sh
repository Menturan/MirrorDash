#!/bin/bash
# The module host's sandbox under real systemd, without an OS image (ARCHITECTURE #41): a Debian container
# runs systemd, the real mirrordash-modules.service (a test memory limit and the container's DNS server
# added) and this checkout's app; check.py, as pi, runs the core's client against it and prints what each
# module's user can and can't do, the self-check, a leak's OOM, a site install and a restart of one
# instance. Needs docker; the kernel is this machine's, not the Pi's.
#   scripts/sandbox-check/run.sh
set -e
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
docker build -q -t mirrordash-sandbox-check "$here" >/dev/null
docker rm -f mirrordash-sandbox-check >/dev/null 2>&1 || true
docker run -d --name mirrordash-sandbox-check --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  --tmpfs /tmp:exec -v "$repo":/src:ro -v "$here":/test:ro mirrordash-sandbox-check >/dev/null
trap 'docker rm -f mirrordash-sandbox-check >/dev/null' EXIT
sleep 4
docker exec mirrordash-sandbox-check bash /test/setup.sh
docker exec -u pi -w /tmp mirrordash-sandbox-check /storage/mirrordash/venv/bin/python -I /test/check.py 2>&1 | grep -v "^INFO"
docker exec mirrordash-sandbox-check journalctl -u mirrordash-modules --no-pager -n 15
