# SPDX-License-Identifier: AGPL-3.0-only

import logging
import os
import time
from logging.handlers import RotatingFileHandler

import uvicorn

from mirrordash_core.config import get_base_dir

# Logging is set up on import, not in main(): with MIRRORDASH_DEV=1 uvicorn's reload runs the app in a
# spawned process, which re-runs this module's top level but never main().
handlers: list[logging.Handler] = [logging.StreamHandler()]
file_error = None
try:
    log_dir = os.path.join(get_base_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handlers.append(RotatingFileHandler(os.path.join(log_dir, "mirrordash.log"),
                                        maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"))
except OSError as e:
    file_error = e  # console only; said below, once logging works

# Every log time is UTC in ISO 8601 (2026-10-19T08:15:02.123Z), like the system log shown with --utc and
# the saved logs: lines from different logs line up without a time zone or summer time to think of.
# Times meant for people in the admin page (when a log was saved, until when) stay local.
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03dZ [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=handlers
)
if file_error:
    logging.warning(f"Logging to the console only, the log file can't be written: {file_error}")

def main() -> None:
    # Run uvicorn server targeting the FastAPI app in app.py
    dev_mode = os.getenv("MIRRORDASH_DEV", "0") == "1"
    
    reload_kwargs = {}
    if dev_mode:
        reload_kwargs["reload"] = True
        
        # Watch the current working directory (core)
        reload_dirs = [os.getcwd()]
        
        # Also watch sibling module folders (e.g. mirrordash-modules) if present
        sibling_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "mirrordash-modules"))
        if os.path.exists(sibling_dir):
            reload_dirs.append(sibling_dir)
            
        reload_kwargs["reload_dirs"] = reload_dirs
        # A module's templates, settings and translations count as code too: saving any of them restarts
        reload_kwargs["reload_includes"] = ["*.py", "*.html", "*.json", "*.css"]
        logging.info(f"Starting server in DEVELOPMENT mode. Watching directories: {reload_dirs}")
    else:
        logging.info("Starting server in PRODUCTION mode.")

    uvicorn.run(
        "mirrordash_core.app:app",
        host="127.0.0.1",  # the home network comes in through nginx on port 80, never around it
        port=8000,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        # The Matter node polls /api/v1/status every 2 s and an open admin page its panels every 5 s:
        # tens of thousands of lines a day on a mirror. nginx in front can log requests if needed.
        access_log=dev_mode,
        **reload_kwargs
    )

if __name__ == "__main__":
    main()
