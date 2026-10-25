#!/bin/bash
# MirrorDash Boot Fallback Launcher
# Manages starting uvicorn, detecting boot crashes, and executing rollbacks.

VENV_LINK="/storage/mirrordash/venv"
GOLDEN_VENV="/home/pi/mirrordash/base_venv"
APP_DIR="/home/pi/mirrordash"

cd "$APP_DIR" || exit 1

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

# Standard SIGTERM exit code is 143, SIGINT is 130. Exit code 0 is clean shutdown.
# If python exited with a crash code and lasted less than 10 seconds, trigger rollback.
if [ "$EXIT_CODE" -ne 0 ] && [ "$EXIT_CODE" -ne 143 ] && [ "$EXIT_CODE" -ne 130 ] && [ "$DURATION" -lt 10 ]; then
    echo "Boot crash detected (Exit Code: $EXIT_CODE, Duration: ${DURATION}s)."

    # On the trial boot of a new OS version the crash is the new OS's doing, not the app's: leave the
    # venvs alone and keep failing, so /health never answers, mirrordash-mark-good gives up and the
    # mirror goes back to the old OS with MirrorDash as it was (ARCHITECTURE #31). A rollback here
    # would downgrade the app for good, and Safe mode would even get the broken OS kept.
    TRIAL=/usr/local/lib/mirrordash/rauc-bootloader
    if [ -x "$TRIAL" ] && "$TRIAL" is-update-trial; then
        echo "This is a trial boot of a new system version: not rolling MirrorDash back."
        exit $EXIT_CODE
    fi

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
        
        # A Python upgrade switches the module site in with the venv (updates/service.py) and leaves the
        # old one as it was: switch back to it too, or modules with compiled code stop. Not after an
        # update on the same Python, which leaves the site alone.
        SITE="/storage/mirrordash/modules/site"
        if [ -L "$SITE" ] && [ "$(ls /storage/mirrordash/venv_old/lib 2>/dev/null)" != "$(ls "/storage/mirrordash/$FAILED_DIR/lib" 2>/dev/null)" ]; then
            if [ "$(readlink "$SITE")" = "site_b" ]; then OLD_SITE="site_a"; else OLD_SITE="site_b"; fi
            if [ -d "/storage/mirrordash/modules/$OLD_SITE" ]; then ln -sfT "$OLD_SITE" "$SITE"; fi
        fi

        # Clean up failed directory
        if [ -d "/storage/mirrordash/venv_failed" ]; then
            rm -rf /storage/mirrordash/venv_failed
        fi
        
        # Swap directories and restore link atomically
        rm -rf "/storage/mirrordash/$OLD_DIR" 2>/dev/null || true
        mv -T "/storage/mirrordash/venv_old" "/storage/mirrordash/$OLD_DIR"
        mv -T "/storage/mirrordash/$FAILED_DIR" "/storage/mirrordash/venv_failed"
        ln -sfT "$OLD_DIR" "$VENV_LINK"

        # Keep the crashed start's log for the admin page (Logs): the system log is in RAM only and
        # gone at the next restart. Only on a MirrorDash image (save-log: scripts/image/rootfs/usr/local/lib/mirrordash/save-log).
        if [ -x /usr/local/lib/mirrordash/save-log ]; then
            /usr/local/lib/mirrordash/save-log app-rollback -u mirrordash -b -n 500 || true
        fi

        # Boot the rolled-back environment
        export MIRRORDASH_BOOT_STATUS="rollback"
        exec "$VENV_LINK/bin/python" -m mirrordash_core.main
    fi
    
    # 2. If A/B Rollback is not possible or also fails, run Golden Copy (Safe Mode)
    if [ "$MIRRORDASH_BOOT_STATUS" != "safe_mode" ]; then
        echo "All persistent environments failed to boot. Falling back to Golden Copy..."
        # Keep the crashed start's log for the admin page, as for a rollback above
        if [ -x /usr/local/lib/mirrordash/save-log ]; then
            /usr/local/lib/mirrordash/save-log app-safe-mode -u mirrordash -b -n 500 || true
        fi
        export MIRRORDASH_BOOT_STATUS="safe_mode"
        exec "$GOLDEN_VENV/bin/python" -m mirrordash_core.main
    fi
fi

exit $EXIT_CODE
