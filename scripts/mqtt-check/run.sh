#!/bin/bash
# The MQTT bridge (features/mqtt/bridge.py, ARCHITECTURE #42) against real Mosquitto 2 brokers in Docker: one as
# Home Assistant's add-on is by default (logins, no ACL), one locked down with the admin card's ACL lines.
# Usage: scripts/mqtt-check/run.sh   (needs Docker; prints ALL GOOD at the end)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'docker rm -f mqtt-check-open mqtt-check-hard >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
IMAGE=eclipse-mosquitto:2
docker pull -q $IMAGE >/dev/null

for broker in open hard; do
    mkdir -p "$WORK/$broker"
    docker run --rm -v "$WORK/$broker:/mosquitto/config" $IMAGE sh -c '
        mosquitto_passwd -b -c /mosquitto/config/passwd mirror pw-mirror
        mosquitto_passwd -b /mosquitto/config/passwd ha pw-ha
        mosquitto_passwd -b /mosquitto/config/passwd zigbee pw-zigbee
        mosquitto_passwd -b /mosquitto/config/passwd watch pw-watch
        chown mosquitto /mosquitto/config/passwd; chmod 0700 /mosquitto/config/passwd'
    printf 'listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwd\n' > "$WORK/$broker/mosquitto.conf"
done
# The admin card's lines for the mirror (admin_mqtt.html), Home Assistant everything, another client its own
cat >> "$WORK/hard/mosquitto.conf" <<'EOF'
acl_file /mosquitto/config/acl
use_username_as_clientid true
EOF
cat > "$WORK/hard/acl" <<'EOF'
user mirror
topic write mirrordash/check/#
topic read mirrordash/check/+/set
topic read mirrordash/check/enabled/+/set
topic read mirrordash/check/m/+/+/set
topic write homeassistant/device/check/#
topic read homeassistant/status

user ha
topic readwrite #

user zigbee
topic readwrite zigbee2mqtt/#
topic write homeassistant/+/+/+/config

# What the check reads (one connection of its own: the client ID is the user name here)
user watch
topic read #
EOF
docker run -d --name mqtt-check-open -p 127.0.0.1:18830:1883 -v "$WORK/open:/mosquitto/config" $IMAGE >/dev/null
docker run -d --name mqtt-check-hard -p 127.0.0.1:18831:1883 -v "$WORK/hard:/mosquitto/config" $IMAGE >/dev/null
sleep 1
cd "$HERE/../.." && uv run python -I "$HERE/check.py"
