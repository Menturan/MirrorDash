"""scripts/release.py's download(): parallel parts when the server allows Range, one stream otherwise,
the same bytes either way, and a progress line."""
import importlib.util
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parent.parent / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

DATA = os.urandom(3 * 1024 * 1024 + 123)  # not a multiple of the part size


def serve(supports_range):
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            if supports_range and match:
                start, end = int(match[1]), min(int(match[2]), len(DATA) - 1)
                ranges.append((start, end))
                body = DATA[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
            else:
                body = DATA
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, ranges


@pytest.mark.parametrize("supports_range", [True, False])
def test_download_gets_every_byte_with_progress(tmp_path, capsys, supports_range):
    server, ranges = serve(supports_range)
    try:
        release.download(f"http://127.0.0.1:{server.server_port}/image.zip", tmp_path / "image.zip", chunk=64 * 1024)
    finally:
        server.shutdown()
    assert (tmp_path / "image.zip").read_bytes() == DATA
    if supports_range:
        assert len(ranges) == 1 + 16  # the probe, then sixteen parts at once
    output = capsys.readouterr().out
    assert re.search(r"\d+ MB in \d+:\d\d \([\d.]+ MB/s\)", output)


def test_fetch_uses_aria2c_when_installed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(release.shutil, "which", lambda name: "/usr/bin/aria2c" if name == "aria2c" else None)
    monkeypatch.setattr(release.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0}))
    monkeypatch.setattr(release, "download", lambda *a, **kw: pytest.fail("the built-in download shouldn't run"))
    release.fetch("https://storage.example/image.zip", tmp_path / "image.zip")
    cmd = calls[0]
    assert cmd[0] == "aria2c" and "-x16" in cmd and "-c" in cmd  # 16 connections, resumes on the next try
    assert cmd[cmd.index("-d") + 1] == str(tmp_path) and cmd[cmd.index("-o") + 1] == "image.zip"
    assert cmd[-1] == "https://storage.example/image.zip"


def test_fetch_downloads_by_itself_without_aria2c(tmp_path, monkeypatch):
    used = []
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    monkeypatch.setattr(release, "download", lambda url, path: used.append((url, path)))
    release.fetch("https://storage.example/image.zip", tmp_path / "image.zip")
    assert used == [("https://storage.example/image.zip", tmp_path / "image.zip")]
