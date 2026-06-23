#!/bin/bash
# MirrorDash Automatic Appliance Setup Script
# Runs natively inside the build container.

set -euo pipefail

# Environment safety
export LC_ALL=C.UTF-8
export LANG=C.UTF-8
export DEBIAN_FRONTEND=noninteractive

PI_USER="pi"
PI_HOME="/home/$PI_USER"
GITHUB_RAW="https://raw.githubusercontent.com/Menturan/MirrorDash/master"

echo "=== 1. Package Installation & Base Setup ==="
apt-get update
# Uppdatera existerande paket och installera Cog & Wayland (med --no-install-recommends)
apt-get upgrade -y
apt-get install -y --no-install-recommends \
    labwc \
    seatd \
    dbus-user-session \
    fonts-liberation \
    cog \
    wlr-randr \
    avahi-daemon \
    nginx \
    plymouth \
    pix-plym-splash \
    systemd-timesyncd \
    python3

echo "=== 2. Creating Appliance User & Directories ==="
# Skapa pi-användaren med rätt system- och grafikrättigheter
if ! id "$PI_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo,video,render,input,tty,plugdev,netdev "$PI_USER"
    echo "pi:raspberry" | chpasswd
fi

# Skapa struktur på persistent storage
mkdir -p /storage/mirrordash/data /storage/mirrordash/venv_a /storage/mirrordash/venv_b
chown -R "$PI_USER:$PI_USER" /storage
mkdir -p "$PI_HOME/.mirrordash/cache" "$PI_HOME/.mirrordash/data"
chown -R "$PI_USER:$PI_USER" "$PI_HOME/.mirrordash"

echo "=== 3. Configuring Fstab ==="
if ! grep -q "LABEL=mirrordash-data" /etc/fstab; then
    cat << 'EOF' >> /etc/fstab

# MirrorDash Storage Map
LABEL=mirrordash-data  /storage  ext4  defaults,noatime,commit=60,nofail,x-systemd.device-timeout=5  0  2
/storage/mirrordash/data  /home/pi/.mirrordash/data  none  bind,nofail,x-systemd.device-timeout=5  0  0
tmpfs  /home/pi/.mirrordash/cache  tmpfs  defaults,noatime,nosuid,size=100M  0  0
EOF
fi

echo "=== 4. Setting Hostname & Network ==="
echo "mirrordash" > /etc/hostname
sed -i 's/127\.0\.1\.1.*/127.0.1.1\tmirrordash/' /etc/hosts
systemctl enable avahi-daemon

echo "=== 5. Console Autologin & Plymouth ==="
# Tvinga tty1 att logga in pi automatiskt
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat << 'EOF' > /etc/systemd/system/getty@tty1.service.d/autologin.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin pi --noclear --noissue %I $TERM
EOF
ln -fs /lib/systemd/system/multi-user.target /etc/systemd/system/default.target
touch "$PI_HOME/.hushlogin"
chown "$PI_USER:$PI_USER" "$PI_HOME/.hushlogin"

echo "=== 6. Configuring Labwc & Cog (Kiosk Mode) ==="
# Autostart Wayland when logging into tty1
cat << 'EOF' > "$PI_HOME/.bash_profile"
if [[ -z $WAYLAND_DISPLAY && $XDG_VTNR -eq 1 ]]; then
  printf "\033c"
  exec labwc
fi
EOF
chown "$PI_USER:$PI_USER" "$PI_HOME/.bash_profile"

# Säkra Kiosken: Ta bort högerklicksmenyn
mkdir -p "$PI_HOME/.config/labwc"
cat << 'EOF' > "$PI_HOME/.config/labwc/rc.xml"
<?xml version="1.0"?>
<labwc_config>
  <mouse><context name="Root"><mousebind button="Right" action="Press"><action name="None" /></mousebind></context></mouse>
</labwc_config>
EOF

# Autostart Cog-webbläsaren
cat << 'EOF' > "$PI_HOME/.config/labwc/autostart"
labwc-msg HideCursor 2>/dev/null || true
while true; do
  cog -P wl file:///home/pi/mirrordash/loading.html
  sleep 2
done &
EOF
chmod +x "$PI_HOME/.config/labwc/autostart"
chown -R "$PI_USER:$PI_USER" "$PI_HOME/.config"

echo "=== 7. Enabling Critical Services ==="
systemctl enable seatd systemd-timesyncd
sed -i 's/#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=14s/' /etc/systemd/system.conf

echo "=== Setup Appliance Script Completed Successfully! ==="
