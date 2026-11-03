"""Files the owner uploads for a module ("format": "files", features/modules/files.py, ARCHITECTURE #44)."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules import files

AUTH = {"X-API-Key": "secret"}
SCHEMA = {"properties": {"photos": {"format": "files", "accept": "image", "max_files": 3},
                         "sounds": {"format": "files", "accept": "audio"},
                         "title": {"type": "string"}}}
URL = "/admin/modules/photos-1/files/photos"


def jpeg(width: int, height: int, marker: int = 0xC0) -> bytes:
    """A JPEG's markers as a browser's canvas writes them: SOI, APP0, a frame header, a scan, EOI."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = bytes([0xFF, marker]) + b"\x00\x11\x08" + height.to_bytes(2) + width.to_bytes(2) + b"\x03" + b"\x01\x22\x00" * 3
    return b"\xff\xd8" + app0 + frame + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34" * 50 + b"\xff\xd9"


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A saved photos-1 instance of a module with files settings; its module folder in tmp_path."""
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path))
    salt = "0123456789abcdef"
    config = {"admin_auth": {"hash": hash_password("secret", salt), "salt": salt},
              "modules": {"photos-1": {"module": "mirrordash_photos", "position": "background"}}}
    with patch("mirrordash_core.admin.load_config", return_value=config), \
         patch.object(files, "load_config", return_value=config), \
         patch.object(files, "find_entry_point", return_value=SimpleNamespace(name="mirrordash_photos")), \
         patch.object(files, "get_module_schema", return_value=SCHEMA), \
         patch.object(files, "run_command", AsyncMock(return_value=(127, "", ""))):
        yield SimpleNamespace(client=TestClient(app), folder=tmp_path / "cache" / "photos-1" / "files" / "photos")


def upload(client, name="IMG_1.jpg", data=None, url=URL):
    return client.post(url, headers=AUTH, files={"file": (name, data if data is not None else jpeg(1920, 1080), "image/jpeg")})


def test_a_photo_lands_in_the_instances_cache_folder_readable_by_it(mirror):
    assert upload(mirror.client).json() == {"name": "IMG_1.jpg"}
    stored = mirror.folder / "IMG_1.jpg"
    assert stored.read_bytes() == jpeg(1920, 1080)
    # The instance isn't in pi's group: it reads them as "other"; its own folder keeps other instances out
    assert stored.stat().st_mode & 0o777 == 0o664
    assert mirror.folder.stat().st_mode & 0o2775 == 0o2775 and mirror.folder.parent.stat().st_mode & 0o2775 == 0o2775
    listing = mirror.client.get(URL, headers=AUTH).json()
    assert [file["name"] for file in listing["files"]] == ["IMG_1.jpg"]
    assert listing["max_files"] == 3 and listing["max_side"] == files.DEFAULT_SIDE and listing["free_bytes"] > 0
    assert mirror.client.get(f"{URL}/IMG_1.jpg", headers=AUTH).content == jpeg(1920, 1080)
    assert mirror.client.delete(f"{URL}/IMG_1.jpg", headers=AUTH).status_code == 200
    assert not stored.exists()


def test_a_photos_small_copy_comes_and_goes_with_it(mirror):
    """The field's thumbnails: the admin page's 240 px copy, kept apart where the module never looks."""
    sent = mirror.client.post(URL, headers=AUTH, files={"file": ("IMG_1.jpg", jpeg(1920, 1080), "image/jpeg"),
                                                        "thumb": ("thumb.jpg", jpeg(240, 135), "image/jpeg")})
    assert sent.json() == {"name": "IMG_1.jpg"}
    thumbs = mirror.folder.parent / ".thumbs" / "photos"
    assert (thumbs / "IMG_1.jpg").read_bytes() == jpeg(240, 135)
    assert [path.name for path in mirror.folder.iterdir()] == ["IMG_1.jpg"]  # the module's folder holds the photo only
    assert mirror.client.get(f"{URL}/IMG_1.jpg?thumb=1", headers=AUTH).content == jpeg(240, 135)
    upload(mirror.client, "IMG_2.jpg")  # no small copy: the photo itself
    assert mirror.client.get(f"{URL}/IMG_2.jpg?thumb=1", headers=AUTH).content == jpeg(1920, 1080)
    too_big = mirror.client.post(URL, headers=AUTH, files={"file": ("IMG_3.jpg", jpeg(1920, 1080), "image/jpeg"),
                                                           "thumb": ("thumb.jpg", jpeg(1920, 1080), "image/jpeg")})
    assert too_big.status_code == 415 and not (mirror.folder / "IMG_3.jpg").exists()
    mirror.client.delete(f"{URL}/IMG_1.jpg", headers=AUTH)
    assert not (thumbs / "IMG_1.jpg").exists()


def test_the_owners_name_is_kept_made_safe_and_never_replaces_a_file(mirror):
    assert upload(mirror.client, "2026-07-14_IMG 1.HEIC.jpg").json()["name"] == "2026-07-14_IMG_1_HEIC.jpg"
    assert upload(mirror.client, "../../config.jpg").json()["name"] == "config.jpg"
    assert upload(mirror.client, "../../config.jpg").json()["name"] == "config-2.jpg"
    assert files.safe_name("a/b.png", ".jpg") == "a_b.jpg"
    assert files.safe_name("x" * 300 + ".jpg", ".jpg") == "x" * 100 + ".jpg"
    assert files.safe_name(".jpg", ".jpg") == "jpg.jpg" and files.safe_name("", ".jpg") == "file.jpg"
    assert sorted(path.name for path in mirror.folder.iterdir()) == ["2026-07-14_IMG_1_HEIC.jpg", "config-2.jpg", "config.jpg"]


def test_only_what_the_setting_takes_gets_in(mirror, monkeypatch):
    assert mirror.client.post(URL, files={"file": ("a.jpg", jpeg(10, 10), "image/jpeg")}).status_code == 401
    assert upload(mirror.client, data=b"<svg onload=alert(1)>").status_code == 415
    assert upload(mirror.client, data=jpeg(8000, 10)).status_code == 415  # never shrunk by the admin page
    assert upload(mirror.client, url="/admin/modules/photos-1/files/sounds").status_code == 415  # no such kind yet
    assert upload(mirror.client, url="/admin/modules/photos-1/files/title").status_code == 404  # not a files setting
    assert upload(mirror.client, url="/admin/modules/photos-2/files/photos").status_code == 404  # not saved yet
    monkeypatch.setitem(files.FILE_KINDS["image"], "max_bytes", 100)
    assert upload(mirror.client).status_code == 413
    monkeypatch.setitem(files.FILE_KINDS["image"], "max_bytes", 4 * 1024 * 1024)
    for _ in range(3):
        assert upload(mirror.client).status_code == 200
    assert upload(mirror.client).status_code == 409  # max_files
    assert not [path for path in mirror.folder.iterdir() if path.name.startswith(".upload-")]


def test_a_link_the_module_put_in_its_folder_is_never_followed(mirror, tmp_path):
    upload(mirror.client)  # makes the folder
    secret = tmp_path / "core-data.json"
    secret.write_text("the core's secrets")
    (mirror.folder / "evil.jpg").symlink_to(secret)
    assert mirror.client.get(f"{URL}/evil.jpg", headers=AUTH).status_code == 404
    assert [file["name"] for file in mirror.client.get(URL, headers=AUTH).json()["files"]] == ["IMG_1.jpg"]
    assert upload(mirror.client, "evil.jpg").json()["name"] == "evil-2.jpg"
    assert mirror.client.delete(f"{URL}/evil.jpg", headers=AUTH).status_code == 404
    assert secret.read_text() == "the core's secrets"
    # A link instead of the files folder itself: refused, nothing written where it points
    os.rename(mirror.folder.parent, tmp_path / "moved")
    (tmp_path / "cache" / "photos-1" / "files").symlink_to(tmp_path / "moved")
    assert upload(mirror.client, "x.jpg").status_code == 500
    assert not (tmp_path / "moved" / "photos" / "x.jpg").exists()


@pytest.mark.parametrize("output,side", [
    ("HDMI-A-1\n  Modes:\n    3840x2160 px, 60.000000 Hz (preferred, current)\n", 3840),
    ("HDMI-A-1\n  Modes:\n    2560x1440 px, 60.000000 Hz (current)\n", 2560),
    ("HDMI-A-1\n  Modes:\n    7680x4320 px, 30.000000 Hz (current)\n", files.MAX_SIDE),
    ("HDMI-A-1\n  Modes:\n    1920x1080 px, 60.000000 Hz\n", files.DEFAULT_SIDE),  # no current mode
])
def test_an_image_is_shrunk_to_the_screens_longest_side(mirror, output, side):
    with patch.object(files, "run_command", AsyncMock(return_value=(0, output, ""))):
        assert mirror.client.get(URL, headers=AUTH).json()["max_side"] == side


def test_a_jpegs_size_comes_from_its_frame_header():
    assert files.jpeg_size(jpeg(1920, 1080)) == (1920, 1080)
    assert files.jpeg_size(jpeg(1080, 1920, marker=0xC2)) == (1080, 1920)  # progressive
    assert files.jpeg_size(jpeg(10, 10)[:-2]) is None  # cut off
    assert files.jpeg_size(b"\xff\xd8\xff\xda\x00\x02\xff\xd9") is None  # a scan before any frame
    assert files.jpeg_size(b"GIF89a") is None
