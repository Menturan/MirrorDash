# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
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
    uvicorn.run(
        "mirrordash_core.app:app",
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=20,
        ws_ping_timeout=20
    )

if __name__ == "__main__":
    main()
