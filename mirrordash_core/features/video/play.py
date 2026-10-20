# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The mirror's video player (ARCHITECTURE #34): plays one stream or file with the Pi's own decoder,
in a window that labwc places by its app id (mirrordash-video), or fullscreen.

    /usr/bin/python3 -I play.py URI [--fullscreen]

It runs with the system's Python, which has GStreamer's bindings (python3-gi, in the OS image); the app's
own Python doesn't, so this file imports nothing from the app. It exits 0 at the end of the stream or
on SIGTERM, and 1 on an error, which it prints.
"""
import os
import re
import signal
import sys
import tempfile
import urllib.parse
import urllib.request

VIDEO, AUDIO = 0x1, 0x2  # GstPlayFlags: no subtitles, which nothing shows


def without_subtitles(playlist: str, base: str) -> str:
    """An HLS master playlist without its subtitle renditions, with every address made absolute.
    With subtitles in it (SVT), hlsdemux2 waits some 18 s for the subtitle track before the first frame,
    whatever streams are selected; without, a live stream starts in under 2 s (measured on the Pi 3)."""
    lines = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES"):
            continue
        line = re.sub(r',SUBTITLES="[^"]*"', "", line)
        line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{urllib.parse.urljoin(base, m.group(1))}"', line)
        lines.append(urllib.parse.urljoin(base, line) if line and not line.startswith("#") else line)
    return "\n".join(lines) + "\n"


def playable(uri: str) -> str:
    """The URI to play: an HLS master playlist goes through without_subtitles into a local file."""
    if not uri.startswith(("http://", "https://")):
        return uri
    try:
        with urllib.request.urlopen(uri, timeout=10) as response:
            if "mpegurl" not in response.headers.get("Content-Type", "").lower() and ".m3u8" not in uri:
                return uri  # a file or another stream: GStreamer fetches it itself
            playlist = response.read(1 << 20).decode("utf-8", "replace")
            base = response.geturl()
    except OSError:
        return uri  # GStreamer tries, and reports the error
    if "#EXT-X-STREAM-INF" not in playlist:
        return uri
    fd, path = tempfile.mkstemp(suffix=".m3u8", prefix="mirrordash-video-")
    with os.fdopen(fd, "w") as f:
        f.write(without_subtitles(playlist, base))
    return "file://" + path


def main(uri: str, fullscreen: bool) -> int:
    import gi  # here, so the tests can import the rest without GStreamer
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst

    GLib.set_prgname("mirrordash-video")  # the window's app id, which the labwc rule matches
    Gst.init(None)
    player = Gst.ElementFactory.make("playbin3")
    sink = Gst.ElementFactory.make("waylandsink")
    sink.set_property("fullscreen", fullscreen)
    player.set_property("video-sink", sink)
    player.set_property("flags", VIDEO | AUDIO)
    playing = playable(uri)
    player.set_property("uri", playing)

    loop, result = GLib.MainLoop(), [0]

    def on_message(_bus, message):
        if message.type == Gst.MessageType.EOS:
            loop.quit()
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"{error.message} ({debug})", file=sys.stderr)
            result[0] = 1
            loop.quit()

    bus = player.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or False)
    player.set_state(Gst.State.PLAYING)
    loop.run()
    player.set_state(Gst.State.NULL)
    if playing != uri:
        os.unlink(playing.removeprefix("file://"))
    return result[0]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--fullscreen" in sys.argv[2:]))
