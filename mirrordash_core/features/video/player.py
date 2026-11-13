# SPDX-License-Identifier: AGPL-3.0-only
"""One run of the video player (play.py) under the core's eye (ARCHITECTURE #34).

Player starts the process, reads its status lines, tells when the first picture shows and how it ended, and
stops it: on request (stop), or because it stopped making progress. A player that hangs leaves its last
frame on top of the screen with nothing to remove it, so the watchdog (verdict) is not optional."""
import asyncio
import contextlib
import logging
import os
import signal
import time

logger = logging.getLogger("mirrordash.core.video")

SILENCE_LIMIT = 10  # seconds without a line: the process hangs (play.py ticks every 2 s)
START_LIMIT = 30    # seconds to the first picture (a live stream starts in 1-2 s, MEASUREMENTS.md)
STALL_LIMIT = 15    # seconds the position stands still once the picture shows
POLL = 1          # seconds between the watchdog's looks when no line comes
KILL_AFTER = 2      # seconds a stopped player gets to leave by itself
ENV = ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "PATH", "LANG")  # nothing of the core's secrets goes along


def verdict(now: float, started: float, last_line: float, picture_at: float | None, moved_at: float,
            limits: tuple[float, float, float] = (SILENCE_LIMIT, START_LIMIT, STALL_LIMIT)) -> str | None:
    """Why the player has to be killed now, or None. Times are time.monotonic() values: when it started, when
    its last line came, when the first picture showed (None: not yet) and when its position last moved.
    ponytail: a live stream's position can stand still for a moment while it buffers; STALL_LIMIT is
    long enough for the segments of a bad hour and short enough that a dead stream doesn't stay on the screen.
    Upgrade: ask the sink for its rendered frame count instead of the position."""
    silence, start, stall = limits
    if now - last_line > silence:
        return "the player stopped answering"
    if picture_at is None and now - started > start:
        return f"no picture after {start:g} s"
    if picture_at is not None and now - moved_at > stall:
        return "the stream stalled"
    return None


class Player:
    def __init__(self, command: list[str], limits: tuple[float, float, float] = (SILENCE_LIMIT, START_LIMIT, STALL_LIMIT)):
        self.command, self.limits = command, limits
        self._process: asyncio.subprocess.Process | None = None
        self._stopped: tuple[str, bool] | None = None  # why it is stopped, and whether that is a failure

    async def run(self, on_picture) -> tuple[bool, str]:
        """Play until it ends: (failed, reason). await on_picture() is called when the first picture shows. A stop before the process exists, or a start that fails, ends it at once."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command, stdout=asyncio.subprocess.PIPE, env={k: os.environ[k] for k in ENV if k in os.environ})
        except OSError as error:
            return True, f"the player didn't start: {error.strerror}"
        process = self._process
        started = last_line = moved_at = time.monotonic()
        picture_at, position, error_text = None, None, None
        try:
            while self._stopped is None:
                try:
                    line = (await asyncio.wait_for(process.stdout.readline(), POLL)).decode(errors="replace").strip()
                except TimeoutError:
                    line = None
                else:
                    if not line and process.stdout.at_eof():
                        break
                now = time.monotonic()
                if line:
                    last_line = now
                    word, _, rest = line.partition(" ")
                    if word == "playing" and picture_at is None:
                        picture_at = moved_at = now
                        await on_picture()
                    elif word == "tick" and rest != position:
                        position, moved_at = rest, now
                    elif word == "error":
                        error_text = rest
                elif why := verdict(now, started, last_line, picture_at, moved_at, self.limits):
                    self._stopped = (why, True)
        finally:
            await self._end()
        if self._stopped:
            return self._stopped[1], self._stopped[0]
        code = process.returncode
        if code == 0:
            return False, "end of stream"
        return True, error_text or f"the player ended with code {code}"

    def stop(self, reason: str, failed: bool = False) -> None:
        """End it: SIGTERM now, SIGKILL if it hasn't left by then. run() answers with the reason."""
        self._stopped = self._stopped or (reason, failed)

    async def _end(self) -> None:
        process = self._process
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), KILL_AFTER)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()  # a stopped (SIGSTOP) or hung player never sees the SIGTERM
                await process.wait()

