#!/bin/bash
# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# MirrorDash Boot Fallback Launcher
# Manages starting uvicorn, detecting boot crashes, and executing rollbacks.

VENV_LINK="/storage/mirrordash/venv"
GOLDEN_VENV="/home/pi/mirrordash/base_venv"
APP_DIR="/home/pi/mirrordash"

cd "$APP_DIR"

# Ensure we have a boot status env var (defaults to normal)
export MIRRORDASH_BOOT_STATUS="${MIRRORDASH_BOOT_STATUS:-normal}"

# Determine which binary to run
if [ -L "$VENV_LINK" ] && [ -d "$VENV_LINK" ]; then
    PYTHON_BIN="$VENV_LINK/bin/python"
else
    echo "Active venv symlink not found. Falling back to Golden Copy..."
    export MIRRORDASH_BOOT_STATUS="safe_mode"
    PYTHON_BIN="$GOLDEN_VENV/bin/python"
fi

echo "Starting MirrorDash using: $PYTHON_BIN (Status: $MIRRORDASH_BOOT_STATUS)"

START_TIME=$(date +%s)
"$PYTHON_BIN" -m mirrordash_core.main
EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# Standard SIGTERM exit code is 143. Exit code 0 is clean shutdown.
# If python exited with a crash code and lasted less than 10 seconds, trigger rollback.
if [ "$EXIT_CODE" -ne 0 ] && [ "$EXIT_CODE" -ne 143 ] && [ "$DURATION" -lt 10 ]; then
    echo "Boot crash detected (Exit Code: $EXIT_CODE, Duration: ${DURATION}s)."
    
    # 1. Try A/B Rollback
    if [ -d "/storage/mirrordash/venv_old" ]; then
        TARGET=$(readlink "$VENV_LINK")
        if [[ "$TARGET" == *"venv_b"* ]]; then
            OLD_DIR="venv_a"
            FAILED_DIR="venv_b"
        else
            OLD_DIR="venv_b"
            FAILED_DIR="venv_a"
        fi
        
        echo "Rolling back symlink to: $OLD_DIR..."
        
        # Clean up failed directory
        if [ -d "/storage/mirrordash/venv_failed" ]; then
            rm -rf /storage/mirrordash/venv_failed
        fi
        
        # Swap directories and restore link
        mv /storage/mirrordash/venv_old "/storage/mirrordash/$OLD_DIR"
        mv "/storage/mirrordash/$FAILED_DIR" "/storage/mirrordash/venv_failed"
        ln -sfT "$OLD_DIR" "$VENV_LINK"
        
        # Boot the rolled-back environment
        export MIRRORDASH_BOOT_STATUS="rollback"
        exec "$VENV_LINK/bin/python" -m mirrordash_core.main
    fi
    
    # 2. If A/B Rollback is not possible or also fails, run Golden Copy (Safe Mode)
    if [ "$MIRRORDASH_BOOT_STATUS" != "safe_mode" ]; then
        echo "All persistent environments failed to boot. Falling back to Golden Copy..."
        export MIRRORDASH_BOOT_STATUS="safe_mode"
        exec "$GOLDEN_VENV/bin/python" -m mirrordash_core.main
    fi
fi

exit $EXIT_CODE
