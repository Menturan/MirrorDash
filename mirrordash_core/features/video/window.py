# Licensed under the PolyForm Noncommercial License 1.0.0.
"""A module's video window (ARCHITECTURE #34): `await self.play_video(uri)` plays a stream or file over
the element with `data-video-window` on the module's card, or fullscreen, and returns when it ends.

The player is its own process (play.py, with the system's GStreamer); its window is placed by a labwc
window rule that this writes into rc.xml while it plays, at the element's place on the page. While it
plays, the other modules update once a minute (video_is_playing), and with hz= the screen runs at that
refresh rate (50 for European TV, which otherwise judders at 60 Hz).
"""
import asyncio
import logging
import re
from pathlib import Path

from mirrordash_core.features.kiosk.ws import manager

logger = logging.getLogger("mirrordash.core.video")

PLAY = Path(__file__).with_name("play.py")
SYSTEM_PYTHON = "/usr/bin/python3"  # has python3-gi; the app's own Python doesn't
RC_XML = Path.home() / ".config" / "labwc" / "rc.xml"  # in the RAM root: a rule left by a crash goes at the next start
RULE = re.compile(r"<!-- mirrordash-video -->.*?<!-- /mirrordash-video -->", re.S)
MODE = re.compile(r"^\s+(\d+)x(\d+) px, ([\d.]+) Hz(.*)$", re.M)
ASK_TIMEOUT = 5  # seconds the page has to say where the video window is

# ponytail: one video at a time (one window rule, one refresh rate); a second waits for the first to end.
# Upgrade path: a rule per player, with its own app id.
_one_at_a_time = asyncio.Lock()
_asked: dict[str, asyncio.Future] = {}


def with_rule(rc_xml: str, rect: list[int] | None) -> str:
    """rc.xml with the video window's rule at rect (x, y, width, height), or without it for None."""
    rc_xml = RULE.sub("", rc_xml)
    if rect is None:
        return rc_xml
    x, y, width, height = rect
    rule = ('<!-- mirrordash-video --><windowRules><windowRule identifier="mirrordash-video" serverDecoration="no" '
            f'ignoreFocusRequest="yes"><action name="MoveTo" x="{x}" y="{y}" /><action name="ResizeTo" '
            f'width="{width}" height="{height}" /></windowRule></windowRules><!-- /mirrordash-video -->')
    return rc_xml.replace("</labwc_config>", rule + "</labwc_config>")


def refresh_switch(wlr_randr: str, hz: int) -> tuple[str, str, str] | None:
    """(output, the mode at hz, the mode now) from wlr-randr's listing, or None when the screen is
    already at hz or has no mode at hz in its current resolution."""
    output = wlr_randr.split(maxsplit=1)[0] if wlr_randr.strip() else None
    modes = [(w, h, float(rate), rest) for w, h, rate, rest in MODE.findall(wlr_randr)]
    current = next(((w, h, rate) for w, h, rate, rest in modes if "current" in rest), None)
    if not output or not current or round(current[2]) == hz:
        return None
    w, h, rate = current
    if not any((mw, mh) == (w, h) and round(mrate) == hz for mw, mh, mrate, _ in modes):
        return None
    return output, f"{w}x{h}@{hz}Hz", f"{w}x{h}@{rate:g}Hz"


def window_reported(module: object, rect: object) -> None:
    """The mirror's own page answers where a module's video window is (features/kiosk/routes.py)."""
    future = _asked.get(module) if isinstance(module, str) else None
    if future is None or future.done():
        return
    valid = (isinstance(rect, list) and len(rect) == 4 and all(isinstance(n, int) for n in rect)
             and 0 <= rect[0] < 20000 and 0 <= rect[1] < 20000 and 0 < rect[2] < 20000 and 0 < rect[3] < 20000)
    future.set_result(rect if valid else None)


async def _ask_window(instance_id: str) -> list[int] | None:
    future = _asked[instance_id] = asyncio.get_running_loop().create_future()
    try:
        await manager.broadcast({"type": "video_window", "module": instance_id})
        return await asyncio.wait_for(future, ASK_TIMEOUT)
    except TimeoutError:
        return None
    finally:
        _asked.pop(instance_id, None)


async def _run(*command: str) -> str | None:
    """A command's output, or None when it fails or doesn't exist (not on a mirror)."""
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    out, _ = await process.communicate()
    return out.decode(errors="replace") if process.returncode == 0 else None


async def _place(rect: list[int] | None) -> None:
    if RC_XML.is_file():
        RC_XML.write_text(with_rule(RC_XML.read_text(), rect))
        await _run("pkill", "-HUP", "-x", "labwc")  # labwc rereads rc.xml


async def play_video(instance_id: str, uri: str, *, fullscreen: bool = False, hz: int | None = None) -> int:
    """Play uri until it ends (or the call is cancelled, which stops it); returns the player's exit code
    (0 at the end, 1 on an error, which is logged)."""
    if not isinstance(uri, str) or not uri or any(c.isspace() for c in uri):
        raise ValueError(f"play_video: not an address: {uri!r}")
    async with _one_at_a_time:
        rect = None
        if not fullscreen:
            rect = await _ask_window(instance_id)
            if rect is None:
                logger.warning(f"{instance_id}: no element with data-video-window on its card; the video plays in the middle")
        switch = refresh_switch(await _run("wlr-randr") or "", hz) if hz else None
        process = None
        try:
            await _place(rect)
            if switch:
                await _run("wlr-randr", "--output", switch[0], "--mode", switch[1])
            await manager.set_video_playing(instance_id, True)
            process = await asyncio.create_subprocess_exec(
                SYSTEM_PYTHON, "-I", str(PLAY), uri, *(["--fullscreen"] if fullscreen else []),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await process.communicate()
            if process.returncode:
                logger.warning(f"{instance_id}: the video stopped: {err.decode(errors='replace').strip()[-500:]}")
            return process.returncode
        finally:
            if process and process.returncode is None:  # cancelled: stop the player
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
            await manager.set_video_playing(instance_id, False)
            if switch:
                await _run("wlr-randr", "--output", switch[0], "--mode", switch[2])
            await _place(None)
