#!/bin/bash
# At boot: no network within 30 s -> start the setup hotspot. With --watch (every 3 minutes, from
# mirrordash-wifi-watch.timer): on the hotspot, try the saved Wi-Fi again (a router that came back
# after a power cut); off it, a Wi-Fi that is gone for good starts the hotspot without a restart,
# a Wi-Fi that is connected but passes no traffic is reconnected (see check_traffic), a Wi-Fi chip
# that hangs is started again step by step (see recover_hung_chip), and a Wi-Fi chip that didn't
# start is started again (see recover_wifi_chip).
INTERFACE="wlan0"
SSID="MirrorDash-Setup"
CACHE_FILE="/var/lib/mirrordash-wifi-scan.cache"
# How many watch rounds in a row found no traffic (in RAM: a restart starts over)
FAILS_FILE="/run/mirrordash-wifi-fails"
# The last step recover_hung_chip took, so the round that finds traffic again can tell which step was enough
RECOVERY_FILE="/run/mirrordash-wifi-recovery"
# Overridable so tests/test_wifi.py can fake the kernel's view
SYS=${MIRRORDASH_SYS:-/sys}
# When the mirror last restarted because its Wi-Fi chip didn't start (on /storage: survives that restart)
CHIP_REBOOT_FILE=${MIRRORDASH_CHIP_REBOOT_FILE:-/storage/mirrordash/wifi-chip-restart}

hotspot_active() {
    nmcli -t -f NAME connection show --active | grep -qxF "$SSID"
}

# The Wi-Fi networks the mirror has joined before (not the hotspot), the last one used first
saved_wifi() {
    nmcli -t -f TIMESTAMP,TYPE,NAME connection show | sort -t: -k1,1rn |
        sed -n 's/^[0-9]*:802-11-wireless://p' | sed 's/\\:/:/g' | grep -vxF "$SSID"
}

# A Wi-Fi the mirror has joined before (a new mirror has none, so its hotspot just stays up)
has_saved_wifi() {
    [ -n "$(saved_wifi)" ]
}

# Join a saved Wi-Fi by its name, after a scan. Not `nmcli device connect`: right after the hotspot
# the chip has no scan results (brcmfmac rejects the first scan), so it saw no saved Wi-Fi and brought
# the hotspot back up instead, which nm-online counts as online: a mirror stayed on the hotspot for good.
join_saved_wifi() {
    local name
    nmcli -t -f SSID device wifi list --rescan yes >/dev/null 2>&1
    while IFS= read -r name; do
        # The service is killed at 150 s (mirrordash-wifi-watch.service): with many saved Wi-Fi, stop trying in time
        [ "$SECONDS" -lt 90 ] || break
        nmcli --wait 45 connection up id "$name" && return 0
    done < <(saved_wifi)
    return 1
}

# Someone is on the hotspot right now (in the middle of setup): don't pull it away from them.
# ponytail: neighbour states, since iw isn't in the image; a phone idle for a minute counts as gone.
phone_on_hotspot() {
    ip neigh show dev "$INTERFACE" | grep -qE 'REACHABLE|DELAY|PROBE'
}

# The hotspot came or went while MirrorDash runs: its screen reloads into the setup prompt or the
# mirror. Nothing to tell at boot (MirrorDash starts after this), so a failure here is harmless.
tell_mirrordash() {
    curl -fsS -m 5 -X POST -H "X-MirrorDash-Local: $(cat /storage/mirrordash/data/local-token 2>/dev/null)" http://127.0.0.1:8000/api/wifi/changed >/dev/null || true
}

# Does anything actually get through? The router, or failing that 1.1.1.1 (some routers don't answer
# ping). NetworkManager only knows the Wi-Fi is joined: the Pi 3's Wi-Fi chip can stay joined while
# nothing passes, and then NetworkManager sees nothing wrong (a mirror stayed like that for 20 minutes).
traffic_flows() {
    local gateway
    gateway=$(ip route show default dev "$INTERFACE" | awk '{print $3; exit}')
    { [ -n "$gateway" ] && ping -c 3 -W 2 -q "$gateway" >/dev/null 2>&1; } || ping -c 3 -W 2 -q 1.1.1.1 >/dev/null 2>&1
}

