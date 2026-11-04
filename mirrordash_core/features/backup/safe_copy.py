# SPDX-License-Identifier: AGPL-3.0-only
"""Copying into and out of the module host's folder without following a link put there (ARCHITECTURE #41).

The module host writes its own folder, so a module could put a symlink there to the core's data. A backup
copies the modules' data out of that folder (copy_out_of_module_folder) and a restore copies it back in
(_replace_in_module_folder); both go through file descriptors with O_NOFOLLOW, so no link is followed, not
even one swapped in while the copy runs.
"""

import contextlib
import logging
import os
import shutil
import stat
from pathlib import Path

from mirrordash_core.config import (
    get_module_dir,
)

logger = logging.getLogger("mirrordash.core.backup")


# Open a folder, and fail (ELOOP/ENOTDIR) rather than follow a link in its place
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def copy_out_of_module_folder(src: Path, dst: Path, ignore) -> None:
    """shutil.copytree(src, dst, ignore=ignore) out of a folder the module host writes, through file
    descriptors (os.fwalk, O_NOFOLLOW): no symlink is followed, not even one swapped in while it copies,
    so the host can't carry the core's data into a backup. Links themselves are left out."""
    for root, dirs, files, root_fd in os.fwalk(src, follow_symlinks=False):
        target = dst / os.path.relpath(root, src)
        target.mkdir(parents=True, exist_ok=True)
        ignored = ignore(root, dirs + files)
        dirs[:] = [d for d in dirs if d not in ignored]
        for name in files:
            if name in ignored:
                continue
            try:
                # O_NONBLOCK: a FIFO put there opens at once instead of hanging, and is then skipped below
                file_descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            except OSError:  # a link (ELOOP), or gone meanwhile
                continue
            with open(file_descriptor, "rb") as source_file:
                if stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                    with open(target / name, "wb") as copy:
                        shutil.copyfileobj(source_file, copy)

def _open_dir(root: Path, parts: tuple[str, ...]) -> int:
    """root/parts... opened as a directory, each part made if missing and none followed if it is a link."""
    folder_descriptor = os.open(root, DIR_FLAGS)
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=folder_descriptor)
            parent_descriptor = folder_descriptor
            folder_descriptor = os.open(part, DIR_FLAGS, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
    except OSError:
        os.close(folder_descriptor)
        raise
    return folder_descriptor

def _copy_into_open_folder(src: Path, dir_fd: int) -> None:
    """src's files into the open directory, group-writable for the module host (pi writes them); links
    left out. Nothing at dir_fd is followed: made with O_EXCL and O_NOFOLLOW."""
    for entry in os.scandir(src):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            os.mkdir(entry.name, dir_fd=dir_fd)
            fd = os.open(entry.name, DIR_FLAGS, dir_fd=dir_fd)
            try:
                os.fchmod(fd, 0o2770)
                _copy_into_open_folder(Path(entry.path), fd)
            finally:
                os.close(fd)
        else:
            fd = os.open(entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o660, dir_fd=dir_fd)
            with open(fd, "wb") as out, open(entry.path, "rb") as f:
                os.fchmod(fd, 0o660)
                shutil.copyfileobj(f, out)

def _replace_in_module_folder(src: Path, dest: Path) -> bool:
    """Copy src over dest, in a folder the module host writes, through file descriptors: no symlink there
    is followed, not even one swapped in meanwhile (a link would have pi delete or write wherever it
    points, the core's data dir). False when a folder on the way is a link."""
    host_dir = get_module_dir()
    root, parts = (host_dir, dest.parent.relative_to(host_dir).parts) if dest.is_relative_to(host_dir) \
        else (dest.parent, ())
    try:
        parent = _open_dir(root, parts)
    except OSError as e:
        logger.error(f"{dest.parent} isn't a folder of its own ({e}): {dest.name} was not restored")
        return False
    try:
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISDIR(os.stat(dest.name, dir_fd=parent, follow_symlinks=False).st_mode):
                shutil.rmtree(dest.name, dir_fd=parent)  # fd-based: follows no link either
            else:
                os.unlink(dest.name, dir_fd=parent)
        os.mkdir(dest.name, dir_fd=parent)
        fd = os.open(dest.name, DIR_FLAGS, dir_fd=parent)
        try:
            os.fchmod(fd, 0o2770)
            _copy_into_open_folder(src, fd)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return True
