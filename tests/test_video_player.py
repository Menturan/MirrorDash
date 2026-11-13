"""The video player the core runs for a module (features/video, ARCHITECTURE #34): what a module may ask,
the watchdog, the supervision and the fullscreen hold. A fake player stands in for play.py, which needs
a Pi's decoder: how it plays and how the HLS playlist is filtered on the real thing is checked on a mirror."""
import asyncio
import importlib.util
import sys
import textwrap
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mirrordash_core import screen
from mirrordash_core.features.video import command, playback, player
from mirrordash_core.features.video.player import Player, verdict
from mirrordash_core.screen import ConnectionManager

FAKE = textwrap.dedent("""
    import os, signal, sys, time
    mode = sys.argv[1]
    print("starting", flush=True)
    if mode == "ok":                      # a clip: the picture, a tick, the end
        print("playing", flush=True); print("tick 1.0", flush=True); sys.exit(0)
    if mode == "error":
        print("error no such stream", flush=True); sys.exit(1)
    if mode == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if mode == "frozen":                  # alive and ticking, the picture stands still
        print("playing", flush=True)
        while True:
            print("tick 5.0", flush=True); time.sleep(0.1)
    if mode == "live":                    # plays until told to stop
        print("playing", flush=True)
        t = 0
        while True:
            t += 1; print(f"tick {t}.0", flush=True); time.sleep(0.1)
    if mode == "hung":                    # SIGSTOP: a stopped player never sees SIGTERM
        print("playing", flush=True); os.kill(os.getpid(), signal.SIGSTOP)
    if mode == "nopicture":
        while True:
            print("tick 0", flush=True); time.sleep(0.1)
""")
FAST = (1.0, 1.0, 0.5)  # silence, start, stall limits for the fake players


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_player.py"
    script.write_text(FAKE)
    monkeypatch.setattr(player, "POLL", 0.1)
    monkeypatch.setattr(player, "KILL_AFTER", 0.3)
    return lambda mode, limits=FAST: Player([sys.executable, str(script), mode], limits)


# What a module may ask for

def test_only_http_addresses_are_played():
    assert command.check_url("https://svt.se/video/ch-svt1") == "https://svt.se/video/ch-svt1"
    for bad in ("file:///etc/passwd", "--help", "rtsp://x/y", "ftp://x/y", "http://", "https://a b", "gst://x", "", None, 5,
                "https://x/" + "a" * 3000, "https://x/\n--uri=file:///etc/passwd"):
        with pytest.raises(ValueError):
            command.check_url(bad)


def test_the_output_is_a_name_never_a_device():
    assert command.check_output("jack") == "jack" and command.check_output("hdmi") == "hdmi"
    for bad in ("plughw:CARD=Headphones,DEV=0", "fakesink", "", None, ["jack"]):
        with pytest.raises(ValueError):
            command.check_output(bad)


