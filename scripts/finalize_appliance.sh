#!/bin/bash
# MirrorDash Appliance Finalization & Locking Script
# Runs on the appliance itself to verify configuration, purge Wi-Fi, lock OverlayFS, and shut down.

set -e

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash finalize_appliance.sh)"
  exit 1
fi

# Parse arguments
BYPASS_CONFIRM=false
if [ "$1" = "-y" ] || [ "$1" = "--yes" ]; then
  BYPASS_CONFIRM=true
fi

echo "=== MirrorDash Appliance Finalization & Locking ==="

# --- 1. Pre-Lock Verification ---
echo "Running pre-lock verification..."

# Check persistent storage
if ! mount | grep -q "/storage"; then
  echo "Error: /storage partition is not mounted." >&2
  exit 1
fi
echo "  [OK] /storage partition is mounted."

# Check services
SERVICES=("mirrordash.service" "mirrordash-wifi-fallback.service" "mirrordash-expand.service" "systemd-time-wait-sync.service")
for svc in "${SERVICES[@]}"; do
  if ! systemctl is-enabled "$svc" >/dev/null 2>&1; then
    echo "Error: Service '$svc' is not enabled." >&2
    exit 1
  fi
  echo "  [OK] Service '$svc' is enabled."
done

if [ "$BYPASS_CONFIRM" = false ]; then
  echo ""
  echo "WARNING: This will purge all Wi-Fi credentials, disable SSH, lock the root"
  echo "filesystem with OverlayFS, and shut down the device."
  echo "Make sure you have tested all configurations before proceeding."
  echo ""
  read -r -p "Are you sure you want to finalize and lock the appliance? (type 'yes' to confirm): " confirm
  if [ "$confirm" != "yes" ]; then
    echo "Finalization aborted by user."
    exit 0
  fi
fi

# --- 2. System Cleanup & UTC Config ---
echo "Setting system timezone to UTC..."
timedatectl set-timezone UTC

echo "Disabling SSH service..."
systemctl disable ssh

echo "Cleaning package caches and log files..."
apt-get clean
apt-get autoremove -y
rm -rf /tmp/* /var/tmp/* /root/.cache /home/pi/.cache
find /var/log -type f -exec truncate -s 0 {} \;
journalctl --vacuum-time=1s 2>/dev/null || true

echo "Clearing bash execution history..."
rm -f /root/.bash_history /home/pi/.bash_history
history -c 2>/dev/null || true

# --- 3. Failsafe Wi-Fi Credentials Purge ---
echo "Purging all configured Wi-Fi networks and secrets..."
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
  for uuid in $(nmcli --fields UUID,TYPE connection show | awk '$2 ~ /wifi|802-11-wireless/ {print $1}'); do
    nmcli connection delete "$uuid" 2>/dev/null || true
  done
fi
rm -rf /etc/NetworkManager/system-connections/*
echo "  [OK] Wi-Fi connections and profiles purged."

# --- 4. Enable OverlayFS and Shutdown ---
echo "Enabling OverlayFS..."
raspi-config nonint enable_overlayfs

echo "=========================================================="
echo " MirrorDash Appliance successfully finalized and locked!"
echo " The system will now shut down."
echo " Once shut down, you can safely extract the golden image."
echo "=========================================================="
sleep 2

poweroff
