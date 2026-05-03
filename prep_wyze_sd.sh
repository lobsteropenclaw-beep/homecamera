#!/bin/bash
# Run this on the HOST Mac Mini Terminal (not inside the VM)
# Preps a microSD card for Wyze V2 custom firmware (Dafang-Hacks + official RTSP)

set -e

SD="/Volumes/NO NAME"

# Confirm SD card is present
if [ ! -d "$SD" ]; then
    echo "[✗] SD card not found at '$SD'"
    echo "[i] Check mounted volumes with: ls /Volumes/"
    exit 1
fi

echo "--- Wyze V2 SD Card Prep ---"
echo "[i] SD card found at: $SD"
echo "[i] Current contents:"
ls "$SD"
echo ""

# 1. Clear previous firmware files
echo "[...] Clearing old files..."
rm -rf \
    "$SD/demo.bin" \
    "$SD/autoupdate.sh" \
    "$SD/bin" \
    "$SD/config" \
    "$SD/controlscripts" \
    "$SD/driver" \
    "$SD/driver_t20l" \
    "$SD/etc" \
    "$SD/fonts" \
    "$SD/hls" \
    "$SD/lib" \
    "$SD/media" \
    "$SD/root" \
    "$SD/run.sh" \
    "$SD/scripts" \
    "$SD/www" \
    "$SD/alarm" \
    "$SD/cores" \
    "$SD/record" \
    "$SD/time_lapse" \
    "$SD/rebootlog" \
    "$SD/"uEnv.*.txt \
    "$SD/"*.txt \
    2>/dev/null || true
echo "[✓] Old files cleared"

# 2. Download official Wyze V2 RTSP firmware
echo "[...] Downloading official Wyze V2 RTSP firmware..."
curl -L --progress-bar -o /tmp/wyze_rtsp.zip \
    "https://download.wyzecam.com/firmware/rtsp/demo_v2_rtsp_4.28.4.49.bin.zip"
unzip -o /tmp/wyze_rtsp.zip -d /tmp/wyze_rtsp/
cp /tmp/wyze_rtsp/demo_v2_rtsp_4.28.4.49.bin "$SD/demo.bin"
echo "[✓] demo.bin copied ($(du -sh "$SD/demo.bin" | cut -f1))"

# 3. Download Dafang-Hacks firmware_mod
echo "[...] Downloading Dafang-Hacks firmware_mod..."
curl -L --progress-bar -o /tmp/dafang.zip \
    "https://github.com/EliasKotlyar/Xiaomi-Dafang-Hacks/archive/refs/heads/master.zip"
unzip -o /tmp/dafang.zip -d /tmp/dafang/
rsync -a --no-perms /tmp/dafang/Xiaomi-Dafang-Hacks-master/firmware_mod/ "$SD/"
echo "[✓] Dafang-Hacks firmware_mod copied"

# 4. Write WiFi config
echo "[...] Writing WiFi config..."
cat > "$SD/config/wpa_supplicant.conf" << 'EOF'
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=0
ap_scan=1

network={
	ssid="Rogers78956"
	key_mgmt=WPA-PSK
	pairwise=CCMP TKIP
	group=CCMP TKIP WEP104 WEP40
	psk="A638ARogers"
	priority=2
}
EOF
echo "[✓] wpa_supplicant.conf written (Rogers78956)"

# 5. Final check
echo ""
echo "[✓] SD card ready. Contents:"
ls "$SD"
echo ""

# 6. Eject safely
diskutil eject "$SD" && echo "[✓] SD card ejected safely" || echo "[!] Eject failed — eject manually before removing"

echo ""
echo "--- Next steps ---"
echo "1. Insert SD card into Wyze V2"
echo "2. Hold setup button, plug in power, keep holding ~10s until light flashes yellow"
echo "3. Release — camera flashes firmware (rapid yellow blink), then reboots"
echo "4. After reboot it will connect to Rogers78956 and appear on your network"