def test_the_volume_is_a_number_from_0_to_1():
    assert command.check_volume(0.35) == 0.35 and command.check_volume(0) == 0.0  # 0 is allowed: the audio chain stays
    assert command.check_volume(7) == 1.0 and command.check_volume(-1) == 0.0
    for bad in ("loud", None, True, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            command.check_volume(bad)


@pytest.mark.asyncio
async def test_a_module_cannot_play_from_this_mirror_or_the_home_network():
    await command.check_host_is_public("http://93.184.216.34/stream.m3u8")
    for private in ("http://127.0.0.1:8000/admin", "http://192.168.1.20/cam", "http://10.0.0.1/", "http://[::1]/",
                    "http://[::ffff:192.168.1.1]/", "http://169.254.169.254/"):
        with pytest.raises(ValueError):
            await command.check_host_is_public(private)


def test_the_command_is_one_argument_per_value_and_dies_with_the_core():
    cmd = command.build_command("https://x/y?a=b&c=d", "hdmi", 0.5, True)
    assert cmd[:4] == ["setpriv", "--pdeathsig", "KILL", "--"] and cmd[4:6] == ["/usr/bin/python3", "-I"]
    assert cmd[7:] == ["--uri=https://x/y?a=b&c=d", "--output=hdmi:CARD=vc4hdmi,DEV=0", "--volume=0.5", "--fullscreen"]


@pytest.mark.asyncio
async def test_nothing_unchecked_reaches_the_command():
    with patch.object(command, "check_host_is_public", new=AsyncMock()):
        assert (await playback.checked_command({"url": "https://x/y"}))[-2:] == ["--output=plughw:CARD=Headphones,DEV=0", "--volume=1.0"]
        for bad in ({"url": "file:///etc/shadow"}, {"url": "https://x/y", "output": "evil"}, {"url": "https://x/y", "volume": "x"}, {}):
            with pytest.raises(ValueError):
                await playback.checked_command(bad)


# The playlist

def play_py():
    spec = importlib.util.spec_from_file_location("play", command.PLAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # needs no GStreamer: main() imports it
    return module


def test_the_playlist_loses_subtitles_iframes_and_variants_over_720p():
    master = textwrap.dedent("""\
        #EXTM3U
        #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="sv",URI="audio/sv.m3u8"
        #EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="Svenska",URI="sub/sv.m3u8"
        #EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360,AUDIO="a",SUBTITLES="s"
        v360/index.m3u8
        #EXT-X-STREAM-INF:BANDWIDTH=3000,RESOLUTION=1280x720,AUDIO="a",SUBTITLES="s"
        v720/index.m3u8
        #EXT-X-STREAM-INF:BANDWIDTH=6000,RESOLUTION=1920x1080,AUDIO="a",SUBTITLES="s"
        v1080/index.m3u8
        #EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=300,URI="iframe/index.m3u8"
        """)
    out = play_py().filtered(master, "https://cdn.example/live/master.m3u8")
    assert "SUBTITLES" not in out and "iframe" not in out and "1080" not in out
    assert 'URI="https://cdn.example/live/audio/sv.m3u8"' in out
    assert "\nhttps://cdn.example/live/v360/index.m3u8\n" in out and "\nhttps://cdn.example/live/v720/index.m3u8\n" in out
    only_big = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000,RESOLUTION=1920x1080\nbig.m3u8\n"
    assert "https://x/big.m3u8" in play_py().filtered(only_big, "https://x/m.m3u8")  # nothing else to play: it stays


# The watchdog

def test_the_watchdog_kills_what_hangs_stalls_or_never_shows():
    t = 100.0
    assert verdict(t + 5, t, t + 4, t + 1, t + 4) is None                      # ticking and moving
    assert "stopped answering" in verdict(t + 20, t, t + 5, t + 1, t + 5)      # no line for 10 s (SIGSTOP)
    assert "stalled" in verdict(t + 40, t, t + 39, t + 1, t + 20)              # ticks, position stands still
    assert "no picture" in verdict(t + 31, t, t + 30, None, t)                 # never showed
    assert verdict(t + 20, t, t + 19, None, t) is None                         # still starting, within its 30 s
    assert verdict(t + 9, t, t, None, t) is None                               # quiet, but not for long yet


# The player under supervision

@pytest.mark.asyncio
async def test_a_clip_that_plays_to_its_end(fake):
    seen = []

    async def picture():
        seen.append("picture")
    assert await fake("ok", (5, 5, 5)).run(picture) == (False, "end of stream") and seen == ["picture"]


@pytest.mark.asyncio
async def test_a_player_that_fails_says_why(fake):
    async def never():
        raise AssertionError("no picture was shown")
    assert await fake("error").run(never) == (True, "no such stream")
    failed, reason = await fake("crash").run(never)
    assert failed and "code -11" in reason
    assert await Player(["/no/such/player"]).run(never) == (True, "the player didn't start: No such file or directory")


@pytest.mark.asyncio
async def test_a_stopped_video_ends_without_failing(fake):
    started, p = asyncio.Event(), fake("live", (5, 5, 5))

    async def picture():
        started.set()
    task = asyncio.create_task(p.run(picture))
    await asyncio.wait_for(started.wait(), 5)
    p.stop("its card is hidden")
    assert await asyncio.wait_for(task, 5) == (False, "its card is hidden")
    assert p._process.returncode is not None  # gone, with its window


@pytest.mark.asyncio
async def test_a_player_that_hangs_is_killed_though_it_ignores_sigterm(fake):
    async def picture():
        pass
    begun = time.monotonic()
    failed, reason = await fake("hung", (1.0, 5, 5)).run(picture)  # as on the Pi: silence comes before a stall
    assert failed and "stopped answering" in reason and time.monotonic() - begun < 5
    assert (await fake("frozen").run(picture)) == (True, "the stream stalled")
    assert (await fake("nopicture").run(picture)) == (True, "no picture after 1 s")


# The block the module is in

@pytest.mark.asyncio
async def test_the_module_hears_the_picture_and_the_end_and_leaving_stops_the_player(fake):
    told, window = [], []

    @asynccontextmanager
    async def video_window(instance_id, **options):
        window.append((instance_id, options))
        yield

    async def send(message):
        told.append(message)
    ended = asyncio.Event()

    async def checked(options):
        return [sys.executable, "-c", "pass"]
    with patch.object(playback, "video_window", video_window), patch.object(playback, "checked_command", checked), \
         patch.object(playback, "Player", lambda cmd: fake("live", (5, 5, 5))):
        task = asyncio.create_task(playback.hold("tv", {"fullscreen": True, "hz": 50}, send, ended))
        await asyncio.sleep(0.5)
        assert told[:2] == [{}, {"event": "playing"}] and window == [("tv", {"fullscreen": True, "hz": 50})]
        playback.stop("tv", "its card is hidden")  # the core's own stop: hidden card
        await asyncio.sleep(0.5)
        assert told[-1] == {"event": "ended", "reason": "its card is hidden"} and not task.done()  # the module leaves itself
        ended.set()
        await asyncio.wait_for(task, 5)
        assert playback._players == {}

        told.clear()  # refused before any window is made
        window.clear()
        with patch.object(playback, "checked_command", side_effect=ValueError("url must start with http")):
            await playback.hold("tv", {"url": "file:///x"}, send, asyncio.Event())
        assert told == [{"error": "url must start with http"}] and window == []


# Fullscreen: the page is covered, nothing is sent

@pytest.mark.asyncio
async def test_while_a_video_plays_fullscreen_no_card_is_sent_at_all():
    manager = ConnectionManager()
    ws = type("Ws", (), {"sent": [], "client": None})()

    async def accept():
        pass

    async def send_json(message):
        ws.sent.append(message)
    ws.accept, ws.send_json = accept, send_json
    await manager.connect(ws)
    clock = {"module": "clock", "html": "<p>12:00</p>"}
    with patch.object(screen.time, "time", return_value=59.95):  # the minute is 0.05 s away
        await manager.set_video_playing("tv", True, fullscreen=True)
        await manager.broadcast(clock)
        await asyncio.sleep(0.3)
        assert clock not in ws.sent                       # not on the minute, either
        await manager.set_video_playing("tv", False)
        assert ws.sent[-1] == clock                       # the latest, when it ends

        await manager.broadcast({**clock, "html": "<p>12:01</p>"})  # a card-sized video: once a minute, as before
        await manager.set_video_playing("tv", True)
        await manager.broadcast({**clock, "html": "<p>12:02</p>"})
        await asyncio.sleep(0.3)
        assert ws.sent[-1]["html"] == "<p>12:02</p>"
        await manager.set_video_playing("tv", False)


# The core stops it, too

@pytest.mark.asyncio
async def test_a_hidden_card_and_the_start_of_the_fade_out_stop_the_video():
    from mirrordash_core.features.modules.client import ModuleHost
    hidden_one, resting_one, other = Player(["x"]), Player(["x"]), Player(["x"])
    with patch.dict(playback._players, {"tv": hidden_one, "radio": other}, clear=True):
        await ModuleHost().set_hidden({"tv"})           # its schedule ended, or privacy mode
        assert hidden_one._stopped == ("its card is hidden", False) and other._stopped is None
        playback._players["tv"] = resting_one
        playback.stop_all("the screen is resting")      # display_power._tell_page(True)
        assert resting_one._stopped == ("the screen is resting", False) and other._stopped[0] == "the screen is resting"


@pytest.mark.asyncio
async def test_the_module_side_knows_when_the_picture_shows_or_the_video_failed():
    from mirrordash_core.features.modules.video_playback import VideoPlayback
    shows, fails = VideoPlayback(), VideoPlayback()
    shows.event({"event": "playing"})
    assert await shows.started() is True and not shows.finished.is_set()
    fails.event({"event": "failed", "reason": "no such stream"})
    assert await fails.started() is False and fails.failed and fails.reason == "no such stream"
