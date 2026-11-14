# SPDX-License-Identifier: AGPL-3.0-only
"""What counts as an address on the owner's home network: shared by the modules' firewall addresses
(features/modules/lan.py) and the core's video player (features/video/command.py), so the two can't drift."""
import ipaddress


def is_home_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A private home-network address: never loopback, "any", link-local (the helper takes no scope, and
    169.254.169.254 is a metadata address) or multicast. An IPv4-mapped IPv6 address counts as its IPv4 one."""
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_private and not (ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast)