# Joined but no traffic for 2 rounds in a row (about 6 minutes; one round can be a router restarting):
# keep the log of what happened (Logs → Saved Logs, once per outage; the system log is in RAM) and
# join the Wi-Fi again, or, when the Wi-Fi chip hangs, start it again. If that doesn't help, the next
# rounds try again.
check_traffic() {
    local fails
    if traffic_flows; then
        if [ -f "$RECOVERY_FILE" ]; then
            logger -t mirrordash-wifi "Traffic gets through again after: $(cat "$RECOVERY_FILE")."
        fi
        rm -f "$FAILS_FILE" "$RECOVERY_FILE"
        return 0
    fi
    fails=$(( $(cat "$FAILS_FILE" 2>/dev/null || echo 0) + 1 ))
    echo "$fails" > "$FAILS_FILE"
    logger -t mirrordash-wifi "Joined to the Wi-Fi, but no traffic gets through (round $fails)."
    [ "$fails" -ge 2 ] || return 0
    if [ "$fails" -eq 2 ] && [ -x /usr/local/lib/mirrordash/save-log ]; then
        /usr/local/lib/mirrordash/save-log wifi-lost -b -n 3000 >/dev/null || true
    fi
    if wifi_chip_hung; then
        recover_hung_chip "$fails"
        return 0
    fi
    logger -t mirrordash-wifi "Joining the Wi-Fi again..."
    nmcli device disconnect "$INTERFACE" || true
    join_saved_wifi || logger -t mirrordash-wifi "Joining the Wi-Fi again failed."
}

# The Wi-Fi chip didn't start: its driver (brcmfmac, on every Pi with built-in Wi-Fi) is loaded, so
# there is a chip, but there is no wlan0. Seen on a Pi 3: "Downloaded RAM image is corrupted" while the
# chip's firmware loaded at boot; then the mirror has no Wi-Fi and no hotspot until it restarts. A Pi
# without built-in Wi-Fi (or with it turned off) never loads brcmfmac, and one whose Wi-Fi works has
# wlan0, so for them this does nothing.
wifi_chip_failed() {
    [ -d "$SYS/module/brcmfmac" ] && [ ! -e "$SYS/class/net/$INTERFACE" ]
}

# The Pi 3's bus to its Wi-Fi chip (SDIO, mmc1) locks up under long heavy traffic, like a 900 MB OS
# update download: "mmc1: Controller never released inhibit bit(s)", then "failed backplane access",
# and from then on the chip answers nothing (-110). A kernel bug without a fix (raspberrypi/linux#5770).
# wlan0 stays, so joining again does nothing: a mirror stayed offline until it was unplugged.
# ponytail: the last 30 minutes of the kernel log cover the rounds recovery takes; a lockup the chip
# survived counts too if the traffic stops for another reason within that half hour.
wifi_chip_hung() {
    journalctl -k -b -q --no-pager --since "-30 min" |
        grep -qE 'brcmf_sdio_isr: failed backplane access|brcmf_proto_bcdc_msg failed w/status -110'
}

# One step a round (round 2 is the first that acts), each stronger than the last: load the driver
# again; restart the bus controller, which also powers the chip off and on; restart the mirror. Each
# step is logged, and the round that finds traffic again logs which step was enough.
recover_hung_chip() {
    local controller driver
    case "$1" in
        2)
            echo "loading the Wi-Fi driver again" > "$RECOVERY_FILE"
            logger -t mirrordash-wifi "The Wi-Fi chip hangs; loading its driver again..."
            reload_wifi_driver
            ;;
        3)
            echo "restarting the Wi-Fi chip's bus" > "$RECOVERY_FILE"
            # wlan0 -> mmc1:0001:1 (the chip) -> mmc1:0001 -> mmc1 -> mmc_host -> the controller
            controller=$(realpath "$SYS/class/net/$INTERFACE/device/../../../..")
            driver=$(realpath "$controller/driver")
            logger -t mirrordash-wifi "The Wi-Fi chip still hangs; restarting its bus (${controller##*/})..."
            # unbind returns once the controller is shut down, so bind can follow at once
            echo "${controller##*/}" > "$driver/unbind"
            echo "${controller##*/}" > "$driver/bind"
            ;;
        *)
            restart_for_wifi_chip "The Wi-Fi chip still hangs"
            ;;
    esac
}

# brcmfmac and the maker's part loaded with it (brcmfmac_cyw, _wcc, _bca: depends on the chip). A hung
# chip can make unloading hang; the timeout lets the rest go on (the next round's step is stronger).
reload_wifi_driver() {
    # shellcheck disable=SC2046
    timeout 30 modprobe -r $(awk '$1 ~ /^brcmfmac_/ { print $1 }' /proc/modules) brcmfmac || true
    timeout 30 modprobe brcmfmac || true
}

# Restart the mirror for its Wi-Fi chip, at most once an hour, so a chip that is really broken doesn't
# make it restart over and over.
restart_for_wifi_chip() {
    local last
    last=$(cat "$CHIP_REBOOT_FILE" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - last )) -lt 3600 ]; then
        logger -t mirrordash-wifi "$1; restarted less than an hour ago, so not again."
        return 0
    fi
    logger -t mirrordash-wifi "$1: restarting the mirror."
    date +%s > "$CHIP_REBOOT_FILE"
    sync
    systemctl reboot
}

