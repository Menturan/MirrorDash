# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import uvicorn

import os
from logging.handlers import RotatingFileHandler

from mirrordash_core.config import get_base_dir

# Pre-create logs directory and configure file logging in addition to console
log_dir = os.path.join(get_base_dir(), "logs")
try:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "mirrordash.log")
except Exception:
    log_file = None

handlers = []
console_handler = logging.StreamHandler()
handlers.append(console_handler)

if log_file:
    try:
        file_handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
        handlers.append(file_handler)
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=handlers
)

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
        logging.info(f"Starting server in DEVELOPMENT mode. Watching directories: {reload_dirs}")
    else:
        logging.info("Starting server in PRODUCTION mode.")

    uvicorn.run(
        "mirrordash_core.app:app",
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        **reload_kwargs
    )

if __name__ == "__main__":
    main()
