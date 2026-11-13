# SPDX-License-Identifier: AGPL-3.0-only
"""The mirror's video player (ARCHITECTURE #34): the core runs it, as its own user, for a module that asked.

    /usr/bin/python3 -I play.py --uri=URI --output=ALSA_DEVICE --volume=0..1 [--fullscreen]

It runs with the system's Python, which has GStreamer's bindings (python3-gi, in the OS image); the app's
own Python doesn't, so this file imports nothing from the app, and -I keeps it from reading anything but
the standard library. command.py has already checked every argument. It plays in a window with the app id
mirrordash-video (labwc's rule places it) or fullscreen, and tells the core on stdout, one line each:

    starting          at once
    playing           the first picture reached the screen
    tick SECONDS      every TICK seconds, with the stream's position: the core's watchdog (player.py)
    error TEXT        what went wrong; the exit code is then 1 (0 at the end of the stream or on SIGTERM)
"""
import argparse
import os
import re
import signal
import sys
import tempfile
import urllib.parse
import urllib.request

VIDEO, AUDIO, SOFT_VOLUME = 0x1, 0x2, 0x10  # GstPlayFlags: no subtitles, which nothing shows. Without
# SOFT_VOLUME playsink finds no volume control and ignores `volume` (measured: it played at full volume)
TICK = 2  # seconds between tick lines; player.py's watchdog counts on it
MAX_HEIGHT = 720  # the Pi 3's decoder and compositor: more than 720p drops frames (MEASUREMENTS.md)
FETCH_TIMEOUT = 5  # under the core's silence limit, which counts from "starting"
RESOLUTION = re.compile(r"RESOLUTION=\d+x(\d+)")


def filtered(playlist: str, base: str) -> str:
    """An HLS master playlist for this Pi: without its subtitle renditions (with them, as in SVT's, hlsdemux2
    waits some 18 s for the subtitle track before the first frame; without, a live stream starts in under
    2 s), without its I-frame lists and variants above MAX_HEIGHT, and with every address made absolute.
    If every variant is above MAX_HEIGHT they all stay: a picture that drops frames beats none.
    ponytail: only the master playlist is filtered; a plain file or a media playlist plays as it is."""
    lines = playlist.splitlines()
    heights = [int(m.group(1)) for line in lines if line.startswith("#EXT-X-STREAM-INF") and (m := RESOLUTION.search(line))]
    capped = any(height <= MAX_HEIGHT for height in heights)
    out, skip_uri = [], False
    for line in lines:
        if skip_uri:  # the address under a variant that was left out
            skip_uri = False
            continue
        if line.startswith(("#EXT-X-MEDIA:TYPE=SUBTITLES", "#EXT-X-I-FRAME-STREAM-INF")):
            continue
        if capped and line.startswith("#EXT-X-STREAM-INF") and (m := RESOLUTION.search(line)) and int(m.group(1)) > MAX_HEIGHT:
            skip_uri = True
            continue
        line = re.sub(r',SUBTITLES="[^"]*"', "", line)
        line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{urllib.parse.urljoin(base, m.group(1))}"', line)
        out.append(urllib.parse.urljoin(base, line) if line and not line.startswith("#") else line)
    return "\n".join(out) + "\n"


def playable(uri: str) -> str:
    """The address to play: an HLS master playlist (an address ending in .m3u8) goes through filtered()
    into a local file, which only this process (the core's user) can make: the module has no way to hand
    GStreamer a file, and command.py allows no file: address."""
    if not urllib.parse.urlsplit(uri).path.endswith(".m3u8"):
        return uri
    try:
        with urllib.request.urlopen(uri, timeout=FETCH_TIMEOUT) as response:
            playlist = response.read(1 << 20).decode("utf-8", "replace")
            base = response.geturl()
    except OSError:
        return uri  # GStreamer tries, and reports the error
    if "#EXT-X-STREAM-INF" not in playlist:
        return uri
    fd, path = tempfile.mkstemp(suffix=".m3u8", prefix="mirrordash-video-")
    with os.fdopen(fd, "w") as f:
        f.write(filtered(playlist, base))
    return "file://" + path


def main(uri: str, device: str, volume: float, fullscreen: bool) -> int:
    print("starting", flush=True)
    import gi  # here, so the tests can import the rest without GStreamer
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst

    GLib.set_prgname("mirrordash-video")  # the window's app id, which the labwc rule matches
    Gst.init(None)
    player = Gst.ElementFactory.make("playbin3")
    video = Gst.ElementFactory.make("waylandsink")
    video.set_property("fullscreen", fullscreen)
    audio = Gst.ElementFactory.make("alsasink")
    audio.set_property("device", device)
    # Always an audio branch, also at volume 0: a live stream without one drops every frame (measured)
    player.set_property("video-sink", video)
    player.set_property("audio-sink", audio)
    player.set_property("flags", VIDEO | AUDIO | SOFT_VOLUME)
    player.set_property("volume", volume)
    playing = playable(uri)
    player.set_property("uri", playing)

    loop, result, shown = GLib.MainLoop(), [0], []

    def on_picture(_pad, _info):
        if not shown:
            shown.append(True)
            print("playing", flush=True)
        return Gst.PadProbeReturn.REMOVE

    def on_message(_bus, message):
        if message.type == Gst.MessageType.EOS:
            loop.quit()
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            print("error " + " ".join(error.message.split()), flush=True)
            result[0] = 1
            loop.quit()

    def tick():
        found, position = player.query_position(Gst.Format.TIME)
        print(f"tick {position / Gst.SECOND:.1f}" if found else "tick 0", flush=True)
        return True

    video.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, on_picture)
    bus = player.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: loop.quit() or False)
    GLib.timeout_add_seconds(TICK, tick)
    player.set_state(Gst.State.PLAYING)
    loop.run()
    player.set_state(Gst.State.NULL)
    if playing != uri:
        os.unlink(playing.removeprefix("file://"))
    return result[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--volume", type=float, required=True)
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()
    sys.exit(main(args.uri, args.output, args.volume, args.fullscreen))
