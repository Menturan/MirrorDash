# SPDX-License-Identifier: AGPL-3.0-only
"""The module sandbox's self-check (ARCHITECTURE #41): what a module could reach that it must not.

systemd only warns when the kernel lacks what a sandbox filter needs (cgroup BPF, the device cgroup,
namespaces), so the module host looks for itself: host.py runs sandbox_checks() as a module's user (probe_main)
when the core connects, and again from Logs -> Diagnostics. Each check is (what, reachable) and every one
should be False; while one is True the core starts no module (client.py).
"""

import contextlib
import glob
import os
import socket
import struct

from mirrordash_core.config import get_module_dir
from mirrordash_core.features.modules import forkserver
from mirrordash_core.features.modules.module_files import (
    SAFE_NAME,
    module_folders,
    module_site,
)

SANDBOX_PORTS = (80, 8000)  # the mirror's own site (nginx) and the core behind it
# The kernel's route table, to find the router, the way into the home network (/proc/net is hidden by ProcSubset=pid)
ROUTE_TABLE_FILE = "/proc/self/net/route"
# mirrordash-modules.service's InaccessiblePaths and ProtectHome
SANDBOX_PATHS = {"/storage/mirrordash/data": "the core's data", "/storage/mirrordash/ssh": "the SSH keys",
                 "/storage/mirrordash/system-connections": "the Wi-Fi passwords", "/storage/rauc": "the OS update keys",
                 "/home": "the home folders", "/run/dbus": "the system bus"}
# Its DevicePolicy=closed and the I2C rule (90-mirrordash-i2c.rules): GPIO only through the core, no other I2C bus
SANDBOX_DEVICES = ("/dev/gpiochip*", "/dev/gpiomem*", "/dev/mem", "/dev/i2c-*", "/dev/input/event*", "/dev/video*",
                   "/dev/snd/pcmC*D*c")  # a microphone; playback (pcmC*D*p) is the modules'
MODULE_DEVICES = {"/dev/i2c-1"}  # the header's I2C bus
# Where the unit's memory limit is: this process's cgroup (v2: one line "0::<path>") under the cgroup tree
OWN_CGROUP_FILE = "/proc/self/cgroup"
CGROUP_TREE = "/sys/fs/cgroup"


def can_connect_to(host: str, port: int) -> bool:
    """A connection gets through, or an answer comes back (refused): the address is in reach. A filter says EPERM."""
    try:
        socket.create_connection((host, port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    return True


def can_open_for_reading_and_writing(path: str) -> bool:
    try:
        os.close(os.open(path, os.O_RDWR | os.O_NONBLOCK))
    except OSError:
        return False
    return True


def default_gateway() -> str | None:
    """The router's address, from the kernel's route table: a header line, then a line per route whose
    Destination 00000000 is the default route, and whose Gateway is an IPv4 address as a hex number in the
    machine's byte order (little-endian on a Pi: 0100A8C0 is 192.168.0.1)."""
    with contextlib.suppress(OSError, ValueError, IndexError), open(ROUTE_TABLE_FILE) as route_table:
        for columns in (line.split() for line in route_table.readlines()[1:]):
            destination, gateway = columns[1], columns[2]
            if destination == "00000000" and gateway != "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(gateway, 16)))
    return None


def memory_limit_missing() -> bool:
    """Whether the unit's MemoryMax doesn't hold. It needs the kernel's memory controller (cgroup_enable=memory
    in cmdline.txt, off in the Pi's firmware by default), and without it systemd starts the unit anyway with a
    mere warning: one module could then use up the whole mirror's memory."""
    try:
        with open(OWN_CGROUP_FILE) as cgroup_file:
            cgroup = next(line.strip().removeprefix("0::") for line in cgroup_file if line.startswith("0::"))
        with open(f"{CGROUP_TREE}{cgroup}/memory.max") as memory_max:  # missing when the controller is off
            return memory_max.read().strip() == "max"
    except (OSError, StopIteration):
        return True


def data_folders() -> list[str]:
    """The instances' data folders, as the control process sees them (a module's user can't list them)."""
    with contextlib.suppress(OSError):
        return sorted(entry.name for entry in os.scandir(get_module_dir() / "data") if SAFE_NAME.match(entry.name))
    return []


def sandbox_checks(data_folder_names: list[str] | None = None) -> list[tuple[str, bool]]:
    """What the sandbox keeps from a module, looked at as one (probe_main): (what, reachable), each should
    be False. systemd only warns when the kernel lacks what a filter needs (cgroup BPF for IPAddressDeny, the
    device cgroup, namespaces), and a user per module holds only if its capabilities are gone and the
    folders' modes are right, so the host looks: when the core connects, and from Logs → Diagnostics.
    data_folder_names: the instances' data folders (default: those this process can list)."""
    if data_folder_names is None:
        data_folder_names = data_folders()
    checks = [(f"the mirror's own port {port}", can_connect_to("127.0.0.1", port)) for port in SANDBOX_PORTS]
    if gateway := default_gateway():
        checks.append((f"the router ({gateway})", can_connect_to(gateway, 53)))
    checks += [(f"{what} ({path})", os.access(path, os.R_OK)) for path, what in SANDBOX_PATHS.items()]
    present_devices = {device for pattern in SANDBOX_DEVICES for device in glob.glob(pattern)}
    checks += [(device, can_open_for_reading_and_writing(device)) for device in sorted(present_devices - MODULE_DEVICES)]
    checks += [(f"another module's data ({name})", os.access(get_module_dir() / "data" / name, os.R_OK))
               for name in data_folder_names]
    checks.append((f"writing the module site ({module_site()})", os.access(module_site(), os.W_OK)))
    checks.append(("writing a module's own folder", any(os.access(f, os.W_OK) for f in module_folders())))
    checks.append(("capabilities (" + (", ".join(forkserver.held_capability_sets()) or "none") + ")",
                   bool(forkserver.held_capability_sets())))
    if os.environ.get("MIRRORDASH_SANDBOX"):  # only the unit has a memory limit
        checks.append(("unlimited memory (MemoryMax doesn't hold: cgroup_enable=memory missing from cmdline.txt?)",
                       memory_limit_missing()))
    return checks
