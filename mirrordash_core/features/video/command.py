# SPDX-License-Identifier: AGPL-3.0-only
"""What a module may ask the core to play (ARCHITECTURE #34), checked, and the command that plays it.

A module is untrusted and the player runs as the core's user with the screen and the decoder, so every
value the module sends is checked here, at the one place it crosses over: the address (http or https
only, to a public host), the output (a name from OUTPUTS, never a device string) and the volume.
Nothing goes through a shell, and the address is a single argument that play.py reads as a value."""
import asyncio
import ipaddress
import math
from pathlib import Path
from urllib.parse import urlsplit

SYSTEM_PYTHON = "/usr/bin/python3"  # the one with GStreamer's bindings (python3-gi in the OS image)
PLAY = Path(__file__).with_name("play.py")
# The same names and devices as the Radio module; HDMI plays without an error to a screen without speakers
OUTPUTS = {"jack": "plughw:CARD=Headphones,DEV=0", "hdmi": "hdmi:CARD=vc4hdmi,DEV=0"}
URL_LIMIT = 2048


def check_volume(volume: object) -> float:
    """The volume as a number from 0 to 1 (playbin3's volume, linear). 0 keeps the audio chain on purpose:
    a live stream without one drops every frame (MEASUREMENTS.md, "Step 0")."""
    if isinstance(volume, bool) or not isinstance(volume, int | float) or not math.isfinite(volume):
        raise ValueError("volume must be a number from 0 to 1")
    return min(max(float(volume), 0.0), 1.0)


def check_output(output: object) -> str:
    if not isinstance(output, str) or output not in OUTPUTS:
        raise ValueError(f"output must be one of {', '.join(OUTPUTS)}")
    return output


def check_url(url: object) -> str:
    """The address if it is an http(s) one; file:, a flag or any other scheme are refused."""
    if not isinstance(url, str) or not url.isascii() or not url.isprintable() or " " in url or len(url) > URL_LIMIT:
        raise ValueError("url must be an address without spaces")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must start with http:// or https://")
    return url


async def check_host_is_public(url: str) -> None:
    """Refuse an address that goes to this mirror or the home network: a module may not reach the LAN
    (modules/lan.py), so it may not ask the core to. ponytail: checked once, here; a redirect or a DNS answer
    that changes afterwards isn't. Upgrade: allow the hosts of the instance's lan-url settings, and pin
    the address the check saw."""
    host = urlsplit(url).hostname
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        raise ValueError("the address of the stream isn't found") from None
    for *_, (ip, *_rest) in found:
        address = ipaddress.ip_address(ip.split("%")[0])
        if not (getattr(address, "ipv4_mapped", None) or address).is_global:
            raise ValueError("the stream is on this mirror or the home network, which a module may not play from")


def build_command(url: str, output: str, volume: float, fullscreen: bool) -> list[str]:
    """The player's command: setpriv makes it die with the core, even one that is killed."""
    return ["setpriv", "--pdeathsig", "KILL", "--", SYSTEM_PYTHON, "-I", str(PLAY), f"--uri={url}",
            f"--output={OUTPUTS[output]}", f"--volume={volume}"] + (["--fullscreen"] if fullscreen else [])
