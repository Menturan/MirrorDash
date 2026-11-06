#!/bin/bash
# What the mirror's screen costs, run on the mirror itself (ssh pi@<mirror>): memory (PSS, shared pages
# split fairly) and CPU of the browser's processes over 60 s, free memory and free GPU memory (CMA).
# Works with Cog (images before WPE 2.54) and mirrordash-screen alike, so both can be compared on the
# same Pi with the same modules.
#   bash measure.sh
set -u
SECONDS_MEASURED=${SECONDS_MEASURED:-60}

pids() { pgrep -f '^/usr/bin/cog|mirrordash-screen|WPEWebProcess|WPENetworkProcess|WPEGPUProcess|^labwc|/labwc' || true; }
ticks() { awk '{print $14 + $15}' "/proc/$1/stat" 2>/dev/null || echo 0; }
name() { cat "/proc/$1/comm"; }

echo "== $(tr -d '\0' < /proc/device-tree/model), kernel $(uname -r)"
dpkg-query -W -f='${Package} ${Version}\n' cog libwpewebkit-2.0-1 2>/dev/null

echo "== Memory (PSS, MB)"
total=0
for p in $(pids); do
    pss=$(awk '/^Pss:/{print int($2/1024)}' "/proc/$p/smaps_rollup" 2>/dev/null || echo 0)
    total=$((total + pss)); printf '  %5s MB  %s\n' "$pss" "$(name "$p")"
done
echo "  ----- $total MB in all (labwc included)"
awk '/^MemTotal|^MemAvailable|^CmaTotal|^CmaFree/{printf "  %-13s %5d MB\n", $1, $2/1024}' /proc/meminfo

echo "== CPU over ${SECONDS_MEASURED} s (% of one core; the Pi 3 has four)"
declare -A start
for p in $(pids); do start[$p]=$(ticks "$p"); done
sleep "$SECONDS_MEASURED"
hz=$(getconf CLK_TCK)
for p in "${!start[@]}"; do
    used=$(( $(ticks "$p") - ${start[$p]} ))
    printf '  %5s %%  %s\n' "$(awk -v t="$used" -v hz="$hz" -v s="$SECONDS_MEASURED" 'BEGIN{printf "%.1f", 100*t/hz/s}')" "$(name "$p")"
done
