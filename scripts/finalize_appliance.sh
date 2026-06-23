#!/bin/bash
# MirrorDash Appliance Finalization & Locking Script
# Runs ON THE RASPBERRY PI to lock it down.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash finalize_appliance.sh)"
  exit 1
fi

echo "=== MirrorDash Appliance Finalization & Locking ==="

# --- 1. System Cleanup & UTC Config ---
echo "Setting system timezone to UTC..."
echo "UTC" > /etc/timezone
ln -sf /usr/share/zoneinfo/UTC /etc/localtime

echo "Disabling SSH service..."
systemctl disable ssh || true

# --- 2. Failsafe Wi-Fi Credentials Purge ---
echo "Purging all configured Wi-Fi networks and secrets..."
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
  for uuid in $(nmcli --fields UUID,TYPE connection show | awk '$2 ~ /wifi|802-11-wireless/ {print $1}'); do
    nmcli connection delete "$uuid" 2>/dev/null || true
  done
fi
rm -rf /etc/NetworkManager/system-connections/* 2>/dev/null || true
echo "  [OK] Wi-Fi connections and profiles purged."

# --- 3. Enable OverlayFS and Reboot ---
echo "Enabling Hardware Read-Only OverlayFS..."
# Använd inbyggda kommandot
raspi-config nonint enable_overlayfs

echo "=========================================================="
echo " MirrorDash Appliance successfully finalized and locked!"
echo " The system will now safely reboot to apply the lock."
echo "=========================================================="

# Extremt viktigt: Tvinga sync av initramfs innan omstart!
sync
sleep 3
reboot