# Keep the log (once per start), then load the driver again: harmless on every model. If wlan0 still
# doesn't come and the mirror has no network at all (not even a cable), restart it.
recover_wifi_chip() {
    if [ ! -e /run/mirrordash-wifi-chip-logged ] && [ -x /usr/local/lib/mirrordash/save-log ]; then
        /usr/local/lib/mirrordash/save-log wifi-chip -b -n 3000 >/dev/null || true
        touch /run/mirrordash-wifi-chip-logged
    fi
    logger -t mirrordash-wifi "The Wi-Fi chip didn't start (no $INTERFACE); loading its driver again..."
    reload_wifi_driver
    # The driver starts the chip in the background: wait for wlan0 to appear (up to 30 s)
    udevadm wait --timeout=30 "$SYS/class/net/$INTERFACE" || true
    if [ -e "$SYS/class/net/$INTERFACE" ]; then
        logger -t mirrordash-wifi "The Wi-Fi chip started."
        return 0
    fi
    if nm-online -q -t 5; then
        return 0  # on another network (a cable): no restart for the Wi-Fi alone
    fi
    restart_for_wifi_chip "The Wi-Fi chip still didn't start and there is no network"
}

start_hotspot() {
    logger -t mirrordash-wifi "No network. Scanning before entering AP mode..."
    # Scan for nearby networks BEFORE entering AP mode (the radio can't scan as a hotspot).
    # --rescan yes returns once the fresh scan has finished.
    SCAN_RESULT=$(nmcli -t -f SSID dev wifi list --rescan yes | sort -u | grep -v '^$' || true)
    echo "$SCAN_RESULT" > "$CACHE_FILE"
    chmod 644 "$CACHE_FILE"
    logger -t mirrordash-wifi "Cached $(echo "$SCAN_RESULT" | grep -c . || echo 0) visible networks for captive portal."

    # Each mirror keeps its own hotspot password: the profile lives on /storage (bind-mounted
    # system-connections), so it survives reboots until the card is reflashed. The app reads the
    # password back from NetworkManager and shows it, with a QR code, on the mirror only.
    # Profiles from older images used the shared password "mirrordash"; those get a new one.
    SAVED_PASSWORD=$(nmcli -s -g 802-11-wireless-security.psk connection show "$SSID" 2>/dev/null || true)
    if [ -z "$SAVED_PASSWORD" ] || [ "$SAVED_PASSWORD" = "mirrordash" ]; then
        nmcli connection delete "$SSID" 2>/dev/null || true
        # 10 characters without look-alikes (no i l o 0 1); nothing that needs escaping in a Wi-Fi QR code.
        # ponytail: ~50 bits, enough for a hotspot that is only up during setup; add characters if that changes.
        PASSWORD=$(tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' </dev/urandom | head -c 10)
        nmcli connection add type wifi ifname "$INTERFACE" con-name "$SSID" ssid "$SSID" mode ap
        nmcli connection modify "$SSID" wifi-sec.key-mgmt wpa-psk
        nmcli connection modify "$SSID" wifi-sec.psk "$PASSWORD"
        nmcli connection modify "$SSID" wifi-sec.pmf 1
        nmcli connection modify "$SSID" ipv4.method shared
        # Only this script starts the hotspot; a saved AP profile must never come up on its own
        nmcli connection modify "$SSID" connection.autoconnect no
        logger -t mirrordash-wifi "Created hotspot '$SSID' with a new password."
    fi

    if nmcli connection up "$SSID"; then
        logger -t mirrordash-wifi "Hotspot '$SSID' started successfully."
    else
        logger -t mirrordash-wifi "Failed to start hotspot."
    fi
}

# Started by the watch timer (every 3 minutes) rather than once at boot
WATCHING=false
if [ "${1:-}" = "--watch" ]; then WATCHING=true; fi

# First: a Wi-Fi chip that didn't start leaves nothing for the rest to work with
if $WATCHING && wifi_chip_failed; then
    recover_wifi_chip
    exit 0
fi

if $WATCHING && hotspot_active; then
    if ! has_saved_wifi || phone_on_hotspot; then
        exit 0
    fi
    logger -t mirrordash-wifi "On the hotspot: trying the saved Wi-Fi again..."
    nmcli connection down "$SSID" || true
    # By name, so it also brings back a profile NetworkManager stopped retrying
    if join_saved_wifi && nm-online -q -t 15; then
        logger -t mirrordash-wifi "Back on the saved Wi-Fi."
        tell_mirrordash
        exit 0
    fi
    logger -t mirrordash-wifi "The saved Wi-Fi isn't there. Back to the hotspot."
    nmcli connection up "$SSID" || logger -t mirrordash-wifi "Failed to start hotspot."
    tell_mirrordash
    exit 0
fi

# nm-online waits on NetworkManager's own state: startup finished, then a connection (or the
# timeout). Any connection counts, also one without internet. --watch waits longer, so a Wi-Fi
# change from the admin page (a few seconds without a connection) never starts the hotspot.
if nm-online -q -t "$($WATCHING && echo 60 || echo 30)"; then
    if $WATCHING; then check_traffic; fi
    exit 0
fi
start_hotspot
$WATCHING && tell_mirrordash
exit 0
