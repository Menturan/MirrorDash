"""An encrypted backup is authenticated: a changed, cut or older file is refused before anything is restored."""

import asyncio
import os
import zipfile

import pytest

from mirrordash_core.features.backup import archive
from mirrordash_core.features.backup.archive import (
    DAMAGED,
    OLDER,
    BackupError,
    encrypt_zip,
    plain_zip,
    read_manifest,
)

PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(archive, "PBKDF2_ROUNDS", 1000)  # the real 600000 is for the one round trip below


@pytest.fixture
def locked(tmp_path):
    zip_path = tmp_path / "plain.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("backup_manifest.json", '{"backup_version": "2.0"}')
        zf.writestr("big.bin", os.urandom(300_000), zipfile.ZIP_STORED)  # more than one 64 KiB chunk
    out = tmp_path / "b.mirror"
    asyncio.run(encrypt_zip(PASSWORD, str(zip_path), str(out)))
    return out


def manifest(path, password=PASSWORD):
    return asyncio.run(read_manifest(str(path), password))


def test_round_trip_with_the_real_key_stretching(tmp_path, monkeypatch):
    monkeypatch.undo()
    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("backup_manifest.json", '{"a": 1}')
    out = tmp_path / "b.mirror"
    asyncio.run(encrypt_zip("pässword with spaces", str(zip_path), str(out)))
    assert archive.PBKDF2_ROUNDS == 600_000 and out.read_bytes().startswith(archive.MAGIC)
    assert manifest(out, "pässword with spaces") == {"a": 1}


def test_round_trip_and_a_fresh_salt_every_time(locked, tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "CHUNK", 64 * 1024)
    assert manifest(locked) == {"backup_version": "2.0"}
    again = tmp_path / "again.mirror"
    asyncio.run(encrypt_zip(PASSWORD, str(tmp_path / "plain.zip"), str(again)))
    assert locked.read_bytes()[:40] != again.read_bytes()[:40]  # magic is the same, salt and check are not


def test_a_wrong_or_missing_password_is_not_an_error(locked):
    assert manifest(locked, "wrong") is None and manifest(locked, None) is None


def test_one_flipped_byte_anywhere_is_refused(locked, monkeypatch):
    monkeypatch.setattr(archive, "CHUNK", 64 * 1024)
    raw = locked.read_bytes()
    positions = [0, 7, 8, 23, 24, 39, 40, 41, 1000, len(raw) // 2, len(raw) - 33, len(raw) - 32, len(raw) - 1]
    for pos in positions:
        changed = bytearray(raw)
        changed[pos] ^= 0x01
        locked.write_bytes(changed)
        try:
            result = manifest(locked)
        except (BackupError, zipfile.BadZipFile):
            continue
        # The magic (a plain zip then), the salt and the check read as a wrong password
        assert result is None, f"byte {pos} changed and the backup still opened"
    for pos in (40, 1000, len(raw) // 2, len(raw) - 1):  # past the header the password is right: a clear message
        changed = bytearray(raw)
        changed[pos] ^= 0x01
        locked.write_bytes(changed)
        with pytest.raises(BackupError, match="damaged or has been changed"):
            manifest(locked)


def test_a_cut_backup_is_refused(locked):
    raw = locked.read_bytes()
    for cut in (len(raw) - 1, len(raw) - 32, len(raw) // 2, 41, 20):
        locked.write_bytes(raw[:cut])
        with pytest.raises(BackupError, match=DAMAGED):
            manifest(locked)


def test_an_appended_byte_is_refused(locked):
    locked.write_bytes(locked.read_bytes() + b"x")
    with pytest.raises(BackupError, match=DAMAGED):
        manifest(locked)


def test_a_backup_from_openssl_enc_says_it_is_older(tmp_path):
    old = tmp_path / "old.mirror"
    old.write_bytes(b"Salted__" + bytes(range(100)))
    for password in (PASSWORD, None):  # no password prompt for a backup that can't be read
        with pytest.raises(BackupError, match=OLDER):
            manifest(old, password)

    async def restore():
        async with plain_zip(str(old), PASSWORD):
            pass
    with pytest.raises(BackupError, match="older MirrorDash and can't be read; make a new backup"):
        asyncio.run(restore())
