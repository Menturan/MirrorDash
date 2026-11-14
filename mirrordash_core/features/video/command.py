# SPDX-License-Identifier: AGPL-3.0-only
"""What a module may ask the core to play (ARCHITECTURE #34), checked, and the command that plays it.

A module is untrusted and the player runs as the core's user with the screen and the decoder, so every
value the module sends is checked here, at the one place it crosses over: the address (http or https
only, to a public host, or to a home-network host the owner typed in that module's settings), the output (a name from OUTPUTS, never a device string) and the volume.
Nothing goes through a shell, and the address is a single argument that play.py reads as a value.

Why the core runs the player and the module only asks (checked on a real mirror, 2026-11-13, as the module's
uid 70100): it gets EACCES on /dev/video10 (root:video 660, the uid isn't in video) and on /run/user/1000
(pi, 0700, also hidden by ProtectHome) and on connect() to wayland-0. The unit's DevicePolicy=closed allows
no /dev/video* either, but that was read from the unit's properties, not tried from inside its cgroup."""
import asyncio
import errno
import ipaddress
import math
import socket
from pathlib import Path
from urllib.parse import urlsplit

from mirrordash_core.home_address import is_home_address

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


def is_on_this_mirror(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if an interface of this mirror has the address (its DHCP address, loopback, any 127/8 address).
    The kernel only lets a socket bind an address it owns (EADDRNOTAVAIL otherwise), so that is the one error
    that means "not mine"; every other outcome counts as mine, so this fails closed (no IPv6, ip_nonlocal_bind)."""
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((str(address), 0))
        except OSError as e:
            return e.errno != errno.EADDRNOTAVAIL
    return True


async def check_host(url: str, lan_hosts: frozenset[str] | set[str] = frozenset()) -> None:
    """Refuse an address that goes to this mirror or the home network, unless the owner typed that host in a
    lan-url setting of the module instance that asks (lan_hosts: modules/lan.py's, from the saved config and
    this instance's connection, never from the module's message). Every address the name has must pass:
    a global one, or, for a typed host, a home-network address (home_address.py) that isn't this mirror's
    own: the player runs as the core's user outside the sandbox, so the firewall rule that keeps modules off
    the mirror's own services doesn't bind it. The port doesn't matter: lan-url opens an address, not a port (#41).
    The host is compared as urlsplit gives it (lower case), like lan_hosts took it; a trailing dot or another
    spelling of a typed name doesn't match and is refused.
    ponytail: checked at every start, once per start. The player resolves the name itself, so a DNS answer that
    changes after the check (rebinding of a typed name; an IP literal has no such gap), a redirect and an HLS
    playlist that names other hosts aren't checked. Upgrade: play.py (which already rewrites the master's
    addresses) refuses a redirect or an entry whose host isn't one it was handed, and the source's
    automatic-redirect is turned off."""
    host = urlsplit(url).hostname
    typed = host in lan_hosts
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        raise ValueError("the address of the stream isn't found") from None
    for *_, (ip, *_rest) in found:
        address = ipaddress.ip_address(ip.split("%")[0])
        address = getattr(address, "ipv4_mapped", None) or address
        if address.is_global and not address.is_multicast:  # 224.0.0.1 counts as global to ipaddress
            continue
        if not (typed and is_home_address(address) and not is_on_this_mirror(address)):
            raise ValueError("the stream is on this mirror or the home network, which a module may not play from "
                             "unless you typed its address in the module's settings")


def build_command(url: str, output: str, volume: float, fullscreen: bool) -> list[str]:
    """The player's command: setpriv makes it die with the core, even one that is killed."""
    return ["setpriv", "--pdeathsig", "KILL", "--", SYSTEM_PYTHON, "-I", str(PLAY), f"--uri={url}",
            f"--output={OUTPUTS[output]}", f"--volume={volume}"] + (["--fullscreen"] if fullscreen else [])
