# Licensed under the PolyForm Noncommercial License 1.0.0.
"""A module's video window (ARCHITECTURE #34): `async with self.video_window() as window:` places the
module's own player over the element with `data-video-window` on its card, or lets it play fullscreen.

The core does what a module may not: it writes a labwc window rule for the player's app id into rc.xml
at the element's place on the page, switches the screen's refresh rate with hz= (50 for European TV,
which otherwise judders at 60 Hz), and holds video_is_playing so the other modules update once a minute.
The player itself, and what it plays, belong to the module.
"""
import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from mirrordash_core.config import write_atomic
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.video")

APP_ID = "mirrordash-video"
RC_XML = Path.home() / ".config" / "labwc" / "rc.xml"  # in the RAM root: a rule left by a crash goes at the next start
RULE = re.compile(r"<!-- mirrordash-video -->.*?<!-- /mirrordash-video -->", re.S)
# One of wlr-randr's mode lines: "    1920x1080 px, 60.000000 Hz (preferred, current)"
MODE = re.compile(r"^\s+(\d+)x(\d+) px, ([\d.]+) Hz(.*)$", re.M)
ASK_TIMEOUT = 5  # seconds the page has to say where the video window is

# ponytail: one video at a time (one window rule, one refresh rate); a second waits for the first to end.
# Upgrade path: a rule per player, with its own app id.
_one_at_a_time = asyncio.Lock()
_asked: dict[str, asyncio.Future] = {}  # instance id -> the page's answer of where its video window is


def with_rule(rc_xml: str, rect: list[int] | None) -> str:
    """rc.xml with the video window's rule at rect (x, y, width, height), or without it for None."""
    rc_xml = RULE.sub("", rc_xml)
    if rect is None:
        return rc_xml
    x, y, width, height = rect
    rule = (f'<!-- mirrordash-video --><windowRules><windowRule identifier="{APP_ID}" serverDecoration="no" '
            f'ignoreFocusRequest="yes"><action name="MoveTo" x="{x}" y="{y}" /><action name="ResizeTo" '
            f'width="{width}" height="{height}" /></windowRule></windowRules><!-- /mirrordash-video -->')
    return rc_xml.replace("</labwc_config>", rule + "</labwc_config>")


def refresh_switch(wlr_randr: str, hz: int) -> tuple[str, str, str] | None:
    """(output, the mode at hz, the mode now) from wlr-randr's listing, or None when the screen is
    already at hz or has no mode at hz in its current resolution."""
    output = wlr_randr.split(maxsplit=1)[0] if wlr_randr.strip() else None
    modes = [(width, height, float(rate), notes) for width, height, rate, notes in MODE.findall(wlr_randr)]
    current = next(((width, height, rate) for width, height, rate, notes in modes if "current" in notes), None)
    if not output or not current or round(current[2]) == hz:
        return None
    width, height, rate = current
    if not any((mode_width, mode_height) == (width, height) and round(mode_rate) == hz
               for mode_width, mode_height, mode_rate, _ in modes):
        return None
    return output, f"{width}x{height}@{hz}Hz", f"{width}x{height}@{rate:g}Hz"


def window_reported(module: object, rect: object) -> None:
    """The mirror's own page answers where a module's video window is (features/kiosk/routes.py)."""
    future = _asked.get(module) if isinstance(module, str) else None
    if future is None or future.done():
        return
    # Trust boundary: the page's answer goes into rc.xml. Whole pixels, on a plausible screen
    valid = (isinstance(rect, list) and len(rect) == 4 and all(isinstance(number, int) for number in rect)
             and 0 <= rect[0] < 20000 and 0 <= rect[1] < 20000 and 0 < rect[2] < 20000 and 0 < rect[3] < 20000)
    future.set_result(rect if valid else None)


async def _ask_window(instance_id: str) -> list[int] | None:
    """Ask the mirror's page where the instance's data-video-window element is; None if it doesn't say."""
    future = _asked[instance_id] = asyncio.get_running_loop().create_future()
    try:
        await manager.broadcast({"type": "video_window", "module": instance_id})
        return await asyncio.wait_for(future, ASK_TIMEOUT)
    except TimeoutError:
        return None
    finally:
        _asked.pop(instance_id, None)


async def _run(*command: str) -> str | None:
    """A command's output, or None when it fails, hangs or doesn't exist (not on a mirror). It runs while
    the one-video lock is held, so it gets a timeout: a hung wlr-randr must not stop every video for good."""
    exit_code, output, _ = await run_command(*command, timeout=10)
    return output if exit_code == 0 else None


async def _place(rect: list[int] | None) -> None:
    """Put the window rule at rect into labwc's rc.xml (None takes it out), and have labwc read it."""
    if RC_XML.is_file():
        write_atomic(RC_XML, with_rule(RC_XML.read_text(), rect))  # labwc never reads half a file
        await _run("pkill", "-HUP", "-x", "labwc")


@dataclass(frozen=True)
class VideoWindow:
    app_id: str               # the player's window must have this app id (GLib.set_prgname in a GStreamer player)
    rect: list[int] | None    # where it goes in screen pixels (x, y, width, height); None: fullscreen or unknown
    fullscreen: bool


@contextlib.asynccontextmanager
async def video_window(instance_id: str, *, fullscreen: bool = False, hz: int | None = None):
    """While the block runs, a window with app id `mirrordash-video` lands over the card's data-video-window
    element (or the module plays fullscreen), the screen runs at hz if it can, and the other modules update
    once a minute. The module starts and stops its own player inside the block."""
    async with _one_at_a_time:
        rect = None
        if not fullscreen:
            rect = await _ask_window(instance_id)
            if rect is None:
                logger.warning(f"{instance_id}: no element with data-video-window on its card; the video plays in the middle")
        switch = refresh_switch(await _run("wlr-randr") or "", hz) if hz else None
        try:
            await _place(rect)
            if switch:
                await _run("wlr-randr", "--output", switch[0], "--mode", switch[1])
            await manager.set_video_playing(instance_id, True)
            yield VideoWindow(APP_ID, rect, fullscreen)
        finally:
            await manager.set_video_playing(instance_id, False)
            if switch:
                await _run("wlr-randr", "--output", switch[0], "--mode", switch[2])
            await _place(None)
