# SPDX-License-Identifier: AGPL-3.0-only
"""Files the owner uploads for a module: a setting of `"format": "files"` in its config_schema.json
(ARCHITECTURE #44). A module can't take them itself (its routes share the admin page's origin, so they
answer sandboxed and without the owner's cookie, #40), so the logged-in admin page sends them here and
the core puts them in the instance's cache folder, `cache/<instance>/files/<key>/`, where the module
reads them. Never in backups: the cache isn't copied. The admin page prepares them first (an image is
shrunk to the screen in the browser, admin.js), so the core only checks what arrives.

The core (pi) writes in a folder the instance's user owns once it has started (the forkserver gives it
the folder and all in it), so nothing there is trusted: every step goes through file descriptors and
O_NOFOLLOW, a new file is made with O_EXCL and linked in under a name that doesn't exist yet, and a link
the module put there is never followed (as backup/service.py does)."""

import contextlib
import logging
import os
import re
import secrets
import shutil
import stat

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response

from mirrordash_core.admin import require_api_key
from mirrordash_core.config import get_module_dir, load_config
from mirrordash_core.features.modules.module_files import SAFE_NAME, find_entry_point
from mirrordash_core.features.settings.schema import get_module_schema
from mirrordash_core.features.video.window import current_size
from mirrordash_core.forms import SAFE_KEY
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.modules.files")
router = APIRouter(prefix="/admin/modules", dependencies=[Depends(require_api_key)])

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# files/ and files/<key>/: the instance reads them as "other" (it isn't in pi's group, forkserver.py
# switch_to_user); no other instance can, as the instance's own folder (0770) is closed to them
FOLDER_MODE, FILE_MODE = 0o2775, 0o664
DEFAULT_MAX_FILES, MAX_FILES_CAP = 500, 2000
DEFAULT_SIDE, MAX_SIDE = 1920, 3840  # the longest side an image is shrunk to: the screen's, within these
MAX_DIMENSION = 4096  # an uploaded image wider or taller than this was never shrunk by the admin page
NAME = re.compile(r"^[A-Za-z0-9_-]{1,110}\.[a-z0-9]{1,5}$")
# A JPEG's frame header markers (SOF0-SOF15 but DHT, JPG and DAC), which hold its size
SOF_MARKERS = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a whole JPEG's frame header, or None when data isn't one."""
    if data[:3] != b"\xff\xd8\xff" or data[-2:] != b"\xff\xd9":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # a fill byte
            i += 1
            continue
        if marker in SOF_MARKERS:
            if i + 9 > len(data):
                return None
            return int.from_bytes(data[i + 7:i + 9]), int.from_bytes(data[i + 5:i + 7])
        if marker == 0xDA:  # the scan, with no frame header before it
            return None
        i += 2 + int.from_bytes(data[i + 2:i + 4])
    return None


def is_image(data: bytes) -> bool:
    """A JPEG of a size the admin page can have made. ponytail: the header only, never decoded (the
    owner, logged in, is the only sender); Pillow on the Pi is the upgrade path if a real check is needed."""
    size = jpeg_size(data)
    return size is not None and all(0 < side <= MAX_DIMENSION for side in size)


THUMB_MAX_SIDE, THUMB_MAX_BYTES = 256, 64 * 1024  # the admin page's small copy (240 px, ~15 kB)


def is_thumb(data: bytes) -> bool:
    size = jpeg_size(data)
    return len(data) <= THUMB_MAX_BYTES and size is not None and all(0 < side <= THUMB_MAX_SIDE for side in size)


# One entry per kind of file the core takes, named by a setting's "accept". ponytail: images only; a new
# kind is one entry here (its extension, size cap, content check, whether the admin page sends a small
# copy for the field's thumbnails) plus how admin.js prepares and shows it.
FILE_KINDS = {"image": {"extension": ".jpg", "max_bytes": 4 * 1024 * 1024, "check": is_image, "thumbs": True}}


def safe_name(name: str, extension: str) -> str:
    """The owner's file name, made safe: its extension replaced by the kind's, every character but letters,
    digits, - and _ made _ (so no dot, slash or link trick), at most 100. "../a b.HEIC" -> "_a_b.jpg"."""
    stem = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:100].strip("_") or "file"
    return stem + extension


def files_setting(instance_id: str, key: str) -> tuple[dict, dict]:
    """(the setting's schema, its kind of file) for an instance's files setting; HTTPException when the
    instance isn't saved, the key isn't a files setting of its module, or the core doesn't take its kind."""
    if not SAFE_NAME.match(instance_id) or not SAFE_KEY.match(key):
        raise HTTPException(status_code=404, detail="No such setting")
    instance = load_config().get("modules", {}).get(instance_id)
    if not isinstance(instance, dict):
        raise HTTPException(status_code=404, detail="Save the settings first, then add files")
    ep = find_entry_point(instance.get("module", instance_id))
    prop = ((get_module_schema(ep) if ep else None) or {}).get("properties", {}).get(key)
    if not isinstance(prop, dict) or prop.get("format") != "files":
        raise HTTPException(status_code=404, detail="No such setting")
    kind = FILE_KINDS.get(prop.get("accept"))
    if kind is None:
        raise HTTPException(status_code=415, detail="This kind of file isn't supported by this MirrorDash version")
    return prop, kind


def max_files(prop: dict) -> int:
    value = prop.get("max_files")
    return min(value, MAX_FILES_CAP) if isinstance(value, int) and value > 0 else DEFAULT_MAX_FILES


def open_folder(instance_id: str, key: str, create: bool, thumbs: bool = False) -> int:
    """cache/<instance>/files/<key> (thumbs: files/.thumbs/<key>, the admin page's small copies, which the
    module never reads) as a directory descriptor, each part made if missing (create) and none followed if
    it is a link. FileNotFoundError when it isn't there and create is False."""
    module_dir = get_module_dir()
    if create:
        module_dir.mkdir(parents=True, exist_ok=True)
    folder = os.open(module_dir, DIR_FLAGS)
    parts = [("cache", None), (instance_id, None), ("files", FOLDER_MODE),
             *([(".thumbs", FOLDER_MODE)] if thumbs else []), (key, FOLDER_MODE)]
    try:
        for part, mode in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o770, dir_fd=folder)
            parent, folder = folder, os.open(part, DIR_FLAGS, dir_fd=folder)
            os.close(parent)
            # Its mode only while it's pi's: after the instance's start it's the instance's (modes kept)
            if create and mode and os.fstat(folder).st_uid == os.getuid():
                os.fchmod(folder, mode)
    except OSError:
        os.close(folder)
        raise
    return folder


