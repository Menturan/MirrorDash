# SPDX-License-Identifier: AGPL-3.0-only
"""The core's player may open a home-network host only if the owner typed it for that module instance (ARCHITECTURE #34)."""

import asyncio
import errno
import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mirrordash_core.features.modules import client as client_module
from mirrordash_core.features.modules.connection import Connection
from mirrordash_core.features.video import command
from mirrordash_core.home_address import is_home_address

NAS = {"nas.local"}


def answers(*ips):
    return patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 0)) for ip in ips])


async def refused(url, hosts, *ips):
    with answers(*ips), patch.object(command, "is_on_this_mirror", return_value=False), pytest.raises(ValueError):
        await command.check_host(url, hosts)


async def allowed(url, hosts, *ips):
    with answers(*ips), patch.object(command, "is_on_this_mirror", return_value=False):
        await command.check_host(url, hosts)


@pytest.mark.asyncio
async def test_a_typed_home_host_plays_and_the_same_host_not_typed_does_not():
    await allowed("http://nas.local/live.m3u8", NAS, "192.168.1.20")
    await allowed("http://192.168.1.20:8080/x.m3u8", {"192.168.1.20"}, "192.168.1.20")  # an IP literal; the port is free
    await refused("http://nas.local/live.m3u8", set(), "192.168.1.20")  # not typed
    await refused("http://other.local/live.m3u8", NAS, "192.168.1.20")  # another host than the typed one
    await allowed("http://example.org/x.m3u8", set(), "93.184.216.34")  # public: as before


@pytest.mark.asyncio
async def test_the_host_must_be_the_typed_one_exactly():
    await allowed("http://NAS.local/x", NAS, "192.168.1.20")  # case: urlsplit lower-cases, lan_hosts did too
    await refused("http://nas.local./x", NAS, "192.168.1.20")  # a trailing dot is another name
    await refused("http://nas.local.evil.example/x", NAS, "192.168.1.20")
    await refused("http://nas.local@evil.example/x", NAS, "192.168.1.20")  # nas.local is only the user name here


@pytest.mark.asyncio
async def test_a_typed_host_never_reaches_loopback_link_local_or_what_is_not_home():
    for ip in ("127.0.0.1", "::1", "0.0.0.0", "169.254.169.254", "fe80::1", "224.0.0.1", "ff02::1",
               "100.64.0.1", "::ffff:127.0.0.1", "::ffff:169.254.169.254"):
        await refused("http://nas.local/x", NAS, ip)
    await allowed("http://nas.local/x", NAS, "::ffff:192.168.1.20")  # mapped: judged as the IPv4 address
    await allowed("http://nas.local/x", NAS, "fd12:3456::20")  # a home IPv6 (unique local)


@pytest.mark.asyncio
async def test_every_answer_must_pass_and_the_mirrors_own_address_never_does():
    await refused("http://nas.local/x", NAS, "192.168.1.20", "127.0.0.1")  # mixed: home and loopback
    await refused("http://nas.local/x", set(), "224.0.0.1")  # multicast is "global" to ipaddress, and not typed either
    await refused("http://nas.local/x", NAS, "93.184.216.34", "10.0.0.1", "169.254.1.1")  # one bad answer is enough
    with answers("192.168.1.20"), patch.object(command, "is_on_this_mirror", return_value=True), \
         pytest.raises(ValueError):  # the mirror's own DHCP address
        await command.check_host("http://mirrordash.local/x", {"mirrordash.local"})


@pytest.mark.asyncio
async def test_a_name_that_is_not_found_is_refused():
    with patch("socket.getaddrinfo", side_effect=OSError), pytest.raises(ValueError):
        await command.check_host("http://nas.local/x", NAS)


def test_the_probe_knows_the_mirrors_addresses_and_fails_closed():
    assert command.is_on_this_mirror(ipaddress.ip_address("127.0.0.1"))
    assert command.is_on_this_mirror(ipaddress.ip_address("127.9.9.9"))  # all of 127/8
    assert not command.is_on_this_mirror(ipaddress.ip_address("192.0.2.1"))  # the documentation range, nobody's
    for error in (errno.EACCES, errno.EAFNOSUPPORT, errno.EINVAL):  # anything but "not mine" counts as mine
        with patch("socket.socket") as sock:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError(error, "x")
            assert command.is_on_this_mirror(ipaddress.ip_address("192.0.2.1"))
    with patch("socket.socket", side_effect=OSError(errno.EMFILE, "x")), pytest.raises(OSError):  # no socket: it raises
        command.is_on_this_mirror(ipaddress.ip_address("192.0.2.1"))


def test_the_home_address_predicate_is_shared_with_the_lan_helper():
    def home(ip):
        return is_home_address(ipaddress.ip_address(ip))
    assert home("192.168.1.2") and home("10.1.1.1") and home("172.16.0.1") and home("::ffff:10.0.0.1")
    assert not any(home(ip) for ip in ("127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "8.8.8.8", "::1", "fe80::1"))


@pytest.mark.asyncio
async def test_an_instance_gets_its_own_hosts_from_the_saved_config_not_the_modules_message():
    schema = {"properties": {"url": {"type": "string", "format": "lan-url"}}}
    saved = {"modules": {"tv": {"module": "tv", "url": "http://nas.local:8080"},
                         "cam": {"module": "tv", "url": "http://camera.local"}}}
    conn = Connection(MagicMock(), MagicMock(), None, "tv")
    conn.windows[1] = asyncio.Event()
    hold = AsyncMock()
    with patch("mirrordash_core.features.modules.schema._installed_module_schemas", return_value={"tv": schema}), \
         patch.object(client_module, "load_config", return_value=saved), patch.object(client_module.playback, "hold", hold):
        # the message names another instance and its own hosts: neither is read
        await client_module.ModuleHost()._hold_window(conn, 1, {"instance": "cam", "lan_hosts": ["camera.local"], "url": "x"})
    assert hold.await_args.args[4] == {"nas.local"}
