#!/bin/bash
# MirrorDash Failsafe Golden Image Extraction Script
# Runs on Linux workstation to safely extract and shrink the production MirrorDash OS image.

set -e

# Ensure running as root for dd and pishrink
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash extract_golden_image.sh)"
  exit 1
fi

echo "=== MirrorDash Failsafe Image Extraction ==="
echo "Scanning for connected MirrorDash storage devices..."

# Find all partitions labeled "mirrordash-data"
MAPFILE=()
while IFS= read -r line; do
  if [ -n "$line" ]; then
    MAPFILE+=("$line")
  fi
done < <(lsblk -ln -o NAME,LABEL | grep "mirrordash-data" | awk '{print $1}')

NUM_DEVICES=${#MAPFILE[@]}
SD_CARD_DEV=""

if [ "$NUM_DEVICES" -eq 0 ]; then
  echo "No partitions labeled 'mirrordash-data' were automatically detected."
  read -r -p "Would you like to manually enter the SD card device path? (e.g. /dev/sdb) (y/N): " manual_entry
  if [[ ! "$manual_entry" =~ ^[yY]$ ]]; then
    echo "Exiting."
    exit 0
  fi
  read -r -p "Enter device path (e.g. /dev/sdb or /dev/mmcblk0): " manual_path
  if [ ! -b "$manual_path" ]; then
    echo "Error: '$manual_path' is not a valid block device." >&2
    exit 1
  fi
  SD_CARD_DEV="$manual_path"
elif [ "$NUM_DEVICES" -eq 1 ]; then
  PARTITION="${MAPFILE[0]}"
  # Find parent disk
  PARENT_NAME=$(lsblk -no pkname "/dev/$PARTITION" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$PARENT_NAME" ]; then
    SD_CARD_DEV="/dev/$PARENT_NAME"
  else
    if [[ "$PARTITION" =~ p[0-9]+$ ]]; then
      SD_CARD_DEV="/dev/${PARTITION%p[0-9]*}"
    else
      SD_CARD_DEV="/dev/${PARTITION%[0-9]*}"
    fi
  fi
  echo "Automatically detected MirrorDash SD card at: $SD_CARD_DEV"
else
  echo "Multiple MirrorDash SD cards detected:"
  DEVICES_LIST=()
  for i in "${!MAPFILE[@]}"; do
    PART="${MAPFILE[$i]}"
    P_NAME=$(lsblk -no pkname "/dev/$PART" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$P_NAME" ]; then
      DEV="/dev/$P_NAME"
    else
      if [[ "$PART" =~ p[0-9]+$ ]]; then
        DEV="/dev/${PART%p[0-9]*}"
      else
        DEV="/dev/${PART%[0-9]*}"
      fi
    fi
    # Avoid duplicate parent devices in list
    if [[ ! " ${DEVICES_LIST[*]} " == *" ${DEV} "* ]]; then
      DEVICES_LIST+=("$DEV")
    fi
  done
  
  for idx in "${!DEVICES_LIST[@]}"; do
    DEV_PATH="${DEVICES_LIST[$idx]}"
    DEV_MODEL=$(lsblk -dno MODEL "$DEV_PATH" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    DEV_SIZE=$(lsblk -dno SIZE "$DEV_PATH" 2>/dev/null | tr -d '[:space:]')
    echo "  $((idx + 1))) $DEV_PATH (Size: $DEV_SIZE, Model: ${DEV_MODEL:-Unknown})"
  done
  
  read -r -p "Select device number (1-${#DEVICES_LIST[@]}): " selection
  if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ] || [ "$selection" -gt "${#DEVICES_LIST[@]}" ]; then
    echo "Error: Invalid selection." >&2
    exit 1
  fi
  SD_CARD_DEV="${DEVICES_LIST[$((selection - 1))]}"
fi

# Print device details and double check
MODEL=$(lsblk -dno MODEL "$SD_CARD_DEV" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
SIZE=$(lsblk -dno SIZE "$SD_CARD_DEV" 2>/dev/null | tr -d '[:space:]')

# Determine output directory
OUT_DIR=""
if [ -n "$1" ]; then
  OUT_DIR="$1"
fi

if [ -z "$OUT_DIR" ]; then
  read -r -p "Enter directory where you want to save the output files (press Enter for current directory [./]): " user_out_dir
  if [ -n "$user_out_dir" ]; then
    OUT_DIR="$user_out_dir"
  else
    OUT_DIR="."
  fi
fi

# Resolve absolute path and verify directory exists and is writable
if [ ! -d "$OUT_DIR" ]; then
  echo "Error: Directory '$OUT_DIR' does not exist." >&2
  exit 1
fi
if [ ! -w "$OUT_DIR" ]; then
  echo "Error: Directory '$OUT_DIR' is not writable." >&2
  exit 1
fi
OUT_DIR="${OUT_DIR%/}"

# Check available disk space before proceeding
SD_CARD_SIZE_BYTES=$(lsblk -dbno SIZE "$SD_CARD_DEV" 2>/dev/null | head -n1 | tr -d '[:space:]')
if [ -z "$SD_CARD_SIZE_BYTES" ]; then
  SD_CARD_SIZE_BYTES=$(blockdev --getsize64 "$SD_CARD_DEV" 2>/dev/null || echo 0)
fi

# We need space for the raw image (SD card size) plus a buffer for the shrunken image (~6GB)
BUFFER_BYTES=$((6 * 1024 * 1024 * 1024))
TOTAL_REQUIRED_BYTES=$((SD_CARD_SIZE_BYTES + BUFFER_BYTES))

# Get available disk space on the target directory's filesystem in bytes
FREE_BLOCKS=$(df -P "$OUT_DIR" 2>/dev/null | tail -1 | awk '{print $4}')
FREE_SPACE_BYTES=$((FREE_BLOCKS * 1024))

# Convert to human-readable GB string
to_gb_str() {
  local bytes=$1
  local gb=$((bytes / 1073741824))
  local tenths=$(( (bytes % 1073741824) * 10 / 1073741824 ))
  echo "${gb}.${tenths} GB"
}

SD_STR=$(to_gb_str "$SD_CARD_SIZE_BYTES")
REQ_STR=$(to_gb_str "$TOTAL_REQUIRED_BYTES")
FREE_STR=$(to_gb_str "$FREE_SPACE_BYTES")

if [ "$FREE_SPACE_BYTES" -lt "$TOTAL_REQUIRED_BYTES" ]; then
  echo "Error: Insufficient disk space in '$OUT_DIR'." >&2
  echo "  SD Card Size:          $SD_STR" >&2
  echo "  Required (with buffer): $REQ_STR" >&2
  echo "  Available Space:        $FREE_STR" >&2
  exit 1
fi

RAW_IMG="${OUT_DIR}/mirrordash-raw.img"
FINAL_IMG="${OUT_DIR}/mirrordash-final.img"
FINAL_GZ="${FINAL_IMG}.gz"

echo ""
echo "----------------------------------------------------------"
echo "SELECTED TARGET DEVICE:"
echo "Device Node: $SD_CARD_DEV"
echo "Size:        $SIZE"
echo "Model:       ${MODEL:-Unknown}"
echo "----------------------------------------------------------"
echo "OUTPUT TARGETS:"
echo "Raw Image File:   $RAW_IMG"
echo "Compressed Image: $FINAL_GZ"
echo "----------------------------------------------------------"
echo "WARNING: All data in the output images will be overwritten."
echo "Ensure you have selected the correct device to avoid data loss."
echo ""
read -r -p "Are you sure you want to extract the image? (type 'yes' to confirm): " final_confirm
if [ "$final_confirm" != "yes" ]; then
  echo "Extraction aborted by user."
  exit 0
fi

# Remove existing temporary raw image if any
rm -f "$RAW_IMG"

echo ""
echo "=== Step 1: Extracting raw block image using dd ==="
echo "Reading from $SD_CARD_DEV..."
dd if="$SD_CARD_DEV" of="$RAW_IMG" bs=4M status=progress conv=fsync

# Setup PiShrink
if [ ! -f pishrink.sh ]; then
  echo ""
  echo "=== Step 2: Downloading PiShrink ==="
  wget -O pishrink.sh https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh
  chmod +x pishrink.sh
fi

echo ""
echo "=== Step 3: Shrinking and compressing the image ==="
# -z: gzip compress the output image. Do NOT use -a (auto-expand) because
# our auto-expand service on the appliance handles partition 3 scaling correctly.
./pishrink.sh -z "$RAW_IMG" "$FINAL_IMG"

# Clean up temporary raw image to free up workstation disk space
echo "Cleaning up temporary raw image..."
rm -f "$RAW_IMG"

echo ""
echo "=== Extraction Complete! ==="
echo "Your compressed golden image is ready:"
ls -lh "$FINAL_GZ"
echo "You can flash '$FINAL_GZ' directly using Raspberry Pi Imager."