def list_files(folder: int) -> list[dict]:
    """The folder's files, oldest first; links and anything not named as the core names files left out."""
    entries = []
    with os.scandir(folder) as scan:
        for entry in scan:
            if NAME.match(entry.name) and entry.is_file(follow_symlinks=False):
                info = entry.stat(follow_symlinks=False)
                entries.append({"name": entry.name, "size": info.st_size, "modified": int(info.st_mtime)})
    return sorted(entries, key=lambda entry: (entry["modified"], entry["name"]))


def _write_temporary(folder: int, data: bytes) -> str:
    """data in a new file of a random name in folder (O_EXCL, no link followed): its name."""
    temporary = f".upload-{secrets.token_hex(8)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE, dir_fd=folder)
    try:
        with open(fd, "wb", closefd=False) as out:
            out.write(data)
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    return temporary


def store_thumb(folder: int, name: str, data: bytes) -> None:
    """A file's small copy under its name: rename replaces whatever is at name, a link too, never
    following it."""
    os.replace(_write_temporary(folder, data), name, src_dir_fd=folder, dst_dir_fd=folder)


def store(folder: int, name: str, data: bytes) -> str:
    """data as a new file under name, or name-2, name-3 ... (an existing file is never replaced); the
    name it got. Written under a temporary name first, so the module never reads half a file."""
    temporary = _write_temporary(folder, data)
    stem, extension = name.rsplit(".", 1)
    try:
        for number in range(1, MAX_FILES_CAP + 2):
            candidate = name if number == 1 else f"{stem}-{number}.{extension}"
            try:
                # link fails when the name exists (a file or a link the module put there): nothing is replaced
                os.link(temporary, candidate, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
                return candidate
            except FileExistsError:
                continue
        raise HTTPException(status_code=409, detail="Too many files of that name")
    finally:
        os.unlink(temporary, dir_fd=folder)


async def screen_side() -> int:
    """The longest side of the screen now (a portrait screen's height), within MAX_SIDE; DEFAULT_SIDE off a
    mirror or when wlr-randr doesn't answer."""
    exit_code, output, _ = await run_command("wlr-randr")
    size = current_size(output) if exit_code == 0 else None
    return min(max(size), MAX_SIDE) if size else DEFAULT_SIDE


@router.get("/{instance_id}/files/{key}")
async def get_files(instance_id: str, key: str):
    """What the settings' files field shows: the files, how many may be added, the room left, and the
    longest side to shrink an image to."""
    prop, _ = files_setting(instance_id, key)
    try:
        folder = open_folder(instance_id, key, create=False)
    except FileNotFoundError:
        files = []
    else:
        try:
            files = list_files(folder)
        finally:
            os.close(folder)
    return {"files": files, "max_files": max_files(prop), "max_side": await screen_side(),
            "free_bytes": shutil.disk_usage(get_module_dir() if get_module_dir().exists() else "/").free}


@router.post("/{instance_id}/files/{key}")
async def upload_file(instance_id: str, key: str, file: UploadFile, thumb: UploadFile | None = None):
    """One file per call (the admin page sends them one at a time), read only up to its kind's size cap;
    with an image, the admin page's small copy of it for the field's thumbnails."""
    prop, kind = files_setting(instance_id, key)
    data = await file.read(kind["max_bytes"] + 1)
    if len(data) > kind["max_bytes"]:
        raise HTTPException(status_code=413, detail=f"Larger than {kind['max_bytes'] // (1024 * 1024)} MB")
    if not kind["check"](data):
        raise HTTPException(status_code=415, detail="Not a file of the kind this setting takes")
    thumb_data = await thumb.read(THUMB_MAX_BYTES + 1) if thumb is not None and kind.get("thumbs") else None
    if thumb_data is not None and not is_thumb(thumb_data):
        raise HTTPException(status_code=415, detail=f"The small copy must be a JPEG of at most {THUMB_MAX_SIDE} px")
    try:
        folder = open_folder(instance_id, key, create=True)
    except OSError as e:
        logger.error(f"{instance_id}: no folder for its files ({e})")
        raise HTTPException(status_code=500, detail="The mirror couldn't make a folder for the files")
    try:
        if len(list_files(folder)) >= max_files(prop):
            raise HTTPException(status_code=409, detail=f"At most {max_files(prop)} files")
        name = store(folder, safe_name(file.filename or "", kind["extension"]), data)
    except OSError as e:
        logger.error(f"{instance_id}: a file wasn't saved ({e})")
        raise HTTPException(status_code=507, detail="The mirror couldn't save the file: is it full?")
    finally:
        os.close(folder)
    if thumb_data is not None:
        try:
            thumbs = open_folder(instance_id, key, create=True, thumbs=True)
            try:
                store_thumb(thumbs, name, thumb_data)
            finally:
                os.close(thumbs)
        except OSError as e:  # the field shows the photo itself instead
            logger.warning(f"{instance_id}: no small copy of {key}/{name} ({e})")
    logger.info(f"{instance_id}: {key}/{name} added")
    return {"name": name}


def _open_file(instance_id: str, key: str, name: str, thumbs: bool = False) -> tuple[int, int]:
    """(folder, file) descriptors of one stored file (or its small copy); 404 for any other name or a link."""
    if not NAME.match(name):
        raise HTTPException(status_code=404, detail="No such file")
    try:
        folder = open_folder(instance_id, key, create=False, thumbs=thumbs)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No such file")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError:
        os.close(folder)
        raise HTTPException(status_code=404, detail="No such file")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        os.close(folder)
        raise HTTPException(status_code=404, detail="No such file")
    return folder, fd


@router.get("/{instance_id}/files/{key}/{name}")
async def get_file(instance_id: str, key: str, name: str, thumb: bool = False):
    """One file, never more than its kind's size cap; thumb: its small copy, for the field's thumbnails
    (the file itself when it has none: one uploaded without, or a copy that wasn't saved)."""
    _, kind = files_setting(instance_id, key)
    try:
        folder, fd = _open_file(instance_id, key, name, thumbs=True) if thumb and kind.get("thumbs") else (None, None)
    except HTTPException:
        folder, fd = None, None
    if fd is None:
        folder, fd = _open_file(instance_id, key, name)
    try:
        with open(fd, "rb", closefd=False) as f:
            data = f.read(kind["max_bytes"])
    finally:
        os.close(fd)
        os.close(folder)
    return Response(content=data, media_type="image/jpeg" if kind["extension"] == ".jpg" else "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.delete("/{instance_id}/files/{key}/{name}")
async def delete_file(instance_id: str, key: str, name: str):
    files_setting(instance_id, key)
    folder, fd = _open_file(instance_id, key, name)
    os.close(fd)
    try:
        os.unlink(name, dir_fd=folder)  # a link swapped in meanwhile is removed itself, not what it points at
    except FileNotFoundError:
        pass
    finally:
        os.close(folder)
    with contextlib.suppress(OSError):  # its small copy, if it has one
        thumbs = open_folder(instance_id, key, create=False, thumbs=True)
        try:
            os.unlink(name, dir_fd=thumbs)
        finally:
            os.close(thumbs)
    logger.info(f"{instance_id}: {key}/{name} removed")
    return {"removed": name}
