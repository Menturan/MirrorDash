# SPDX-License-Identifier: AGPL-3.0-only
"""Downloading the system update's bundle: about 1 GB over a mirror's Wi-Fi, so a try that broke is
taken up where it stopped instead of started over, and a file of the wrong size is never installed."""

import shutil
import urllib.request
from pathlib import Path


def partial_file(target: Path, build: str) -> Path:
    """Where a bundle is kept while it downloads: named after its build, so only that build is taken up again."""
    return target.with_name(f"{target.stem}.{build}.part")


def download(url: str, size: int, partial: Path, target: Path) -> None:
    """Fetch url (size bytes) into target, through partial. What partial already holds is kept and the
    rest asked for (HTTP Range); a server that sends the whole file anyway starts it over. Raises OSError
    when the download breaks (partial stays, for the next try) or ends at another size than GitHub named.
    ponytail: a bundle nobody tries again stays as its .part until a newer build's download removes it."""
    for other in target.parent.glob(f"{target.stem}.*.part"):
        if other != partial:
            other.unlink(missing_ok=True)  # an older build's: never continued
    have = partial.stat().st_size if partial.exists() else 0
    if have > size:
        have = 0
    if have < size or not size:
        # GitHub sends the file from another address (a redirect); urllib follows it, the Range with it
        request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            continued = have and response.status == 206
            with open(partial, "ab" if continued else "wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
    got = partial.stat().st_size
    if size and got > size:
        partial.unlink()  # nothing to continue from
    if size and got != size:
        raise OSError(f"got {got} of {size} bytes")
    partial.replace(target)
