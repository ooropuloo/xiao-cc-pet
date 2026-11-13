#!/bin/sh
# Build and flash the round-display firmware.
#   sh flash.sh            USB (default COM13)
#   sh flash.sh COM7       USB on another port
#   sh flash.sh ota [host] over Wi-Fi to http://<host>/update (default xiao-player.local)
# The partition scheme must stay min_spiffs: two 1.9MB app slots so OTA works (firmware is > 1.2MB).
# USB needs host/player.py stopped (it may hold the COM port); OTA doesn't.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
FQBN=esp32:esp32:XIAO_ESP32C3:CDCOnBoot=default,PartitionScheme=min_spiffs
cd "$ROOT/tools"

if [ "$1" = "ota" ]; then
  HOST="${2:-xiao-player.local}"
  OUT="$ROOT/build"
  ./arduino-cli.exe compile -b "$FQBN" --output-dir "$OUT" ../firmware/ytplayer
  SECRETS="$ROOT/firmware/ytplayer/secrets.h"
  USER=$(sed -n 's/^#define OTA_USER "\(.*\)"/\1/p' "$SECRETS")
  PASS=$(sed -n 's/^#define OTA_PASS "\(.*\)"/\1/p' "$SECRETS")
  echo "uploading $OUT/ytplayer.ino.bin to http://$HOST/update"
  # HTTPUpdateServer has a CSRF check: Origin must equal http://<Host>, else it answers 401
  curl -sS --fail -m 120 -H "Origin: http://$HOST" -u "$USER:$PASS" -F "update=@$OUT/ytplayer.ino.bin" "http://$HOST/update"
  echo
  echo "OTA upload done; the board reboots into the new firmware"
else
  ./arduino-cli.exe compile -u -p "${1:-COM13}" -b "$FQBN" ../firmware/ytplayer
fi
