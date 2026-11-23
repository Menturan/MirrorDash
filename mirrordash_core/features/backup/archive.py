# SPDX-License-Identifier: AGPL-3.0-only
"""The backup file itself: a .mirror file is a zip, encrypted and authenticated when it has a password.

is_encrypted tells which kind a file is; encrypt_zip makes the encrypted one; plain_zip gives the zip either
way (decrypted into a temporary file, only after its MAC is checked); read_manifest and backup_wifi read what
is in it. openssl runs with the key in its environment, never on its command line. content_hash tells whether
two backups hold the same.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import tempfile
import zipfile

logger = logging.getLogger("mirrordash.core.backup")


# The zip's own encryption, which only MirrorDash versions before openssl used (zipfile can't read it)
ZIP_ENTRY_IS_ENCRYPTED = 0x1

OLDER = "This backup was made by an older MirrorDash and can't be read; make a new backup."
DAMAGED = "This backup is damaged or has been changed, so it can't be restored."

class BackupError(ValueError):
    """A backup that can't be read, whatever the password; the message is for the owner."""

# An encrypted backup: MAGIC (the format's version in it) | salt (16) | check (16) | ciphertext | tag (32).
# check and tag are HMAC-SHA256 keyed by the password's PBKDF2 (600000 rounds, SHA-256): check over MAGIC and
# salt tells a wrong password from a changed file, tag over everything before it (so the header is authenticated
# too) is verified before one byte is decrypted. The ciphertext is `openssl enc` AES-256-CBC, streamed: the whole
# backup is never in memory. ponytail: encrypt-then-MAC with the stdlib's hmac around openssl, not AES-GCM:
# `cryptography` isn't a dependency (a binary wheel on the Pi) and GCM in one shot needs the backup in memory.
# A flipped byte in the salt reads as a wrong password. The format changes: bump MAGIC and OLD_MAGICs grow.
MAGIC = b"MDBAKv01"
OLD_MAGIC = b"Salted__"  # openssl enc's own header: AES-CBC without a MAC, MirrorDash before 1.0
SALT_LEN, CHECK_LEN, TAG_LEN, HEADER_LEN = 16, 16, 32, len(MAGIC) + 16 + 16
PBKDF2_ROUNDS = 600_000
CHUNK = 1024 * 1024

def is_encrypted(path: str) -> bool:
    """Whether a backup file is encrypted (made with a password), in the current format or an older one."""
    with open(path, "rb") as f:
        return f.read(len(MAGIC)) in (MAGIC, OLD_MAGIC)

def _keys(password: str, salt: bytes) -> tuple[bytes, str]:
    """The MAC key and openssl's password, both from one PBKDF2 (slow: run it off the event loop)."""
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS, 64)
    return derived[:32], derived[32:].hex()

async def _openssl(pass_hex: str, *args: str, stdin=None, stdout=None) -> asyncio.subprocess.Process:
    """`openssl enc` with the backup's cipher; its password (already stretched, so one round and no salt:
    it is new for every file) goes in the environment, never on the command line."""
    return await asyncio.create_subprocess_exec(
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "1", "-nosalt", *args, "-pass", "env:MIRRORDASH_BACKUP_PASS",
        stdin=stdin, stdout=stdout, stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MIRRORDASH_BACKUP_PASS": pass_hex})

async def encrypt_zip(password: str, zip_path: str, out_path: str) -> None:
    """Write zip_path as an encrypted backup to out_path. Raises RuntimeError when openssl fails."""
    salt = os.urandom(SALT_LEN)
    mac_key, pass_hex = await asyncio.to_thread(_keys, password, salt)
    header = MAGIC + salt
    check = hmac.digest(mac_key, header, "sha256")[:CHECK_LEN]
    mac = hmac.new(mac_key, header + check, "sha256")
    proc = await _openssl(pass_hex, "-e", "-in", zip_path, stdout=asyncio.subprocess.PIPE)
    with open(out_path, "wb") as out:
        out.write(header + check)
        while chunk := await proc.stdout.read(CHUNK):
            mac.update(chunk)
            out.write(chunk)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"openssl enc failed: {err.decode(errors='replace').strip()}")
        out.write(mac.digest())

def _verify(path: str, password: str) -> tuple[bool, str]:
    """(whether the password is right, openssl's password); raises BackupError for a damaged file."""
    size = os.path.getsize(path)
    if size < HEADER_LEN + TAG_LEN:
        raise BackupError(DAMAGED)
    with open(path, "rb") as f:
        header = f.read(HEADER_LEN)
        mac_key, pass_hex = _keys(password, header[len(MAGIC):len(MAGIC) + SALT_LEN])
        if not hmac.compare_digest(hmac.digest(mac_key, header[:-CHECK_LEN], "sha256")[:CHECK_LEN], header[-CHECK_LEN:]):
            return False, ""
        mac = hmac.new(mac_key, header, "sha256")
        left = size - HEADER_LEN - TAG_LEN
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            mac.update(chunk)
            left -= len(chunk)
        if not hmac.compare_digest(mac.digest(), f.read()):
            raise BackupError(DAMAGED)
    return True, pass_hex

async def _decrypt(path: str, pass_hex: str, out_path: str) -> None:
    """Decrypt the ciphertext of a verified backup into out_path (openssl reads it from a pipe)."""
    with open(out_path, "wb") as out, open(path, "rb") as f:
        proc = await _openssl(pass_hex, "-d", stdin=asyncio.subprocess.PIPE, stdout=out)
        f.seek(HEADER_LEN)
        left = os.path.getsize(path) - HEADER_LEN - TAG_LEN
        try:
            while left > 0:
                proc.stdin.write(f.read(min(CHUNK, left)))
                left -= min(CHUNK, left)
                await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # openssl gave up: its exit code says so below
        _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.info(f"openssl enc failed: {err.decode(errors='replace').strip()}")
        raise BackupError(DAMAGED)

@contextlib.asynccontextmanager
async def plain_zip(path: str, password: str | None):
    """The backup as a zip: the file itself, or decrypted into a temporary file. None when it's
    encrypted and the password is missing or wrong. Raises BackupError for one that is damaged, changed
    or made by an older MirrorDash (before anything is unpacked), zipfile.BadZipFile for a file that isn't a backup."""
    with open(path, "rb") as f:
        start = f.read(len(MAGIC))
    if start == OLD_MAGIC:
        raise BackupError(OLDER)
    if start != MAGIC:
        with zipfile.ZipFile(path) as zf:
            if any(info.flag_bits & ZIP_ENTRY_IS_ENCRYPTED for info in zf.infolist()):
                raise BackupError(OLDER)
        yield path
        return
    if not password:
        yield None
        return
    right, pass_hex = await asyncio.to_thread(_verify, path, password)
    if not right:
        yield None
        return
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), suffix=".zip") as tmp:
        await _decrypt(path, pass_hex, tmp.name)
        if not zipfile.is_zipfile(tmp.name):
            raise BackupError(DAMAGED)  # the right password and an unchanged file, yet no zip
        yield tmp.name

async def read_manifest(path: str, password: str | None = None) -> dict | None:
    """A backup's manifest, or None when it's encrypted and the password is missing or wrong.
    Raises zipfile.BadZipFile, KeyError or ValueError for a file that isn't a backup."""
    async with plain_zip(path, password) as zip_path:
        if zip_path is None:
            return None
        with zipfile.ZipFile(zip_path) as zf:
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
