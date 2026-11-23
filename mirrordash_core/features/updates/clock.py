# SPDX-License-Identifier: AGPL-3.0-only
"""The clock module is always there: the image installs it into the core's venv (configure_image.sh), so
every venv built fresh (a rebuild, a new Python) gets it again here, the way the image installs it."""

import asyncio
import logging
import re
import urllib.request
from pathlib import Path

from mirrordash_core import notifications
from mirrordash_core.features.backup.service import venv_distributions
from mirrordash_core.venv import uv_pip

logger = logging.getLogger("mirrordash.core.updates")

CLOCK_PACKAGE = "mirrordash-clock"
CLOCK_REPOSITORY = "https://github.com/Menturan/mirrordash-clock"


def _latest_release() -> str:
    """The tag of the clock's newest release (configure_image.sh asks the same): GitHub sends /releases/latest on to its tag."""
    request = urllib.request.Request(f"{CLOCK_REPOSITORY}/releases/latest", method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        tag = response.url.rsplit("/", 1)[-1]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"no release of {CLOCK_PACKAGE} found: {tag!r}")
    return tag


async def ensure_clock(python: str) -> None:
    """Install the clock into the venv `python` belongs to unless it has it. Never raises: a clock that
    can't be installed (no network) is logged and told to the owner, and the rebuild goes on without it.
    ponytail: the image has no offline copy to take it from (base_venv holds it as installed files only),
    so this needs the network; a copy of base_venv's files would work offline."""
    venv = Path(python).parent.parent
    if next(iter(venv_distributions(venv, name=CLOCK_PACKAGE)), None):
        return
    try:
        tag = await asyncio.to_thread(_latest_release)
        code, _, err = await uv_pip(python, "install", f"git+{CLOCK_REPOSITORY}.git@{tag}", timeout=300)
        if code != 0:
            raise RuntimeError(err.strip() or "uv pip install failed")
    except Exception as e:
        logger.error(f"The clock module could not be installed into the new environment: {e}")
        notifications.add("warning", "The clock module could not be installed again after the rebuild. "
                          "Install it from Modules when the mirror is online.")
        return
    logger.info(f"The clock module {tag} was missing from the new environment and is installed again")
