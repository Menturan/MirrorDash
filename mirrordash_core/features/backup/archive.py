# SPDX-License-Identifier: AGPL-3.0-only
"""The backup file itself: a .mirror file is a zip, encrypted with openssl when it has a password.

is_encrypted tells which kind a file is; plain_zip gives the zip either way (decrypted into a temporary
file); read_manifest and backup_wifi read what is in it. _openssl runs `openssl enc` with the password in
its environment, never on its command line. content_hash tells whether two backups hold the same.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import zipfile

logger = logging.getLogger("mirrordash.core.backup")


# The zip's own encryption, which only MirrorDash versions before openssl used (zipfile can't read it)
ZIP_ENTRY_IS_ENCRYPTED = 0x1

# openssl enc's header: the backup is encrypted. ponytail: AES-256-CBC with PBKDF2, no MAC; a changed
# file fails to unpack as a zip rather than being detected. Upgrade path: AES-GCM with `cryptography`
# (`openssl enc` refuses AEAD ciphers, also in 3.x).
ENCRYPTED_MAGIC = b"Salted__"

def is_encrypted(path: str) -> bool:
    """Whether a backup file is encrypted (made with a password)."""
    with open(path, "rb") as f:
        return f.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC

async def _openssl(password: str, *args: str) -> bool:
    """`openssl enc` with the backup's cipher; the password goes in the environment, never on the
    command line (where `ps` shows it, and where a space split it with the old zip tool)."""
    proc = await asyncio.create_subprocess_exec(
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", *args, "-pass", "env:MIRRORDASH_BACKUP_PASS",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MIRRORDASH_BACKUP_PASS": password})
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.info(f"openssl enc failed: {err.decode(errors='replace').strip()}")
    return proc.returncode == 0

@contextlib.asynccontextmanager
async def plain_zip(path: str, password: str | None):
    """The backup as a zip: the file itself, or decrypted into a temporary file. None when it's
    encrypted and the password is missing or wrong."""
    if not is_encrypted(path):
        yield path
        return
    if not password:
        yield None
        return
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), suffix=".zip") as tmp:
        # A wrong password fails at the padding, or (about 1 in 256) gives bytes that aren't a zip
        ok = await _openssl(password, "-d", "-in", path, "-out", tmp.name)
        yield tmp.name if ok and zipfile.is_zipfile(tmp.name) else None

async def read_manifest(path: str, password: str | None = None) -> dict | None:
    """A backup's manifest, or None when it's encrypted and the password is missing or wrong.
    Raises zipfile.BadZipFile, KeyError or ValueError for a file that isn't a backup."""
    async with plain_zip(path, password) as zip_path:
        if zip_path is None:
            return None
        with zipfile.ZipFile(zip_path) as zf:
            if zf.getinfo("backup_manifest.json").flag_bits & ZIP_ENTRY_IS_ENCRYPTED:
                raise ValueError("This backup was made by an older MirrorDash version and can't be read.")
            return json.loads(zf.read("backup_manifest.json").decode("utf-8"))

async def backup_wifi(path: str, password: str | None) -> list[dict]:
    """The Wi-Fi networks an encrypted backup carries ([] for a plain one or a wrong password)."""
    async with plain_zip(path, password) as zip_path:
        if zip_path is None:
            return []
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read("wifi.json")) if "wifi.json" in zf.namelist() else []

def content_hash(folder) -> str:
    """A hash of every file's path and bytes under a backup's content folder: the same settings and data
    give the same hash, whenever the zip was made (the automatic backup skips a week without changes)."""
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            digest.update(os.path.relpath(path, folder).encode() + b"\0")
            with open(path, "rb") as f:
                digest.update(hashlib.file_digest(f, "sha256").digest())
    return digest.hexdigest()
