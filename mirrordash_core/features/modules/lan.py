# SPDX-License-Identifier: AGPL-3.0-only
"""What each module instance may reach on the home network (ARCHITECTURE #41).

A module's sandbox reaches nothing at home, except the addresses the owner typed in its settings marked
"format": "lan-url" (Home Assistant's address, a camera's). The core finds those settings (lan_hosts),
looks the names up (lan_addresses) and hands the addresses, per instance's user, to the OS image's helper
mirrordash-module-network through sudo (apply), which opens exactly those for exactly that user.
"""

import asyncio
import ipaddress
import logging
import os
import socket
import urllib.parse

logger = logging.getLogger("mirrordash.core.modules.client")

LAN_HELPER = "/usr/local/bin/mirrordash-module-network"  # an OS image file; the home-network addresses per module
LAN_REFRESH = 600  # seconds between looking up the lan-url hosts again (a DHCP address can change)
LAN_ADDRESS_LIMIT = 64  # the helper's


def lan_hosts(modules: dict) -> dict[str, set[str]]:
    """Each enabled instance's host names in its settings marked "format": "lan-url": what the owner typed
    (or kept) in the admin page, never a schema's default alone."""
    from mirrordash_core.features.modules.schema import _installed_module_schemas
    schemas = _installed_module_schemas()
    hosts = {}
    for instance_id, cfg in modules.items():
        if not cfg.get("enabled", True):
            continue
        schema = schemas.get(str(cfg.get("module", instance_id)).replace("-", "_")) or {}
        for value in _lan_urls(cfg, schema.get("properties")):
            host = urllib.parse.urlsplit(value if "//" in value else "//" + value).hostname
            if host:
                hosts.setdefault(instance_id, set()).add(host)
    return hosts


def _lan_urls(values, properties):
    """The addresses typed in the lan-url settings of these values: at the top, in a nested object and in a
    list's rows (a printer per row, ARCHITECTURE #54)."""
    if not isinstance(values, dict) or not isinstance(properties, dict):
        return
    for key, prop in properties.items():
        value = values.get(key)
        if not isinstance(prop, dict):
            continue
        if prop.get("format") == "lan-url" and isinstance(value, str) and value.strip():
            yield value.strip()
        elif isinstance(prop.get("properties"), dict):
            yield from _lan_urls(value, prop["properties"])
        elif isinstance(prop.get("items"), dict) and isinstance(value, list):
            for row in value:
                yield from _lan_urls(row, prop["items"].get("properties"))


async def lan_addresses(hosts: set[str]) -> dict[str, list[str]]:
    """The home-network addresses each name has now (an IP as is, a name looked up, mDNS too)."""
    loop = asyncio.get_running_loop()

    async def lookup(host: str) -> list:  # all at once: one name that doesn't answer holds the modules' start 5 s, not 5 s each
        try:
            return await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        except (OSError, TimeoutError, UnicodeError):
            logger.warning(f"Could not look up {host} for a module's home-network address")
            return []

    names = sorted(hosts)
    found = {}
    for host, infos in zip(names, await asyncio.gather(*map(lookup, names)), strict=True):
        addresses = set()
        for *_, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
            if ip.is_private and not ip.is_loopback and not ip.is_unspecified:
                addresses.add(str(ip))
        found[host] = sorted(addresses)
    return found


async def apply(modules: dict, uids: dict[str, int], current: list[str] | None) -> list[str] | None:
    """Let each instance's user (uids) reach exactly its lan-url addresses: the helper's arguments in force
    afterwards. current: those in force now; the helper runs only when they change, and off a mirror never."""
    if not os.environ.get("MIRRORDASH_MODULE_SOCKET") or not os.path.exists(LAN_HELPER):
        return current
    host_names_per_instance = {instance_id: names for instance_id, names in lan_hosts(modules).items()
                               if instance_id in uids}
    addresses_per_host_name = await lan_addresses(set().union(*host_names_per_instance.values()))
    allowed = sorted({(instance_id, address)
                      for instance_id, names in host_names_per_instance.items()
                      for name in names
                      for address in addresses_per_host_name.get(name, [])})[:LAN_ADDRESS_LIMIT]
    helper_arguments = [f"{uids[instance_id]}={address}" for instance_id, address in allowed]
    if helper_arguments == current:
        return current
    process = await asyncio.create_subprocess_exec("sudo", "-n", LAN_HELPER, *helper_arguments,
                                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await process.communicate()
    if process.returncode == 0:
        logger.info("Modules may reach on the home network: "
                    + (", ".join(f"{instance_id} {address}" for instance_id, address in allowed) or "nothing"))
        return helper_arguments
    logger.error(f"Could not set the modules' home-network addresses: {err.decode(errors='replace').strip()}")
    return current
